from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
import re
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

VK_API_ENDPOINT = "https://api.vk.com/method/messages.send"
VK_API_VERSION = os.getenv("VK_API_VERSION", "5.199").strip() or "5.199"
VK_WORKFLOW_FILE = "vk-wheel-notification.yml"
VK_REQUEST_TIMEOUT_SECONDS = max(
    3.0, float(os.getenv("VK_REQUEST_TIMEOUT_SECONDS", "15") or 15)
)
_TAG_RE = re.compile(r"<[^>]+>")
_SPLIT_PEERS_RE = re.compile(r"[\s,;]+")


def _plain_text(value: str) -> str:
    text = re.sub(r"(?i)<br\s*/?>", "\n", str(value or ""))
    text = _TAG_RE.sub("", text)
    return html.unescape(text).strip()


def _wheel_url(
    router_module: Any,
    text: str,
    url: str | None,
    reply_markup: dict | None,
) -> str:
    candidates: list[str] = []
    if url:
        candidates.append(str(url))
    candidates.append(str(text or ""))
    if isinstance(reply_markup, dict):
        for row in reply_markup.get("inline_keyboard", []):
            if not isinstance(row, list):
                continue
            for button in row:
                if isinstance(button, dict) and button.get("url"):
                    candidates.append(str(button.get("url")))
    for candidate in candidates:
        match = router_module.WHEEL_URL_RE.search(candidate)
        if match:
            return f"https://betboom.ru/freestream/{match.group(1)}"
    return str(url or "").strip()


def _vk_message(text: str, wheel_url: str) -> str:
    message = _plain_text(text)
    if wheel_url and wheel_url not in message:
        message = f"{message}\n\n{wheel_url}" if message else wheel_url
    return message


def _is_wheel_notification(
    router_module: Any,
    text: str,
    url: str | None,
    reply_markup: dict | None,
) -> tuple[bool, str]:
    kind = str(router_module.notification_kind(text) or "")
    if kind != "wheels":
        return False, ""
    lowered = _plain_text(text).casefold()
    if "активные колёса" in lowered and "betboom" not in lowered:
        return False, ""
    identity = str(
        router_module.notification_event_identity(kind, text, url, reply_markup) or ""
    )
    return bool(identity.startswith("wheel:wheels:")), identity


def _github_dispatch(
    *,
    message: str,
    wheel_url: str,
    event_identity: str,
) -> bool:
    token = str(os.getenv("GITHUB_TOKEN") or "").strip()
    repository = str(os.getenv("GITHUB_REPOSITORY") or "").strip()
    branch = str(os.getenv("GITHUB_BRANCH") or "main").strip() or "main"
    if not token or not repository:
        print("VK wheel notification dispatch skipped: GitHub runtime credentials are unavailable")
        return False

    endpoint = (
        f"https://api.github.com/repos/{repository}/actions/workflows/"
        f"{VK_WORKFLOW_FILE}/dispatches"
    )
    body = json.dumps(
        {
            "ref": branch,
            "inputs": {
                "message": message[:12000],
                "url": wheel_url[:2000],
                "event_identity": event_identity[:500],
            },
        },
        ensure_ascii=False,
    ).encode("utf-8")
    request = Request(
        endpoint,
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "Content-Type": "application/json; charset=utf-8",
            "User-Agent": "BB-VG-VK-Wheel-Notifications",
        },
    )
    try:
        with urlopen(request, timeout=VK_REQUEST_TIMEOUT_SECONDS) as response:
            status = int(getattr(response, "status", response.getcode()))
    except (HTTPError, URLError, OSError) as exc:
        raise RuntimeError(f"GitHub workflow dispatch failed: {type(exc).__name__}: {exc}") from exc
    if status != 204:
        raise RuntimeError(f"GitHub workflow dispatch returned HTTP {status}")
    return True


def dispatch_vk_wheel_notification(
    router_module: Any,
    text: str,
    url: str | None = None,
    reply_markup: dict | None = None,
    *,
    dispatcher: Callable[..., bool] = _github_dispatch,
) -> dict[str, Any]:
    eligible, event_identity = _is_wheel_notification(
        router_module, text, url, reply_markup
    )
    if not eligible:
        return {"eligible": False, "dispatched": False}

    wheel_url = _wheel_url(router_module, text, url, reply_markup)
    message = _vk_message(text, wheel_url)
    dedup_key = router_module.delivery_key(
        "vk:wheel-notifications",
        "wheels",
        event_identity or message,
        wheel_url,
    )
    if not router_module.claim_delivery(dedup_key):
        return {
            "eligible": True,
            "dispatched": False,
            "duplicate": True,
            "event_identity": event_identity,
        }

    try:
        dispatched = bool(
            dispatcher(
                message=message,
                wheel_url=wheel_url,
                event_identity=event_identity,
            )
        )
    except Exception:
        router_module.release_delivery(dedup_key)
        raise
    if not dispatched:
        router_module.release_delivery(dedup_key)
        return {
            "eligible": True,
            "dispatched": False,
            "event_identity": event_identity,
        }

    router_module.complete_delivery(dedup_key)
    return {
        "eligible": True,
        "dispatched": True,
        "event_identity": event_identity,
    }


def install(monitor_module: Any, router_module: Any) -> None:
    if getattr(monitor_module, "_bbvg_vk_wheel_notifications_installed", False):
        return
    original_send = monitor_module.send_message

    def send_message_with_vk(
        text: str,
        url: str | None = None,
        reply_markup: dict | None = None,
    ) -> dict:
        telegram_error: Exception | None = None
        telegram_result: dict[str, Any] | None = None
        try:
            telegram_result = original_send(text, url=url, reply_markup=reply_markup)
        except Exception as exc:
            telegram_error = exc

        try:
            dispatch_vk_wheel_notification(
                router_module,
                text,
                url=url,
                reply_markup=reply_markup,
            )
        except Exception as exc:
            # VK is an optional second delivery channel. Its failure must never
            # turn a successful Telegram notification into a failed wheel event.
            print(f"WARNING VK wheel notification: {type(exc).__name__}: {exc}")

        if telegram_error is not None:
            raise telegram_error
        return telegram_result or {"ok": True, "result": {"sent": 0}}

    monitor_module.send_message = send_message_with_vk
    monitor_module._bbvg_vk_wheel_notifications_installed = True


def configured_peer_ids(raw: str | None = None) -> list[str]:
    value = str(raw if raw is not None else os.getenv("VK_WHEEL_PEER_IDS", "")).strip()
    result: list[str] = []
    for item in _SPLIT_PEERS_RE.split(value):
        if not item:
            continue
        try:
            normalized = str(int(item))
        except ValueError:
            continue
        if normalized not in result:
            result.append(normalized)
    return result


def vk_random_id(event_identity: str, peer_id: str) -> int:
    digest = hashlib.sha256(
        f"{event_identity}\x1f{peer_id}".encode("utf-8")
    ).digest()
    value = int.from_bytes(digest[:4], "big") & 0x7FFFFFFF
    return value or 1


def send_vk_message(
    *,
    token: str,
    peer_id: str,
    message: str,
    event_identity: str,
    api_version: str = VK_API_VERSION,
) -> dict[str, Any]:
    payload = urlencode(
        {
            "access_token": token,
            "v": api_version,
            "peer_id": peer_id,
            "random_id": str(vk_random_id(event_identity, peer_id)),
            "message": message,
        }
    ).encode("utf-8")
    request = Request(
        VK_API_ENDPOINT,
        data=payload,
        method="POST",
        headers={
            "Content-Type": "application/x-www-form-urlencoded; charset=utf-8",
            "User-Agent": "BB-VG-VK-Wheel-Notifications",
        },
    )
    try:
        with urlopen(request, timeout=VK_REQUEST_TIMEOUT_SECONDS) as response:
            raw = response.read().decode("utf-8")
    except (HTTPError, URLError, OSError) as exc:
        raise RuntimeError(f"VK API transport failed: {type(exc).__name__}: {exc}") from exc
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError("VK API returned invalid JSON") from exc
    if not isinstance(data, dict):
        raise RuntimeError("VK API returned an unexpected response")
    error = data.get("error")
    if isinstance(error, dict):
        code = error.get("error_code")
        message_text = str(error.get("error_msg") or "unknown VK API error")
        raise RuntimeError(f"VK API error {code}: {message_text}")
    return data


def send_from_environment() -> int:
    token = str(os.getenv("VK_GROUP_TOKEN") or "").strip()
    peers = configured_peer_ids()
    message = str(os.getenv("VK_WHEEL_MESSAGE") or "").strip()
    wheel_url = str(os.getenv("VK_WHEEL_URL") or "").strip()
    event_identity = str(os.getenv("VK_WHEEL_EVENT_ID") or "").strip()

    if not token or not peers:
        print("VK wheel notifications are not configured; skipping delivery")
        return 0
    if not message:
        raise SystemExit("VK_WHEEL_MESSAGE is required")
    if wheel_url and wheel_url not in message:
        message = f"{message}\n\n{wheel_url}"
    if not event_identity:
        event_identity = hashlib.sha256(message.encode("utf-8")).hexdigest()

    failures: list[str] = []
    sent = 0
    for peer_id in peers:
        try:
            send_vk_message(
                token=token,
                peer_id=peer_id,
                message=message,
                event_identity=event_identity,
            )
            sent += 1
        except Exception as exc:
            failures.append(f"{peer_id}:{type(exc).__name__}")
            print(f"WARNING VK target {peer_id}: {type(exc).__name__}: {exc}")
    print(f"VK wheel notification delivery: sent={sent}, failed={len(failures)}")
    if failures:
        raise SystemExit("VK delivery failed for: " + ", ".join(failures))
    return 0


def self_test() -> None:
    assert _plain_text("🎡 <b>Новое колесо</b>") == "🎡 Новое колесо"
    assert configured_peer_ids("1, 2;2 bad -3") == ["1", "2", "-3"]
    assert vk_random_id("event", "1") == vk_random_id("event", "1")
    assert vk_random_id("event", "1") != vk_random_id("event", "2")

    class FakeRouter:
        WHEEL_URL_RE = re.compile(
            r"(?:https?://)?(?:www\.)?betboom\.ru/freestream/([A-Za-z0-9._~-]+)",
            re.IGNORECASE,
        )
        claimed: set[str] = set()
        completed: set[str] = set()

        @staticmethod
        def notification_kind(text: str) -> str:
            return "wheel_final_reminders" if "Напоминание" in text else "wheels"

        @staticmethod
        def notification_event_identity(kind: str, text: str, url: str | None, reply_markup: dict | None) -> str:
            match = FakeRouter.WHEEL_URL_RE.search(str(url or text or ""))
            return f"wheel:{kind}:{match.group(1)}:detected" if match else ""

        @staticmethod
        def delivery_key(chat_id: str, kind: str, text: str, url: str | None) -> str:
            return hashlib.sha256(
                f"{chat_id}|{kind}|{text}|{url or ''}".encode("utf-8")
            ).hexdigest()

        @classmethod
        def claim_delivery(cls, key: str) -> bool:
            if key in cls.claimed or key in cls.completed:
                return False
            cls.claimed.add(key)
            return True

        @classmethod
        def release_delivery(cls, key: str) -> None:
            cls.claimed.discard(key)

        @classmethod
        def complete_delivery(cls, key: str) -> None:
            cls.claimed.discard(key)
            cls.completed.add(key)

    calls: list[dict[str, str]] = []

    def fake_dispatcher(**kwargs: str) -> bool:
        calls.append(dict(kwargs))
        return True

    first = dispatch_vk_wheel_notification(
        FakeRouter,
        "🎡 <b>Новое колесо BetBoom</b>",
        url="https://betboom.ru/freestream/test-wheel",
        dispatcher=fake_dispatcher,
    )
    second = dispatch_vk_wheel_notification(
        FakeRouter,
        "🎡 <b>Новое колесо BetBoom</b>",
        url="https://betboom.ru/freestream/test-wheel",
        dispatcher=fake_dispatcher,
    )
    reminder = dispatch_vk_wheel_notification(
        FakeRouter,
        "🚨 Напоминание о колесе BetBoom",
        url="https://betboom.ru/freestream/reminder-wheel",
        dispatcher=fake_dispatcher,
    )
    assert first["dispatched"] is True
    assert second.get("duplicate") is True
    assert reminder["eligible"] is False
    assert len(calls) == 1
    assert "<b>" not in calls[0]["message"]
    assert calls[0]["wheel_url"].endswith("/test-wheel")
    print("VK wheel notifications self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--send-env", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return 0
    if args.send_env:
        return send_from_environment()
    self_test()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
