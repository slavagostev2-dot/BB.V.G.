from __future__ import annotations

import html
import json
from datetime import datetime, timezone
from typing import Any, Iterable

from bbvg.ai_core import AIClient, FEATURE_HEALTH_INSPECTOR, client_from_env

UTC = timezone.utc
MAX_AI_FINDINGS = 12


def _as_dict(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _text(value: object, limit: int) -> str:
    return str(value or "").strip()[:limit]


def active_findings(findings: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        item
        for item in findings
        if isinstance(item, dict) and item.get("status") in {None, "active"}
    ]


def recommended_action(kinds: set[str]) -> str:
    if any(kind.startswith("monitor_") for kind in kinds) or {
        "all_sources_unreachable",
        "partial_source_failure",
    } & kinds:
        return "Проверить состояние основного monitor workflow и время последней успешной итерации."
    if any(kind.startswith("telegram_") for kind in kinds) or "legacy_domain_redirect" in kinds:
        return "Проверить Telegram transport и доступность рабочего web-домена."
    if "wheel_api_validation_failure" in kinds:
        return "Проверить доступность BetBoom API и журнал последних проверок колёс."
    if any(kind.startswith("admin_panel_") for kind in kinds):
        return "Проверить heartbeat Control Center и последний workflow панели."
    if any(kind.startswith("source_transport_") for kind in kinds) or "source_inventory" in kinds:
        return "Сверить настроенный inventory с последней транспортной проверкой."
    if {"rating_score_mismatch", "inactive_wheel_leak"} & kinds:
        return "Проверить консистентность состояния колёс и рейтинга штатными средствами проекта."
    if kinds:
        return "Открыть system_check_state.json и активные инциденты, затем подтвердить первопричину."
    return "Вмешательство не требуется. Продолжать штатный мониторинг."


def rules_assessment(
    details: dict[str, Any], findings: Iterable[dict[str, Any]]
) -> dict[str, Any]:
    active = active_findings(findings)
    critical = sum(
        1 for item in active if str(item.get("severity") or "warning") == "critical"
    )
    warnings = max(0, len(active) - critical)
    kinds = {str(item.get("kind") or "").strip() for item in active if item.get("kind")}
    monitor = _as_dict(details.get("monitor"))
    checked = int(monitor.get("checked_sources", 0) or 0)
    reachable = int(monitor.get("reachable_sources", 0) or 0)
    source_errors = int(monitor.get("source_errors", 0) or 0)

    if critical:
        severity = "critical"
        summary = f"Система требует внимания: критических проблем — {critical}, предупреждений — {warnings}."
        impact = "Часть функций BB V.G. может работать неполно до устранения подтверждённой причины."
    elif warnings:
        severity = "warning"
        summary = f"Система работает с отклонениями: активных предупреждений — {warnings}."
        impact = "Основные функции продолжают работать, но отдельные проверки требуют наблюдения."
    else:
        severity = "ok"
        summary = "Системные проверки не обнаружили активных проблем."
        impact = "Мониторинг и связанные контуры могут продолжать штатную работу."

    if checked:
        summary += f" Источники последнего цикла: доступно {reachable}/{checked}, ошибок {source_errors}."

    return {
        "severity": severity,
        "summary": summary,
        "impact": impact,
        "recommended_action": recommended_action(kinds),
        "requires_human_attention": severity == "critical",
        "critical_count": critical,
        "warning_count": warnings,
        "finding_count": len(active),
        "finding_kinds": sorted(kinds),
    }


def safe_ai_input(
    details: dict[str, Any], findings: list[dict[str, Any]], rules: dict[str, Any]
) -> dict[str, Any]:
    monitor = _as_dict(details.get("monitor"))
    inventory = _as_dict(details.get("inventory"))
    wheel_api = _as_dict(details.get("wheel_api_health"))
    compact = [
        {
            "kind": _text(item.get("kind"), 120),
            "severity": _text(item.get("severity"), 30),
            "title": _text(item.get("title"), 240),
            "detail": _text(item.get("detail"), 500),
        }
        for item in findings[:MAX_AI_FINDINGS]
    ]
    return {
        "deterministic_severity": rules["severity"],
        "deterministic_summary": rules["summary"],
        "deterministic_recommended_action": rules["recommended_action"],
        "critical_count": rules["critical_count"],
        "warning_count": rules["warning_count"],
        "monitor": {
            "status": _text(monitor.get("status"), 80),
            "checked_sources": int(monitor.get("checked_sources", 0) or 0),
            "reachable_sources": int(monitor.get("reachable_sources", 0) or 0),
            "source_errors": int(monitor.get("source_errors", 0) or 0),
            "consecutive_failures": int(monitor.get("consecutive_failures", 0) or 0),
            "consecutive_no_progress": int(monitor.get("consecutive_no_progress", 0) or 0),
            "restart_recommended": bool(monitor.get("restart_recommended")),
        },
        "inventory": {
            "total": int(inventory.get("total", 0) or 0),
            "operational": int(inventory.get("operational", 0) or 0),
            "nightly": int(inventory.get("nightly", 0) or 0),
            "duplicates": int(inventory.get("duplicates", 0) or 0),
        },
        "wheel_api": {
            "status": _text(wheel_api.get("status"), 80),
            "consecutive_failures": int(wheel_api.get("consecutive_failures", 0) or 0),
        },
        "active_incidents": int(details.get("active_incidents", 0) or 0),
        "findings": compact,
    }


def validated_explanation(data: dict[str, Any], rules: dict[str, Any]) -> dict[str, Any]:
    confidence_raw = data.get("confidence")
    confidence = (
        max(0.0, min(1.0, float(confidence_raw)))
        if isinstance(confidence_raw, (int, float)) and not isinstance(confidence_raw, bool)
        else 0.0
    )
    return {
        "summary": _text(data.get("summary"), 900) or rules["summary"],
        "impact": _text(data.get("impact"), 900) or rules["impact"],
        "recommended_action": _text(data.get("recommended_action"), 900)
        or rules["recommended_action"],
        "severity": rules["severity"],
        "requires_human_attention": rules["requires_human_attention"],
        "confidence": confidence,
    }


def inspect(
    details: dict[str, Any],
    findings: Iterable[dict[str, Any]],
    *,
    client: AIClient | None = None,
) -> dict[str, Any]:
    active = active_findings(findings)
    rules = rules_assessment(details, active)
    ai_client = client or client_from_env()
    fallback = {
        "summary": rules["summary"],
        "impact": rules["impact"],
        "recommended_action": rules["recommended_action"],
        "confidence": 1.0,
    }
    result = ai_client.ask_json(
        FEATURE_HEALTH_INSPECTOR,
        system_prompt=(
            "Объясни состояние BB V.G. администратору на русском языке. "
            "Детерминированная диагностика является источником истины и её severity неизменна. "
            "Дай короткое резюме, практический эффект и одно безопасное рекомендуемое действие. "
            "Ответ должен содержать поля summary, impact, recommended_action, confidence."
        ),
        user_input=json.dumps(safe_ai_input(details, active, rules), ensure_ascii=False, sort_keys=True),
        fallback_data=fallback,
    )
    explanation = validated_explanation(result.data or fallback, rules)
    return {
        "version": 1,
        "generated_at": datetime.now(UTC).isoformat(),
        "mode": "ai" if result.ok else "rules",
        "ai_status": result.status,
        "decision_id": result.decision_id,
        "provider": result.provider,
        "model": result.model,
        "cached": result.cached,
        "used_fallback": result.used_fallback,
        **rules,
        **explanation,
    }


def admin_note(insight: dict[str, Any]) -> str:
    if str(insight.get("severity") or "") != "critical":
        return ""
    mode = "AI-анализ" if insight.get("mode") == "ai" else "правила диагностики"
    summary = html.escape(_text(insight.get("summary"), 850))
    action = html.escape(_text(insight.get("recommended_action"), 850))
    return (
        f"🧠 <b>Инспектор здоровья ({mode})</b>\n"
        f"{summary}\n"
        f"<b>Рекомендуемое действие:</b> {action}"
    )[:1900]


def self_test() -> None:
    healthy = rules_assessment(
        {"monitor": {"checked_sources": 5, "reachable_sources": 5, "source_errors": 0}},
        [],
    )
    assert healthy["severity"] == "ok"
    critical = rules_assessment(
        {"monitor": {"checked_sources": 5, "reachable_sources": 0, "source_errors": 5}},
        [{"kind": "all_sources_unreachable", "severity": "critical", "title": "Нет источников", "detail": "test"}],
    )
    assert critical["severity"] == "critical"
    assert critical["requires_human_attention"] is True
    assert admin_note(critical)
    print("AI health inspector self-test passed")


if __name__ == "__main__":
    self_test()
