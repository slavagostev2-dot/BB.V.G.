from __future__ import annotations

from typing import Any

import vk_dynamic_subscribers as vk


def test_discovers_only_writable_user_conversations_across_pages(monkeypatch) -> None:
    monkeypatch.setattr(vk, "VK_CONVERSATION_PAGE_SIZE", 2)
    calls: list[int] = []

    def api_call(method: str, token: str, **params: Any) -> dict[str, Any]:
        assert method == "messages.getConversations"
        assert token == "token"
        offset = int(params["offset"])
        calls.append(offset)
        if offset == 0:
            return {
                "count": 4,
                "items": [
                    {"conversation": {"peer": {"id": 10, "type": "user"}, "can_write": {"allowed": True}}},
                    {"conversation": {"peer": {"id": 20, "type": "chat"}, "can_write": {"allowed": True}}},
                ],
            }
        return {
            "count": 4,
            "items": [
                {"conversation": {"peer": {"id": 30, "type": "user"}, "can_write": {"allowed": False}}},
                {"conversation": {"peer": {"id": 40, "type": "user"}}},
            ],
        }

    assert vk.conversation_peer_ids("token", api_call=api_call) == ["10", "40"]
    assert calls == [0, 2]


def test_stable_random_id_is_per_event_and_peer() -> None:
    assert vk.vk_random_id("wheel", "10") == vk.vk_random_id("wheel", "10")
    assert vk.vk_random_id("wheel", "10") != vk.vk_random_id("wheel", "20")
