from __future__ import annotations

import hashlib
import json
import os
import time
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

VK_API_VERSION = os.getenv("VK_API_VERSION", "5.199").strip() or "5.199"
VK_API_BASE = "https://api.vk.com/method"
VK_REQUEST_TIMEOUT_SECONDS = max(3.0, float(os.getenv("VK_REQUEST_TIMEOUT_SECONDS", "15") or 15))
VK_SEND_ATTEMPTS = max(1, int(os.getenv("VK_SEND_ATTEMPTS", "3") or 3))
VK_CONVERSATION_PAGE_SIZE = 200


def _vk_api(method: str, token: str, **params: Any) -> dict[str, Any]:
    payload = {"access_token": token, "v": VK_API_VERSION, **params}
    request = Request(
        f"{VK_API_BASE}/{method}",
        data=urlencode(payload).encode("utf-8"),
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
        raise RuntimeError(f"VK API transport failed for {method}: {type(exc).__name__}: {exc}") from exc
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"VK API returned invalid JSON for {method}") from exc
    if not isinstance(data, dict):
        raise RuntimeError(f"VK API returned unexpected response for {method}")
    error = data.get("error")
    if isinstance(error, dict):
        raise RuntimeError(
            f"VK API error {error.get('error_code')} for {method}: "
            f"{error.get('error_msg') or 'unknown VK API error'}"
        )
    response = data.get("response")
    return response if isinstance(response, dict) else {"value": response}


def conversation_peer_ids(
    token: str,
    *,
    api_call: Callable[..., dict[str, Any]] = _vk_api,
) -> list[str]:
    """Return writable user dialogs of the community, without storing user IDs."""
    result: list[str] = []
    offset = 0
    total: int | None = None
    while total is None or offset < total:
        page = api_call(
            "messages.getConversations",
            token,
            offset=offset,
            count=VK_CONVERSATION_PAGE_SIZE,
            filter="all",
        )
        items = page.get("items") if isinstance(page, dict) else []
        if not isinstance(items, list):
            items = []
        try:
            total = int(page.get("count", len(items)))
        except (TypeError, ValueError):
            total = len(items)

        for item in items:
            if not isinstance(item, dict):
                continue
            conversation = item.get("conversation")
            if not isinstance(conversation, dict):
                continue
            peer = conversation.get("peer")
            if not isinstance(peer, dict) or str(peer.get("type") or "") != "user":
                continue
            can_write = conversation.get("can_write")
            if isinstance(can_write, dict) and can_write.get("allowed") is False:
                continue
            try:
                peer_id = str(int(peer.get("id")))
            except (TypeError, ValueError):
                continue
            if peer_id not in result:
                result.append(peer_id)

        if not items:
            break
        offset += len(items)
    return result


def vk_random_id(event_identity: str, peer_id: str) -> int:
    digest = hashlib.sha256(f"{event_identity}\x1f{peer_id}".encode("utf-8")).digest()
    value = int.from_bytes(digest[:4], "big") & 0x7FFFFFFF
    return value or 1


def send_message(token: str, peer_id: str, message: str, event_identity: str) -> None:
    last_error: Exception | None = None
    for attempt in range(1, VK_SEND_ATTEMPTS + 1):
        try:
            _vk_api(
                "messages.send",
                token,
                peer_id=peer_id,
                random_id=vk_random_id(event_identity, peer_id),
                message=message,
            )
            return
        except Exception as exc:
            last_error = exc
            if attempt < VK_SEND_ATTEMPTS:
                time.sleep(min(2.0, 0.25 * attempt))
    assert last_error is not None
    raise last_error


def send_from_environment() -> int:
    token = str(os.getenv("VK_GROUP_TOKEN") or "").strip()
    message = str(os.getenv("VK_WHEEL_MESSAGE") or "").strip()
    wheel_url = str(os.getenv("VK_WHEEL_URL") or "").strip()
    event_identity = str(os.getenv("VK_WHEEL_EVENT_ID") or "").strip()
    if not token:
        print("VK_GROUP_TOKEN is not configured; skipping VK delivery")
        return 0
    if not message:
        raise SystemExit("VK_WHEEL_MESSAGE is required")
    if wheel_url and wheel_url not in message:
        message = f"{message}\n\n{wheel_url}"
    if not event_identity:
        event_identity = hashlib.sha256(message.encode("utf-8")).hexdigest()

    peers = conversation_peer_ids(token)
    if not peers:
        print("VK wheel notification delivery: no writable user conversations")
        return 0

    sent = 0
    failed = 0
    for peer_id in peers:
        try:
            send_message(token, peer_id, message, event_identity)
            sent += 1
        except Exception as exc:
            failed += 1
            print(f"WARNING VK target {peer_id}: {type(exc).__name__}: {exc}")
    print(f"VK wheel notification delivery: subscribers={len(peers)}, sent={sent}, failed={failed}")
    if failed and sent == 0:
        raise SystemExit("VK delivery failed for all writable conversations")
    return 0


def self_test() -> None:
    pages = {
        0: {
            "count": 3,
            "items": [
                {"conversation": {"peer": {"id": 10, "type": "user"}, "can_write": {"allowed": True}}},
                {"conversation": {"peer": {"id": 20, "type": "chat"}, "can_write": {"allowed": True}}},
                {"conversation": {"peer": {"id": 30, "type": "user"}, "can_write": {"allowed": False}}},
            ],
        }
    }

    def fake_api(method: str, token: str, **params: Any) -> dict[str, Any]:
        assert method == "messages.getConversations"
        assert token == "token"
        return pages.get(int(params.get("offset", 0)), {"count": 3, "items": []})

    assert conversation_peer_ids("token", api_call=fake_api) == ["10"]
    assert vk_random_id("wheel", "10") == vk_random_id("wheel", "10")
    print("VK automatic conversation subscribers self-test passed")


if __name__ == "__main__":
    if "--self-test" in os.sys.argv:
        self_test()
        raise SystemExit(0)
    raise SystemExit(send_from_environment())
