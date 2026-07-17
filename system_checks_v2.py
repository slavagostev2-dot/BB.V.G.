from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

import bot_notification_state
import bot_private_state
import notification_integrity_v2
import notification_navigation
import notification_router
import personal_wheel_voting
import system_checks as legacy

notification_router.load_config = bot_notification_state.load_config
legacy.notification_router.load_config = bot_notification_state.load_config
notification_navigation.install(legacy.monitor)


def check_miniapp_archived(details: dict[str, Any], findings: list[dict[str, Any]]) -> None:
    del findings
    details["miniapp"] = {
        "status": "archived",
        "checked": False,
        "reason": "Mini App is frozen and excluded from the production bot runtime",
    }


def check_notification_routing_private(
    details: dict[str, Any], findings: list[dict[str, Any]]
) -> None:
    config, exists = notification_router.load_config()
    admin_targets = notification_router.recipients(config, exists, "admin")
    user_targets = notification_router.recipients(config, exists, "user")
    admin_kinds = {
        kind: notification_router.recipients(config, exists, kind)
        for kind in sorted(notification_router.ADMIN_NOTIFICATION_KINDS)
    }
    user_kinds = {
        kind: notification_router.recipients(config, exists, kind)
        for kind in sorted(notification_router.USER_NOTIFICATION_KINDS)
    }

    # Public diagnostics contain counts only. Telegram ID and chat ID remain in
    # the encrypted bot state and are never copied into system_check_state.json.
    details["notification_routing"] = {
        "admin_recipient_count": len(admin_targets),
        "user_recipient_count": len(user_targets),
        "admin_kind_counts": {kind: len(targets) for kind, targets in admin_kinds.items()},
        "user_kind_counts": {kind: len(targets) for kind, targets in user_kinds.items()},
        "error_category": notification_router.classify("⚠️ Ошибка BB V.G."),
        "error_kind": notification_router.notification_kind("⚠️ Ошибка BB V.G."),
        "wheel_with_error_word_kind": notification_router.notification_kind(
            "🎡 Новое колесо BetBoom — ошибка в тексте публикации"
        ),
        "duplicate_window_seconds": notification_integrity_v2.RETENTION_SECONDS,
    }

    if notification_router.classify("⚠️ Ошибка BB V.G.") != "admin":
        findings.append(
            legacy.finding(
                "notification_routing",
                "Ошибки могут попасть обычным пользователям",
                "Тестовое сообщение об ошибке не классифицировано как административное.",
                severity="critical",
            )
        )
    if notification_router.notification_kind(
        "🎡 Новое колесо BetBoom — ошибка в тексте публикации"
    ) != "wheels":
        findings.append(
            legacy.finding(
                "notification_routing",
                "Сообщение о колесе ошибочно стало административным",
                "Ключевые слова внутри публикации изменили категорию пользовательского события.",
                severity="critical",
            )
        )

    admin_ids = notification_router.admin_user_ids(config)
    invalid_admin_targets = 0
    for targets in admin_kinds.values():
        for chat_id in targets:
            user_id, _ = notification_router.user_for_chat(config, chat_id)
            if user_id and user_id not in admin_ids:
                invalid_admin_targets += 1
    if invalid_admin_targets:
        findings.append(
            legacy.finding(
                "non_admin_error_recipient",
                "Обычный пользователь включён в получателей административных сообщений",
                f"Неверных назначений: {invalid_admin_targets}.",
                severity="critical",
            )
        )

    try:
        ledger = notification_integrity_v2.load_state()
    except notification_integrity_v2.NotificationIntegrityError as exc:
        details["notification_integrity"] = {
            "status": "failed",
            "error_type": type(exc).__name__,
        }
        findings.append(
            legacy.finding(
                "notification_routing",
                "Повреждён журнал одноразовых уведомлений",
                "Журнал не прошёл проверку формата или целостности.",
                severity="critical",
            )
        )
    else:
        details["notification_integrity"] = {
            "status": "ok",
            "format": ledger.get("format"),
            "algorithm": ledger.get("algorithm"),
            "entry_count": len(ledger.get("entries", {})),
            "retention_seconds": ledger.get("retention_seconds"),
            "router_installed": bool(
                getattr(notification_router, "_bbvg_notification_integrity_v2_installed", False)
            ),
            "contains_personal_fields": False,
        }
        if not getattr(notification_router, "_bbvg_notification_integrity_v2_installed", False):
            findings.append(
                legacy.finding(
                    "notification_routing",
                    "Постоянная дедупликация уведомлений не установлена",
                    "Маршрутизатор использует неполную политику доставки.",
                    severity="critical",
                )
            )


def _personal_rating_expectation(
    stats: dict[str, Any],
) -> tuple[dict[str, int], int, int]:
    votes = stats.get("personal_wheel_votes")
    votes = votes if isinstance(votes, dict) else {}
    expected: dict[str, int] = {}
    valid_votes = 0
    for entry in votes.values():
        if not isinstance(entry, dict):
            continue
        try:
            payload = personal_wheel_voting.normalize_vote_payload(entry)
        except (TypeError, ValueError):
            continue
        valid_votes += 1
        for source in payload["sources"]:
            key = str(source).casefold()
            if key:
                expected[key] = expected.get(key, 0) + int(payload["weight"])
    return expected, len(votes), valid_votes


def check_rating_consistency_additive(
    details: dict[str, Any], findings: list[dict[str, Any]]
) -> None:
    stats = legacy.load_json(legacy.SOURCE_STATS_PATH, {})
    state = legacy.load_json(legacy.RUNTIME_STATE_PATH, {})
    stats = stats if isinstance(stats, dict) else {}
    state = state if isinstance(state, dict) else {}
    decisions = stats.get("admin_wheel_decisions")
    decisions = decisions if isinstance(decisions, dict) else {}
    inactive_decisions: list[str] = []
    for wheel, entry in decisions.items():
        if isinstance(entry, dict) and str(entry.get("decision") or "") == "inactive":
            inactive_decisions.append(str(wheel).casefold())

    policy = str(stats.get("source_rating_policy") or "additive_only_v1")
    personal_vote_records = 0
    valid_personal_votes = 0
    if policy == personal_wheel_voting.PERSONAL_RATING_POLICY:
        expected, personal_vote_records, valid_personal_votes = _personal_rating_expectation(
            stats
        )
        mismatch_title = "Рейтинг источников не совпадает с личными голосами"
    else:
        expected: dict[str, int] = {}
        for entry in decisions.values():
            if not isinstance(entry, dict):
                continue
            verdict = str(entry.get("decision") or "")
            points = 40 if verdict == "confirmed" else 0
            for source in entry.get("sources", []):
                key = str(source).casefold()
                if key:
                    expected[key] = expected.get(key, 0) + points
        mismatch_title = "Рейтинг источников не совпадает с решениями администратора"

    actual = {
        str(source).casefold(): max(0, int(entry.get("quality_score", 0) or 0))
        for source, entry in stats.get("sources", {}).items()
        if isinstance(entry, dict) and entry.get("quality_score") is not None
    }
    mismatches = sorted(
        key
        for key in set(expected) | set(actual)
        if expected.get(key, 0) != actual.get(key, 0)
    )
    active_keys = {str(key).casefold() for key in state.get("active_wheels", {})}
    participating_keys = {
        str(key).casefold() for key in state.get("participating_wheels", {})
    }
    inactive_leaks = sorted(set(inactive_decisions) & (active_keys | participating_keys))
    details["rating_consistency"] = {
        "policy": policy,
        "administrator_decisions": len(decisions),
        "personal_vote_records": personal_vote_records,
        "valid_personal_votes": valid_personal_votes,
        "rated_sources": len(expected),
        "score_mismatches": mismatches[:30],
        "inactive_wheel_leaks": inactive_leaks[:30],
    }
    if mismatches:
        findings.append(
            legacy.finding(
                "rating_score_mismatch",
                mismatch_title,
                f"Количество несовпадений: {len(mismatches)}.",
                severity="critical",
            )
        )
    if inactive_leaks:
        findings.append(
            legacy.finding(
                "inactive_wheel_leak",
                "Неактивное колесо осталось в пользовательских списках",
                f"Количество событий: {len(inactive_leaks)}.",
                severity="critical",
            )
        )


legacy.check_miniapp_release = check_miniapp_archived
legacy.check_notification_routing = check_notification_routing_private
legacy.check_rating_consistency = check_rating_consistency_additive


def self_test() -> None:
    details: dict[str, Any] = {}
    findings: list[dict[str, Any]] = []
    check_miniapp_archived(details, findings)
    assert details["miniapp"]["status"] == "archived"
    assert not findings
    assert legacy.monitor._bbvg_notification_navigation_installed is True
    assert notification_router._bbvg_notification_integrity_v2_installed is True

    original_state_path = bot_private_state.STATE_PATH
    try:
        with TemporaryDirectory() as temporary:
            # The routing self-test uses the fallback owner and must not attempt
            # to decrypt the production bundle with a synthetic CI key.
            bot_private_state.STATE_PATH = Path(temporary) / "missing-state.enc.json"
            routing_details: dict[str, Any] = {}
            routing_findings: list[dict[str, Any]] = []
            check_notification_routing_private(routing_details, routing_findings)
    finally:
        bot_private_state.STATE_PATH = original_state_path

    routing = routing_details["notification_routing"]
    assert "admin_recipients" not in routing
    assert "user_recipients" not in routing
    assert isinstance(routing["admin_recipient_count"], int)
    assert routing["wheel_with_error_word_kind"] == "wheels"
    assert routing_details["notification_integrity"]["status"] == "ok"
    assert not routing_findings

    original_stats_path = legacy.SOURCE_STATS_PATH
    original_runtime_path = legacy.RUNTIME_STATE_PATH
    try:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            legacy.SOURCE_STATS_PATH = root / "source_stats.json"
            legacy.RUNTIME_STATE_PATH = root / "state.json"
            legacy.RUNTIME_STATE_PATH.write_text("{}", encoding="utf-8")
            personal_stats = {
                "source_rating_policy": personal_wheel_voting.PERSONAL_RATING_POLICY,
                "personal_wheel_votes": {
                    "owner": {
                        "wheel_key": "wheel-a",
                        "event_key": "wheel-a#action:1",
                        "actor": personal_wheel_voting.actor_vote_token(
                            "owner", secret="self-test-secret"
                        ),
                        "role": "owner",
                        "weight": 5,
                        "sources": ["source"],
                    },
                    "admin": {
                        "wheel_key": "wheel-a",
                        "event_key": "wheel-a#action:1",
                        "actor": personal_wheel_voting.actor_vote_token(
                            "admin", secret="self-test-secret"
                        ),
                        "role": "admin",
                        "weight": 5,
                        "sources": ["source"],
                    },
                    "user": {
                        "wheel_key": "wheel-a",
                        "event_key": "wheel-a#action:1",
                        "actor": personal_wheel_voting.actor_vote_token(
                            "user", secret="self-test-secret"
                        ),
                        "role": "user",
                        "weight": 1,
                        "sources": ["source"],
                    },
                },
                "sources": {"source": {"quality_score": 11}},
            }
            legacy.SOURCE_STATS_PATH.write_text(
                json.dumps(personal_stats), encoding="utf-8"
            )
            rating_details: dict[str, Any] = {}
            rating_findings: list[dict[str, Any]] = []
            check_rating_consistency_additive(rating_details, rating_findings)
            assert not rating_findings
            assert rating_details["rating_consistency"]["policy"] == "personal_votes_v1"
            assert rating_details["rating_consistency"]["valid_personal_votes"] == 3
            assert rating_details["rating_consistency"]["rated_sources"] == 1

            personal_stats["sources"]["source"]["quality_score"] = 10
            legacy.SOURCE_STATS_PATH.write_text(
                json.dumps(personal_stats), encoding="utf-8"
            )
            mismatch_details: dict[str, Any] = {}
            mismatch_findings: list[dict[str, Any]] = []
            check_rating_consistency_additive(mismatch_details, mismatch_findings)
            assert {item["kind"] for item in mismatch_findings} == {
                "rating_score_mismatch"
            }
            assert mismatch_findings[0]["title"] == (
                "Рейтинг источников не совпадает с личными голосами"
            )

            legacy_stats = {
                "admin_wheel_decisions": {
                    "wheel-a": {
                        "decision": "confirmed",
                        "sources": ["source"],
                    }
                },
                "sources": {"source": {"quality_score": 40}},
            }
            legacy.SOURCE_STATS_PATH.write_text(
                json.dumps(legacy_stats), encoding="utf-8"
            )
            legacy_details: dict[str, Any] = {}
            legacy_findings: list[dict[str, Any]] = []
            check_rating_consistency_additive(legacy_details, legacy_findings)
            assert not legacy_findings
            assert legacy_details["rating_consistency"]["policy"] == "additive_only_v1"
    finally:
        legacy.SOURCE_STATS_PATH = original_stats_path
        legacy.RUNTIME_STATE_PATH = original_runtime_path

    original_panel_path = legacy.ADMIN_PANEL_STATUS_PATH
    try:
        with TemporaryDirectory() as temporary:
            legacy.ADMIN_PANEL_STATUS_PATH = Path(temporary) / "admin_panel_status.json"
            legacy.ADMIN_PANEL_STATUS_PATH.write_text(
                json.dumps(
                    {
                        "status": "running",
                        "heartbeat_version": 1,
                        "last_heartbeat_at": datetime(2000, 1, 1, tzinfo=timezone.utc).isoformat(),
                    }
                ),
                encoding="utf-8",
            )
            panel_details: dict[str, Any] = {}
            panel_findings: list[dict[str, Any]] = []
            legacy.check_admin_panel_runtime(panel_details, panel_findings)
    finally:
        legacy.ADMIN_PANEL_STATUS_PATH = original_panel_path
    assert panel_details["admin_panel"]["age_minutes"] > legacy.ADMIN_PANEL_MAX_AGE_MINUTES
    assert {item["kind"] for item in panel_findings} == {"admin_panel_stale"}
    print("BB V.G. bot-only system checks self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return 0
    return legacy.main()


if __name__ == "__main__":
    raise SystemExit(main())
