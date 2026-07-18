from __future__ import annotations

import json
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

import admin_action_queue
import bbvg_monitor_main
import personal_wheel_voting
from bbvg.bot import runtime as bot_runtime


UTC = timezone.utc


def test_user_and_admin_weights_credit_every_source_once() -> None:
    stats = {"version": 1, "sources": {}, "daily": {}}
    user = personal_wheel_voting.actor_vote_token("100", secret="test-secret")
    admin = personal_wheel_voting.actor_vote_token("200", secret="test-secret")
    owner = personal_wheel_voting.actor_vote_token("300", secret="test-secret")
    event = "wheel-a#action:10"
    assert personal_wheel_voting.record_personal_vote(
        stats,
        event_key=event,
        sources=["first", "second", "first"],
        actor=user,
        role="user",
        weight=1,
        at=datetime(2026, 7, 16, 12, 0, tzinfo=UTC),
    )
    assert personal_wheel_voting.record_personal_vote(
        stats,
        event_key=event,
        sources=["first", "second"],
        actor=admin,
        role="admin",
        weight=5,
        at=datetime(2026, 7, 16, 12, 1, tzinfo=UTC),
    )
    assert personal_wheel_voting.record_personal_vote(
        stats,
        event_key=event,
        sources=["first", "second"],
        actor=owner,
        role="owner",
        weight=5,
        at=datetime(2026, 7, 16, 12, 2, tzinfo=UTC),
    )
    assert stats["sources"]["first"]["quality_score"] == 11
    assert stats["sources"]["second"]["quality_score"] == 11


def test_same_actor_is_idempotent_per_action_id() -> None:
    stats = {"version": 1, "sources": {}, "daily": {}}
    actor = personal_wheel_voting.actor_vote_token("100", secret="test-secret")
    kwargs = {
        "event_key": "wheel-a#action:10",
        "sources": ["first"],
        "actor": actor,
        "role": "user",
        "weight": 1,
        "at": datetime(2026, 7, 16, 12, 0, tzinfo=UTC),
    }
    assert personal_wheel_voting.record_personal_vote(stats, **kwargs)
    assert not personal_wheel_voting.record_personal_vote(stats, **kwargs)
    assert stats["sources"]["first"]["quality_score"] == 1


def test_same_action_id_new_generation_is_a_new_vote_event() -> None:
    first = personal_wheel_voting.wheel_event_key(
        "wheel-a", {"action_id": 10, "generation_id": "generation-one"}
    )
    second = personal_wheel_voting.wheel_event_key(
        "wheel-a", {"action_id": 10, "generation_id": "generation-two"}
    )
    assert first != second

    stats = {"version": 1, "sources": {}, "daily": {}}
    actor = personal_wheel_voting.actor_vote_token("100", secret="test-secret")
    for event in (first, second):
        assert personal_wheel_voting.record_personal_vote(
            stats,
            event_key=event,
            sources=["first"],
            actor=actor,
            role="user",
            weight=1,
            at=datetime(2026, 7, 16, 12, 0, tzinfo=UTC),
        )
    assert stats["sources"]["first"]["quality_score"] == 2


def test_new_action_id_is_a_new_vote_event() -> None:
    stats = {"version": 1, "sources": {}, "daily": {}}
    actor = personal_wheel_voting.actor_vote_token("100", secret="test-secret")
    for action_id in (10, 11):
        assert personal_wheel_voting.record_personal_vote(
            stats,
            event_key=f"wheel-a#action:{action_id}",
            sources=["first"],
            actor=actor,
            role="user",
            weight=1,
            at=datetime(2026, 7, 16, 12, action_id, tzinfo=UTC),
        )
    assert stats["sources"]["first"]["quality_score"] == 2


def test_actor_token_never_contains_telegram_id() -> None:
    token = personal_wheel_voting.actor_vote_token("123456789", secret="test-secret")
    assert "123456789" not in token
    assert personal_wheel_voting.ACTOR_TOKEN_RE.fullmatch(token)


def test_reminder_event_key_does_not_reuse_old_action() -> None:
    old = personal_wheel_voting.wheel_event_key("wheel-a", {"action_id": 10})
    new = personal_wheel_voting.wheel_event_key("wheel-a", {"action_id": 11})
    assert old != new


def test_bot_token_is_not_accepted_as_state_key(monkeypatch: pytest.MonkeyPatch) -> None:
    assert bot_runtime.PersonalWheelVotingMixin is personal_wheel_voting.PersonalWheelVotingMixin
    monkeypatch.delenv("BOT_STATE_KEY", raising=False)
    monkeypatch.setenv("BOT_TOKEN", "bot-token-must-not-be-used")
    with pytest.raises(RuntimeError, match="BOT_STATE_KEY"):
        personal_wheel_voting.actor_vote_token("100")


def test_applied_vote_commands_repair_lost_rating_without_duplicates() -> None:
    queue = admin_action_queue.default_queue()
    command_ids: list[str] = []
    for index, (user_id, role, weight) in enumerate(
        (("100", "owner", 5), ("200", "admin", 5), ("300", "user", 1)),
        1,
    ):
        actor = personal_wheel_voting.actor_vote_token(user_id, secret="test-secret")
        payload = {
            "wheel_key": "wheel-a",
            "event_key": "wheel-a#action:10",
            "actor": actor,
            "role": role,
            "weight": weight,
            "sources": ["first"],
        }
        queue, command_id = admin_action_queue.append_command(
            queue,
            "record_personal_vote",
            json.dumps(payload),
            command_id=f"repair-vote-{index}",
        )
        command_ids.append(command_id)

    state = {
        "applied_admin_actions": {
            command_id: "2026-07-17T08:00:00+00:00" for command_id in command_ids
        }
    }
    health = {"sources": {}}
    lost_stats = {"version": 1, "sources": {}, "daily": {}}

    repaired = admin_action_queue.process_pending(
        state, health, lost_stats, queue=queue
    )
    stable = admin_action_queue.process_pending(
        state, health, lost_stats, queue=queue
    )

    assert repaired["applied"] == 3
    assert stable["applied"] == 0
    assert len(lost_stats["personal_wheel_votes"]) == 3
    assert lost_stats["sources"]["first"]["quality_score"] == 11
    assert lost_stats["sources"]["first"]["admin_votes"] == 2
    assert lost_stats["sources"]["first"]["user_votes"] == 1


def test_participation_is_scoped_to_current_telegram_account(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("BOT_STATE_KEY", "test-state-key")
    access = {
        "users": {
            "100": {"id": "100", "chat_id": "100", "participating_wheels": {}},
            "200": {"id": "200", "chat_id": "200", "participating_wheels": {}},
        }
    }
    dispatched: list[dict[str, object]] = []

    class Panel(personal_wheel_voting.PersonalWheelVotingMixin):
        current_user_id = "100"
        current_chat_id = "100"
        current_role = "user"

        def load_access(self, force: bool = False) -> dict[str, object]:
            del force
            return access

        def save_access(self, message: str) -> None:
            del message

        def _active_item(self, key: str) -> tuple[SimpleNamespace, dict[str, object]]:
            return SimpleNamespace(), {
                "_key": key,
                "identifier": key,
                "action_id": 10,
            }

        def role_for(self, user_id: str) -> str:
            del user_id
            return "user"

        def _sources_for_item(
            self, snap: SimpleNamespace, key: str, item: dict[str, object]
        ) -> list[str]:
            del snap, key, item
            return ["first"]

        def dispatch_admin_action(self, action: str, value: str) -> dict[str, object]:
            dispatched.append({"action": action, "payload": json.loads(value)})
            return {"queued": True, "command_id": f"command-{len(dispatched)}"}

    panel = Panel()
    panel.mark_personal_participation("wheel-a")
    first_event = "wheel-a#action:10"
    assert first_event in access["users"]["100"]["participating_wheels"]
    assert first_event not in access["users"]["200"]["participating_wheels"]

    panel.current_user_id = "200"
    panel.current_chat_id = "200"
    assert first_event not in panel._personal_participating_wheels()
    panel.mark_personal_participation("wheel-a")

    assert first_event in access["users"]["100"]["participating_wheels"]
    assert first_event in access["users"]["200"]["participating_wheels"]
    assert dispatched[0]["payload"]["actor"] != dispatched[1]["payload"]["actor"]


def test_rating_reset_removes_scores_but_preserves_operations() -> None:
    stats = {
        "version": 1,
        "source_rating_epoch_day": "2026-07-14",
        "admin_wheel_decisions": {"old": {"decision": "confirmed"}},
        "personal_wheel_votes": {"old": {"weight": 5}},
        "sources": {
            "source": {
                "checks": 100,
                "messages_scanned": 2000,
                "recent_post_keys": {"post": {"wheel": "wheel-a"}},
                "wheel_posts": 7,
                "quality_score": 46,
                "quality_decisions": {"old": 40},
                "personal_vote_points": {"vote": 6},
                "personal_vote_score": 6,
                "personal_votes": 2,
                "user_votes": 1,
                "admin_votes": 1,
            }
        },
        "daily": {
            "2026-07-16": {
                "totals": {
                    "checks": 100,
                    "wheel_posts": 7,
                    "personal_vote_points": 6,
                },
                "sources": {
                    "source": {
                        "checks": 100,
                        "wheel_posts": 7,
                        "personal_vote_points": 6,
                    }
                },
            }
        },
    }

    changed = bbvg_monitor_main.reset_source_rating_epoch(
        stats,
        at=datetime(2026, 7, 17, 2, 0, tzinfo=UTC),
    )

    assert changed is True
    assert stats["source_rating_epoch_day"] == "2026-07-17"
    assert stats["source_rating_policy"] == "personal_votes_v1"
    assert "admin_wheel_decisions" not in stats
    assert "personal_wheel_votes" not in stats
    assert stats["sources"]["source"]["checks"] == 100
    assert stats["sources"]["source"]["messages_scanned"] == 2000
    assert stats["sources"]["source"]["recent_post_keys"] == {
        "post": {"wheel": "wheel-a"}
    }
    for field in bbvg_monitor_main.SOURCE_RATING_RESET_FIELDS:
        assert field not in stats["sources"]["source"]
        assert field not in stats["daily"]["2026-07-16"]["totals"]
        assert field not in stats["daily"]["2026-07-16"]["sources"]["source"]
    assert bbvg_monitor_main.reset_source_rating_epoch(stats) is False


def test_late_second_source_expands_existing_vote_without_duplication() -> None:
    stats = {"version": 1, "sources": {}, "daily": {}}
    actor = personal_wheel_voting.actor_vote_token("100", secret="test-secret")
    event = "zonertg8#action:693"
    at = datetime(2026, 7, 17, 10, 0, tzinfo=UTC)
    assert personal_wheel_voting.record_personal_vote(
        stats, event_key=event, sources=["mechanogun"], actor=actor,
        role="owner", weight=5, at=at,
    )
    assert personal_wheel_voting.reconcile_personal_vote_sources(
        stats, event_key=event, sources=["mechanogun", "kolesaBB"], at=at,
    ) == 1
    assert personal_wheel_voting.reconcile_personal_vote_sources(
        stats, event_key=event, sources=["mechanogun", "kolesaBB"], at=at,
    ) == 0
    assert stats["sources"]["mechanogun"]["quality_score"] == 5
    assert stats["sources"]["kolesaBB"]["quality_score"] == 5
    assert stats["daily"]["2026-07-17"]["totals"]["personal_votes"] == 1
    assert stats["daily"]["2026-07-17"]["totals"]["personal_vote_points"] == 10


def test_three_votes_credit_both_channels_equally() -> None:
    stats = {"version": 1, "sources": {}, "daily": {}}
    event = "zonertg8#action:693"
    for user_id, role, weight in (("1", "owner", 5), ("2", "admin", 5), ("3", "user", 1)):
        assert personal_wheel_voting.record_personal_vote(
            stats, event_key=event, sources=["mechanogun"],
            actor=personal_wheel_voting.actor_vote_token(user_id, secret="test-secret"),
            role=role, weight=weight, at=datetime(2026, 7, 17, 10, 0, tzinfo=UTC),
        )
    assert personal_wheel_voting.reconcile_personal_vote_sources(
        stats, event_key=event, sources=["mechanogun", "kolesaBB"]
    ) == 3
    assert stats["sources"]["mechanogun"]["quality_score"] == 11
    assert stats["sources"]["kolesaBB"]["quality_score"] == 11
    assert len(stats["personal_wheel_votes"]) == 3

