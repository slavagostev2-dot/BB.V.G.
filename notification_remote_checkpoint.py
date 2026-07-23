from __future__ import annotations

import base64
import json
import os
import time
from typing import Any

import requests

_API_ROOT = "https://api.github.com"
_STATE_PATH = "notification_delivery_state.json"
_TIME_OUT = 12
_dispatched_wheel_events: set[str] = set()


def _configured() -> bool:
    return bool(
        os.getenv("GITHUB_TOKEN", "").strip()
        and os.getenv("GITHUB_REPOSITORY", "").strip()
    )


def _headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {os.environ['GITHUB_TOKEN'].strip()}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "BB-VG-notification-checkpoint",
    }


def _endpoint() -> str:
    return f"{_API_ROOT}/repos/{os.environ['GITHUB_REPOSITORY'].strip()}/contents/{_STATE_PATH}"


def _branch() -> str:
    return (
        os.getenv("GITHUB_BRANCH", "").strip()
        or os.getenv("GITHUB_REF_NAME", "").strip()
        or "main"
    )


def _remote_state() -> tuple[dict[str, Any], str]:
    response = requests.get(
        _endpoint(),
        headers=_headers(),
        params={"ref": _branch()},
        timeout=_TIME_OUT,
    )
    if response.status_code == 404:
        return {}, ""
    response.raise_for_status()
    payload = response.json()
    raw = base64.b64decode(str(payload.get("content") or "")).decode("utf-8")
    value = json.loads(raw)
    return (value if isinstance(value, dict) else {}), str(payload.get("sha") or "")


def _encoded_state(value: dict[str, Any]) -> str:
    return base64.b64encode(
        (
            json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True)
            + "\n"
        ).encode("utf-8")
    ).decode("ascii")


def _put_remote_state(value: dict[str, Any], sha: str, message: str) -> requests.Response:
    body: dict[str, Any] = {
        "message": message,
        "content": _encoded_state(value),
        "branch": _branch(),
    }
    if sha:
        body["sha"] = sha
    return requests.put(
        _endpoint(),
        headers=_headers(),
        json=body,
        timeout=_TIME_OUT,
    )


def _normalized_remote_state(integrity_module: Any, value: dict[str, Any]) -> dict[str, Any]:
    return integrity_module.merge_states(integrity_module.default_state(), value)


def checkpoint(integrity_module: Any) -> bool:
    """Merge the local delivery ledger into main after external delivery.

    This closes the crash window where Telegram already received a message but a
    cancelled GitHub runner had not yet pushed notification_delivery_state.json.
    Claims use a stricter compare-and-swap path in ``claim_checkpoint`` so two
    concurrent runners cannot both reserve the same delivery.
    """

    if not _configured():
        return True

    for attempt in range(1, 4):
        try:
            local = integrity_module.load_state()
            remote, sha = _remote_state()
            merged = integrity_module.merge_states(remote, local)
            response = _put_remote_state(
                merged,
                sha,
                "Checkpoint BB V.G. notification delivery [skip ci]",
            )
            if response.status_code in {409, 422} and attempt < 3:
                time.sleep(0.4 * attempt)
                continue
            response.raise_for_status()
            integrity_module.save_state(merged)
            return True
        except Exception as exc:
            if attempt >= 3:
                print(
                    "WARNING remote notification checkpoint failed: "
                    f"{type(exc).__name__}: {exc}"
                )
                return False
            time.sleep(0.4 * attempt)
    return False


def claim_checkpoint(integrity_module: Any, key: str) -> bool:
    """Atomically publish one local claim before Telegram is called.

    GitHub's contents SHA is the compare-and-swap token. Only the runner whose
    PUT succeeds may continue. A runner that loses a 409 race reloads the file,
    observes the winner's live claim and returns ``False`` without sending.
    """

    if not _configured():
        return True

    local = integrity_module.load_state()
    local_claims = local.get("claims") if isinstance(local.get("claims"), dict) else {}
    local_claim = str(local_claims.get(key) or "")
    if not local_claim:
        return False

    for attempt in range(1, 4):
        try:
            remote_raw, sha = _remote_state()
            remote = _normalized_remote_state(integrity_module, remote_raw)
            entries = remote.get("entries") if isinstance(remote.get("entries"), dict) else {}
            claims = remote.get("claims") if isinstance(remote.get("claims"), dict) else {}
            if key in entries or key in claims:
                return False

            merged = integrity_module.merge_states(remote, local)
            merged.setdefault("claims", {})[key] = local_claim
            response = _put_remote_state(
                merged,
                sha,
                "Claim BB V.G. notification delivery [skip ci]",
            )
            if response.status_code in {409, 422}:
                if attempt < 3:
                    time.sleep(0.4 * attempt)
                    continue
                return False
            response.raise_for_status()
            integrity_module.save_state(merged)
            return True
        except Exception as exc:
            if attempt >= 3:
                print(
                    "WARNING remote notification claim failed: "
                    f"{type(exc).__name__}: {exc}"
                )
                return False
            time.sleep(0.4 * attempt)
    return False


def release_checkpoint(
    integrity_module: Any,
    key: str,
    expected_claim: str,
) -> bool:
    """Remove only the same remote lease that this process acquired."""

    if not _configured() or not expected_claim:
        return True

    for attempt in range(1, 4):
        try:
            remote_raw, sha = _remote_state()
            remote = _normalized_remote_state(integrity_module, remote_raw)
            claims = remote.get("claims") if isinstance(remote.get("claims"), dict) else {}
            current_claim = str(claims.get(key) or "")
            if not current_claim or current_claim != expected_claim:
                return True

            claims.pop(key, None)
            response = _put_remote_state(
                remote,
                sha,
                "Release BB V.G. notification claim [skip ci]",
            )
            if response.status_code in {409, 422} and attempt < 3:
                time.sleep(0.4 * attempt)
                continue
            response.raise_for_status()
            integrity_module.save_state(remote)
            return True
        except Exception as exc:
            if attempt >= 3:
                print(
                    "WARNING remote notification claim release failed: "
                    f"{type(exc).__name__}: {exc}"
                )
                return False
            time.sleep(0.4 * attempt)
    return False


def delivery_reservation_status(integrity_module: Any, key: str) -> str:
    """Return ``completed``, ``claimed`` or ``available`` for one delivery key."""

    try:
        local = integrity_module.load_state()
        entries = local.get("entries") if isinstance(local.get("entries"), dict) else {}
        claims = local.get("claims") if isinstance(local.get("claims"), dict) else {}
        if key in entries:
            return "completed"
        if key in claims:
            return "claimed"
        if not _configured():
            return "available"
        remote_raw, _sha = _remote_state()
        remote = _normalized_remote_state(integrity_module, remote_raw)
        entries = remote.get("entries") if isinstance(remote.get("entries"), dict) else {}
        claims = remote.get("claims") if isinstance(remote.get("claims"), dict) else {}
        if key in entries:
            return "completed"
        if key in claims:
            return "claimed"
        return "available"
    except Exception as exc:
        print(
            "WARNING remote notification status failed: "
            f"{type(exc).__name__}: {exc}"
        )
        return "unknown"


def _needs_auto_recovery(event_identity: str) -> bool:
    identity = str(event_identity or "")
    return identity.startswith("wheel:") and any(
        marker in identity for marker in (":detected", ":active", ":available")
    )


def _dispatch_auto_participation_recovery(event_identity: str) -> bool:
    if not _configured() or not event_identity:
        return False
    if event_identity in _dispatched_wheel_events:
        return True
    endpoint = (
        f"{_API_ROOT}/repos/{os.environ['GITHUB_REPOSITORY'].strip()}"
        "/actions/workflows/auto-participation.yml/dispatches"
    )
    try:
        response = requests.post(
            endpoint,
            headers=_headers(),
            json={"ref": _branch()},
            timeout=_TIME_OUT,
        )
        response.raise_for_status()
        _dispatched_wheel_events.add(event_identity)
        print(f"Auto participation recovery dispatched for {event_identity}")
        return True
    except Exception as exc:
        print(
            "WARNING auto participation recovery dispatch failed: "
            f"{type(exc).__name__}: {exc}"
        )
        return False


def install(router_module: Any, integrity_module: Any) -> None:
    if getattr(router_module, "_bbvg_remote_notification_checkpoint_installed", False):
        return

    original_claim = router_module.claim_delivery
    original_complete = router_module.complete_delivery
    original_release = router_module.release_delivery
    original_event_identity = router_module.notification_event_identity

    def durable_event_identity(
        kind: str,
        text: str,
        url: str | None,
        reply_markup: dict | None,
    ) -> str:
        identity = original_event_identity(kind, text, url, reply_markup)
        if _needs_auto_recovery(identity):
            _dispatch_auto_participation_recovery(identity)
        return identity

    def durable_claim(key: str) -> bool:
        claimed = bool(original_claim(key))
        if not claimed:
            return False
        if not claim_checkpoint(integrity_module, key):
            original_release(key)
            return False
        return True

    def durable_complete(key: str) -> None:
        original_complete(key)
        checkpoint(integrity_module)

    def durable_release(key: str) -> None:
        state = integrity_module.load_state()
        claims = state.get("claims") if isinstance(state.get("claims"), dict) else {}
        expected_claim = str(claims.get(key) or "")
        original_release(key)
        release_checkpoint(integrity_module, key, expected_claim)

    router_module.notification_event_identity = durable_event_identity
    router_module.claim_delivery = durable_claim
    router_module.complete_delivery = durable_complete
    router_module.release_delivery = durable_release
    router_module.delivery_reservation_status = lambda key: delivery_reservation_status(
        integrity_module, key
    )
    router_module._bbvg_remote_notification_checkpoint_installed = True


def self_test() -> None:
    assert _needs_auto_recovery("wheel:wheels:test:detected")
    assert _needs_auto_recovery("wheel:wheel_new:test:active:event")
    assert _needs_auto_recovery("wheel:wheels:test:available")
    assert not _needs_auto_recovery("wheel:wheels:test:reminder")

    key = "a" * 64

    class FakeResponse:
        def __init__(self, status_code: int) -> None:
            self.status_code = status_code

        def raise_for_status(self) -> None:
            if self.status_code >= 400:
                raise RuntimeError(f"HTTP {self.status_code}")

    class FakeIntegrity:
        local = {
            "format": "test",
            "version": 1,
            "entries": {},
            "claims": {key: "2026-07-23T15:55:00+00:00"},
            "messages": {},
        }

        @classmethod
        def default_state(cls) -> dict[str, Any]:
            return {
                "format": "test",
                "version": 1,
                "entries": {},
                "claims": {},
                "messages": {},
            }

        @classmethod
        def load_state(cls) -> dict[str, Any]:
            return json.loads(json.dumps(cls.local))

        @classmethod
        def save_state(cls, value: dict[str, Any]) -> dict[str, Any]:
            cls.local = json.loads(json.dumps(value))
            return cls.local

        @classmethod
        def merge_states(
            cls,
            left: dict[str, Any],
            right: dict[str, Any],
        ) -> dict[str, Any]:
            result = cls.default_state()
            for value in (left, right):
                for name in ("entries", "claims", "messages"):
                    raw = value.get(name) if isinstance(value, dict) else None
                    if isinstance(raw, dict):
                        result[name].update(raw)
            for digest in result["entries"]:
                result["claims"].pop(digest, None)
            return result

    original_configured = globals()["_configured"]
    original_remote_state = globals()["_remote_state"]
    original_put_remote_state = globals()["_put_remote_state"]
    try:
        globals()["_configured"] = lambda: True

        remote = FakeIntegrity.default_state()
        remote_sha = "0"

        def winner_remote_state() -> tuple[dict[str, Any], str]:
            return json.loads(json.dumps(remote)), remote_sha

        def winner_put(
            value: dict[str, Any],
            sha: str,
            _message: str,
        ) -> FakeResponse:
            nonlocal remote, remote_sha
            assert sha == remote_sha
            remote = json.loads(json.dumps(value))
            remote_sha = str(int(remote_sha) + 1)
            return FakeResponse(200)

        globals()["_remote_state"] = winner_remote_state
        globals()["_put_remote_state"] = winner_put
        assert claim_checkpoint(FakeIntegrity, key) is True
        assert remote["claims"][key] == "2026-07-23T15:55:00+00:00"
        assert delivery_reservation_status(FakeIntegrity, key) == "claimed"
        assert release_checkpoint(
            FakeIntegrity, key, "2026-07-23T15:55:00+00:00"
        ) is True
        assert key not in remote["claims"]
        assert delivery_reservation_status(FakeIntegrity, key) == "available"

        FakeIntegrity.local["claims"] = {key: "2026-07-23T15:55:01+00:00"}
        remote = FakeIntegrity.default_state()
        remote_sha = "0"
        put_attempts = 0

        def losing_put(
            _value: dict[str, Any],
            _sha: str,
            _message: str,
        ) -> FakeResponse:
            nonlocal remote, remote_sha, put_attempts
            put_attempts += 1
            remote["claims"][key] = "2026-07-23T15:55:00+00:00"
            remote_sha = "1"
            return FakeResponse(409)

        globals()["_put_remote_state"] = losing_put
        assert claim_checkpoint(FakeIntegrity, key) is False
        assert put_attempts == 1
    finally:
        globals()["_configured"] = original_configured
        globals()["_remote_state"] = original_remote_state
        globals()["_put_remote_state"] = original_put_remote_state

    print("notification remote checkpoint self-test passed")


if __name__ == "__main__":
    self_test()
