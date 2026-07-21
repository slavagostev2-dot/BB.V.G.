from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
import re
import time
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
VK_SEND_ATTEMPTS = max(1, int(os.getenv("VK_SEND_ATTEMPTS", "3") or 3))
_TAG_RE = re.compile(r"<[^>]+>")
_SPLIT_PEERS_RE = re.compile(r"[\s,;]+")

# VK receives only the first user-facing wheel alert. These messages belong to
# later lifecycle phases or to service diagnostics and must stay in Telegram.
_BLOCKED_WHEEL_NOTIFICATION_MARKERS = (
    "активные колёса",
    "напоминание о колесе",
    "последний шанс",
    "время прокрутки",
    "уже должно быть прокручено",
    "ошибка",
    "сбой",
    "не смог проверить",
    "недоступ",
    "служебн",
    "диагност",
)


def configured_peer_ids(raw: str | None = None) -> list[str]:
    """Parse the legacy fixed-recipient setting kept for compatibility."""

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
    return ""


def _canonical_initial_identity(identity: str) -> str:
    parts = str(identity or "").split(":")
    if len(parts) >= 4 and parts[0] == "wheel" and parts[1] == "wheels":
        parts[3] = "detected"
        return ":".join(parts)
    return ""


def _wheel_event(
    router_module: Any,
    text: str,
    url: str | None,
    reply_markup: dict | None,
) -> tuple[bool, str]:
    """Recognize the first user-facing notification for a BetBoom wheel.

    Initial monitor wording may vary. Some verified wheels are announced without
    the exact phrase ``Новое колесо BetBoom``; CTOM05 was one such production
    event. The stable signal is the BetBoom wheel URL or button markup. Menus,
    reminders, draw alerts and explicit system failures remain excluded.
    """

    wheel_url = _wheel_url(router_module, text, url, reply_markup)
    if not wheel_url:
        return False, ""

    lowered = _plain_text(text).casefold()
    if any(marker in lowered for marker in _BLOCKED_WHEEL_NOTIFICATION_MARKERS):
        return False, ""

    # Do not depend on notification_kind(text): its marker list is deliberately
    # strict and previously classified the valid CTOM05 alert as admin_system.
    identity = str(
        router_module.notification_event_identity(
            "wheels", text, wheel_url, reply_markup
        )
        or ""
    )
    identity = _canonical_initial_identity(identity)
    if not identity.startswith("wheel:wheels:"):
        return False, ""
    return True, identity


def _vk_message(text: str, wheel_url: str) -> str:
    message = _plain_text(text)
    if wheel_url and wheel_url not in message:
        message = f"{message}\n\n{wheel_url}" if message else wheel_url
    return message


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
        raise RuntimeError(
            f"GitHub VK workflow dispatch failed: {type(exc).__name__}: {exc}"
        ) from exc
    if status != 204:
        raise RuntimeError(f"GitHub VK workflow dispatch returned HTTP {status}")
    return True


def dispatch_vk_wheel_notification(
    router_module: Any,
    text: str,
    url: str | None = None,
    reply_markup: dict | None = None,
    *,
    dispatcher: Callable[..., bool] = _github_dispatch,
) -> dict[str, Any]:
    eligible, event_identity = _wheel_event(router_module, text, url, reply_markup)
    if not eligible:
        return {"eligible": False, "dispatched": False}

    wheel_url = _wheel_url(router_module, text, url, reply_markup)
    message = _vk_message(text, wheel_url)
    dedup_key = router_module.delivery_key(
        "vk:wheel-notifications",
        "wheels",
        event_identity,
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
            vk_result = dispatch_vk_wheel_notification(
                router_module,
                text,
                url=url,
                reply_markup=reply_markup,
            )
            if vk_result.get("dispatched"):
                print(
                    "VK wheel notification workflow scheduled: "
                    f"{vk_result.get('event_identity', '')}"
                )
        except Exception as exc:
            print(f"WARNING VK wheel notification dispatch: {type(exc).__name__}: {exc}")

        if telegram_error is not None:
            raise telegram_error
        return telegram_result or {"ok": True, "result": {"sent": 0}}

    monitor_module.send_message = send_message_with_vk
    monitor_module._bbvg_vk_wheel_notifications_installed = True


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


def _send_with_retries(
    *,
    token: str,
    peer_id: str,
    message: str,
    event_identity: str,
    sender: Callable[..., dict[str, Any]] = send_vk_message,
) -> dict[str, Any]:
    last_error: Exception | None = None
    for attempt in range(1, VK_SEND_ATTEMPTS + 1):
        try:
            return sender(
                token=token,
                peer_id=peer_id,
                message=message,
                event_identity=event_identity,
            )
        except Exception as exc:
            last_error = exc
            if attempt < VK_SEND_ATTEMPTS:
                time.sleep(min(2.0, 0.25 * attempt))
    assert last_error is not None
    raise last_error


def send_from_environment() -> int:
    """Legacy fixed-recipient sender; production uses dynamic conversations."""

    token = str(os.getenv("VK_GROUP_TOKEN") or "").strip()
    peers = configured_peer_ids()
    message = str(os.getenv("VK_WHEEL_MESSAGE") or "").strip()
    wheel_url = str(os.getenv("VK_WHEEL_URL") or "").strip()
    event_identity = str(os.getenv("VK_WHEEL_EVENT_ID") or "").strip()

    if not token or not peers:
        print("VK fixed-recipient delivery is not configured; skipping")
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
            _send_with_retries(
                token=token,
                peer_id=peer_id,
                message=message,
                event_identity=event_identity,
            )
            sent += 1
        except Exception as exc:
            failures.append(peer_id)
            print(f"WARNING VK target {peer_id}: {type(exc).__name__}: {exc}")
    print(f"VK fixed-recipient delivery: sent={sent}, failed={len(failures)}")
    if failures:
        raise SystemExit("VK delivery failed for: " + ", ".join(failures))
    return 0


def self_test() -> None:
    assert _plain_text("🎡 <b>Новое колесо</b>") == "🎡 Новое колесо"
    assert configured_peer_ids("1, 2;2 bad -3") == ["1", "2", "-3"]
    assert vk_random_id("event", "1") == vk_random_id("event", "1")
    assert vk_random_id("event", "1") != vk_random_id("event", "2")
    assert _canonical_initial_identity("wheel:wheels:test:active:token") == (
        "wheel:wheels:test:detected:token"
    )
    print("VK wheel notifications self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--send-env", action="store_true")
    args = parser.parse_args()
    if args.send_env:
        return send_from_environment()
    self_test()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
