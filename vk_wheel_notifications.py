from __future__ import annotations

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
VK_REQUEST_TIMEOUT_SECONDS = max(
    3.0, float(os.getenv("VK_REQUEST_TIMEOUT_SECONDS", "15") or 15)
)
VK_SEND_ATTEMPTS = max(1, int(os.getenv("VK_SEND_ATTEMPTS", "3") or 3))
_TAG_RE = re.compile(r"<[^>]+>")
_SPLIT_PEERS_RE = re.compile(r"[\s,;]+")


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


def _wheel_event(
    router_module: Any,
    text: str,
    url: str | None,
    reply_markup: dict | None,
) -> tuple[bool, str]:
    kind = str(router_module.notification_kind(text) or "")
    if kind != "wheels":
        return False, ""
    identity = str(
        router_module.notification_event_identity(kind, text, url, reply_markup) or ""
    )
    if not identity.startswith("wheel:wheels:"):
        return False, ""
    # The Active Wheels menu is UI, not an outbound wheel notification.
    lowered = _plain_text(text).casefold()
    if "активные колёса" in lowered and "новое колесо" not in lowered:
        return False, ""
    return True, identity


def _vk_message(text: str, wheel_url: str) -> str:
    message = _plain_text(text)
    if wheel_url and wheel_url not in message:
        message = f"{message}\n\n{wheel_url}" if message else wheel_url
    return message


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
    sender: Callable[..., dict[str, Any]],
    *,
    token: str,
    peer_id: str,
    message: str,
    event_identity: str,
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


def deliver_vk_wheel_notification(
    router_module: Any,
    text: str,
    url: str | None = None,
    reply_markup: dict | None = None,
    *,
    token: str | None = None,
    peer_ids: list[str] | None = None,
    sender: Callable[..., dict[str, Any]] = send_vk_message,
) -> dict[str, Any]:
    access_token = str(token if token is not None else os.getenv("VK_GROUP_TOKEN", "")).strip()
    targets = list(peer_ids) if peer_ids is not None else configured_peer_ids()
    if not access_token or not targets:
        return {"configured": False, "eligible": False, "sent": 0, "failed": 0}

    eligible, event_identity = _wheel_event(router_module, text, url, reply_markup)
    if not eligible:
        return {"configured": True, "eligible": False, "sent": 0, "failed": 0}

    wheel_url = _wheel_url(router_module, text, url, reply_markup)
    message = _vk_message(text, wheel_url)
    sent = 0
    failed = 0
    duplicates = 0

    for peer_id in targets:
        dedup_key = router_module.delivery_key(
            f"vk:{peer_id}",
            "wheels",
            event_identity,
            wheel_url,
        )
        if not router_module.claim_delivery(dedup_key):
            duplicates += 1
            continue
        try:
            _send_with_retries(
                sender,
                token=access_token,
                peer_id=peer_id,
                message=message,
                event_identity=event_identity,
            )
        except Exception as exc:
            router_module.release_delivery(dedup_key)
            failed += 1
            print(f"WARNING VK wheel notification to {peer_id}: {type(exc).__name__}: {exc}")
            continue
        router_module.complete_delivery(dedup_key)
        sent += 1

    return {
        "configured": True,
        "eligible": True,
        "sent": sent,
        "failed": failed,
        "duplicates": duplicates,
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
            deliver_vk_wheel_notification(
                router_module,
                text,
                url=url,
                reply_markup=reply_markup,
            )
        except Exception as exc:
            # VK is an optional second transport. It must never convert a
            # successful Telegram notification into a failed monitor event.
            print(f"WARNING VK wheel notification transport: {type(exc).__name__}: {exc}")

        if telegram_error is not None:
            raise telegram_error
        return telegram_result or {"ok": True, "result": {"sent": 0}}

    monitor_module.send_message = send_message_with_vk
    monitor_module._bbvg_vk_wheel_notifications_installed = True


def self_test() -> None:
    assert _plain_text("🎡 <b>Новое колесо</b>") == "🎡 Новое колесо"
    assert configured_peer_ids("1, 2;2 bad -3") == ["1", "2", "-3"]
    assert vk_random_id("event", "1") == vk_random_id("event", "1")
    assert vk_random_id("event", "1") != vk_random_id("event", "2")
    print("VK wheel notifications self-test passed")


if __name__ == "__main__":
    self_test()
