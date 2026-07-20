from __future__ import annotations

import hashlib
import json
import os
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

VK_API_BASE = "https://api.vk.com/method"
VK_API_VERSION = os.getenv("VK_API_VERSION", "5.199").strip() or "5.199"
VK_REQUEST_TIMEOUT_SECONDS = 15.0
WELCOME_TEXT = (
    "👋 Привет! BB V.G. работает и готов присылать уведомления о новых колёсах BetBoom.\n\n"
    "Бот отслеживает появление новых колёс и отправляет сюда уведомление со ссылкой, как только колесо будет найдено.\n\n"
    "Дополнительных команд использовать не нужно — после начала диалога уведомления будут приходить автоматически."
)
START_WORDS = {"старт", "start", "/start", "начать"}


def _api_call(method: str, *, token: str, **params: Any) -> dict[str, Any]:
    payload = {
        "access_token": token,
        "v": VK_API_VERSION,
        **{key: str(value) for key, value in params.items()},
    }
    request = Request(
        f"{VK_API_BASE}/{method}",
        data=urlencode(payload).encode("utf-8"),
        method="POST",
        headers={
            "Content-Type": "application/x-www-form-urlencoded; charset=utf-8",
            "User-Agent": "BB-VG-VK-Start-Welcome",
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
        raise RuntimeError(
            f"VK API error {error.get('error_code')}: "
            f"{error.get('error_msg') or 'unknown VK API error'}"
        )
    response_data = data.get("response")
    return response_data if isinstance(response_data, dict) else {"value": response_data}


def _normalized_text(value: str) -> str:
    return " ".join(str(value or "").strip().casefold().split())


def is_start_message(value: str) -> bool:
    return _normalized_text(value) in START_WORDS


def welcome_random_id(peer_id: int, message_id: int) -> int:
    digest = hashlib.sha256(f"vk-start-welcome\x1f{peer_id}\x1f{message_id}".encode("utf-8")).digest()
    return (int.from_bytes(digest[:4], "big") & 0x7FFFFFFF) or 1


def process_unread_start_messages(*, token: str) -> dict[str, int]:
    conversations = _api_call(
        "messages.getConversations",
        token=token,
        filter="unread",
        count=200,
    )
    items = conversations.get("items") if isinstance(conversations, dict) else []
    if not isinstance(items, list):
        items = []

    checked = 0
    welcomed = 0
    for item in items:
        if not isinstance(item, dict):
            continue
        conversation = item.get("conversation")
        last_message = item.get("last_message")
        if not isinstance(conversation, dict) or not isinstance(last_message, dict):
            continue
        peer = conversation.get("peer")
        can_write = conversation.get("can_write")
        if not isinstance(peer, dict) or str(peer.get("type") or "") != "user":
            continue
        if isinstance(can_write, dict) and can_write.get("allowed") is False:
            continue

        peer_id = int(peer.get("id") or 0)
        message_id = int(last_message.get("id") or 0)
        from_id = int(last_message.get("from_id") or 0)
        text = str(last_message.get("text") or "")
        if peer_id <= 0 or from_id != peer_id:
            continue
        checked += 1
        if not is_start_message(text):
            continue

        _api_call(
            "messages.send",
            token=token,
            peer_id=peer_id,
            random_id=welcome_random_id(peer_id, message_id),
            message=WELCOME_TEXT,
        )
        _api_call("messages.markAsRead", token=token, peer_id=peer_id)
        welcomed += 1

    return {"checked": checked, "welcomed": welcomed}


def main() -> int:
    token = str(os.getenv("VK_GROUP_TOKEN") or "").strip()
    if not token:
        print("VK_GROUP_TOKEN is not configured; skipping VK Start welcome")
        return 0
    result = process_unread_start_messages(token=token)
    print(f"VK Start welcome: checked={result['checked']}, welcomed={result['welcomed']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
