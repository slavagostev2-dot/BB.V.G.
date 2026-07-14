from __future__ import annotations

import json
import os
import ssl
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import requests

import incident_manager
import monitor
import monitor_data as data_store
import notification_router
import telegram_transport

ROOT = Path(__file__).resolve().parent
STATUS_PATH = ROOT / "monitor_status.json"
HEALTH_PATH = ROOT / "source_health.json"
CHECK_STATE_PATH = ROOT / "system_check_state.json"
PUBLIC_SOURCES_PATH = ROOT / "public_sources.txt"
NIGHTLY_SOURCES_PATH = ROOT / "source_catalog.txt"
UTC = timezone.utc
EXPECTED_SOURCE_COUNT = max(1, int(os.getenv("EXPECTED_SOURCE_COUNT", "66")))
MONITOR_MAX_AGE_MINUTES = max(5, int(os.getenv("MONITOR_MAX_AGE_MINUTES", "20")))
SCOPE = "system_checks"

notification_router.install(monitor)
telegram_transport.install(monitor)


def now_utc() -> datetime:
    return datetime.now(UTC)


def load_json(path: Path, default: Any) -> Any:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return default
    return value


def save_json(path: Path, value: dict[str, Any]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def unique_sources(path: Path) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for source in monitor.read_list(path):
        clean = str(source).strip().lstrip("@")
        if clean and clean.casefold() not in seen:
            seen.add(clean.casefold())
            result.append(clean)
    return result


def finding(kind: str, title: str, detail: str, *, severity: str = "warning", subject: str = "") -> dict[str, Any]:
    return {
        "kind": kind,
        "title": title,
        "detail": detail,
        "severity": severity,
        "subject": subject,
    }


def parse_datetime(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.astimezone(UTC) if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def classify_transport_error(exc: BaseException) -> tuple[str, str]:
    text = f"{type(exc).__name__}: {exc}"
    lowered = text.casefold()
    if isinstance(exc, requests.exceptions.SSLError) or isinstance(exc, ssl.SSLError) or "certificate" in lowered or "tls" in lowered:
        return "telegram_tls", text
    if "resolve" in lowered or "name or service not known" in lowered or "dns" in lowered:
        return "telegram_dns", text
    if isinstance(exc, (requests.Timeout, TimeoutError)) or "timeout" in lowered:
        return "telegram_timeout", text
    if "403" in lowered or "451" in lowered or "blocked" in lowered:
        return "telegram_access_blocked", text
    return "telegram_transport", text


def check_inventory(details: dict[str, Any], findings: list[dict[str, Any]]) -> None:
    configured = unique_sources(PUBLIC_SOURCES_PATH)
    nightly = unique_sources(NIGHTLY_SOURCES_PATH)
    operational = data_store.operational_sources(configured, "fast")
    duplicates = len(configured) - len({source.casefold() for source in configured})
    details["inventory"] = {
        "expected": EXPECTED_SOURCE_COUNT,
        "configured": len(configured),
        "operational": len(operational),
        "nightly": len(nightly),
        "duplicates": duplicates,
        "domain": telegram_transport.PRIMARY_DOMAIN,
    }
    if len(configured) != EXPECTED_SOURCE_COUNT:
        findings.append(finding(
            "source_inventory",
            "Неверное количество источников",
            f"Ожидалось {EXPECTED_SOURCE_COUNT}, в public_sources.txt найдено {len(configured)}.",
            severity="critical",
        ))
    if len(operational) != EXPECTED_SOURCE_COUNT:
        findings.append(finding(
            "source_policy",
            "Не все источники включены в постоянную проверку",
            f"Постоянно проверяется {len(operational)} из {EXPECTED_SOURCE_COUNT}; ночная/внутренняя фильтрация должна быть отключена.",
            severity="critical",
        ))
    if nightly:
        findings.append(finding(
            "nightly_sources_remaining",
            "Остались источники в отдельной ночной базе",
            f"В source_catalog.txt осталось {len(nightly)} источников: {', '.join('@' + item for item in nightly[:12])}.",
        ))
    if duplicates:
        findings.append(finding(
            "source_duplicates",
            "Обнаружены дубли источников",
            f"Количество повторов: {duplicates}.",
        ))


def check_telegram_web(details: dict[str, Any], findings: list[dict[str, Any]]) -> None:
    probe_source = unique_sources(PUBLIC_SOURCES_PATH)[0] if unique_sources(PUBLIC_SOURCES_PATH) else "telegram"
    url = telegram_transport.public_source_url(probe_source)
    result: dict[str, Any] = {"source": probe_source, "url": url}
    try:
        response = monitor.request_with_retries(
            "GET",
            url,
            attempts=2,
            timeout=monitor.REQUEST_TIMEOUT,
            headers={"User-Agent": monitor.USER_AGENT},
            allow_redirects=True,
        )
        result["status_code"] = response.status_code
        result["final_url"] = response.url
        hostname = (urlsplit(str(response.url)).hostname or "").casefold()
        if hostname in {"t.me", "www.t.me"}:
            findings.append(finding(
                "legacy_domain_redirect",
                "Telegram снова перенаправил запрос на заблокированный домен",
                f"Проверка {url} завершилась на {response.url}.",
                severity="critical",
            ))
        if response.status_code in {401, 403, 451}:
            findings.append(finding(
                "telegram_access_blocked",
                "Доступ к Telegram Web ограничен",
                f"{url} вернул HTTP {response.status_code}.",
                severity="critical",
            ))
        elif response.status_code >= 500:
            findings.append(finding(
                "telegram_http_5xx",
                "Telegram Web временно недоступен",
                f"{url} вернул HTTP {response.status_code}.",
            ))
        elif response.status_code >= 400:
            findings.append(finding(
                "telegram_http_error",
                "Telegram Web вернул ошибку",
                f"{url} вернул HTTP {response.status_code}.",
            ))
        else:
            response.raise_for_status()
            has_messages = "tgme_widget_message" in response.text
            result["html_messages_detected"] = has_messages
            if not has_messages:
                findings.append(finding(
                    "telegram_html_changed",
                    "Изменилась структура страницы Telegram",
                    f"Страница @{probe_source} открылась, но блоки сообщений не найдены.",
                    severity="critical",
                ))
    except Exception as exc:
        kind, text = classify_transport_error(exc)
        findings.append(finding(
            kind,
            "Не работает подключение к новому домену Telegram",
            f"{telegram_transport.PRIMARY_DOMAIN}: {text[:900]}",
            severity="critical",
            subject=telegram_transport.PRIMARY_DOMAIN,
        ))
        result["error"] = text[:1000]
    details["telegram_web"] = result


def check_bot_api(details: dict[str, Any], findings: list[dict[str, Any]]) -> None:
    if not os.getenv("BOT_TOKEN"):
        findings.append(finding(
            "bot_token_missing",
            "Не задан токен Telegram-бота",
            "В workflow отсутствует BOT_TOKEN.",
            severity="critical",
        ))
        details["bot_api"] = {"ok": False, "error": "BOT_TOKEN missing"}
        return
    try:
        payload = monitor.telegram_api("getMe", {})
        username = str((payload.get("result") or {}).get("username") or "")
        details["bot_api"] = {"ok": True, "username": username}
    except Exception as exc:
        details["bot_api"] = {"ok": False, "error": f"{type(exc).__name__}: {exc}"[:1000]}
        findings.append(finding(
            "bot_api",
            "Telegram Bot API недоступен",
            f"{type(exc).__name__}: {exc}"[:900],
            severity="critical",
        ))


def check_monitor_runtime(details: dict[str, Any], findings: list[dict[str, Any]]) -> None:
    status = load_json(STATUS_PATH, {})
    health = load_json(HEALTH_PATH, {})
    details["monitor"] = status if isinstance(status, dict) else {}
    last_iteration = parse_datetime(status.get("last_iteration_at") if isinstance(status, dict) else None)
    if last_iteration is None:
        findings.append(finding(
            "monitor_status_missing",
            "Нет данных о работе основного монитора",
            "monitor_status.json не содержит завершённой итерации.",
            severity="critical",
        ))
    else:
        age = now_utc() - last_iteration
        details["monitor_age_minutes"] = int(age.total_seconds() // 60)
        if age > timedelta(minutes=MONITOR_MAX_AGE_MINUTES):
            findings.append(finding(
                "monitor_stale",
                "Основной монитор давно не обновлялся",
                f"Последняя итерация была {int(age.total_seconds() // 60)} минут назад.",
                severity="critical",
            ))
    checked = int(status.get("checked_sources", 0) or 0) if isinstance(status, dict) else 0
    reachable = int(status.get("reachable_sources", 0) or 0) if isinstance(status, dict) else 0
    source_errors = int(status.get("source_errors", 0) or 0) if isinstance(status, dict) else 0
    if checked and checked != EXPECTED_SOURCE_COUNT:
        findings.append(finding(
            "monitor_source_count",
            "Основной монитор проверяет не все источники",
            f"В последней итерации проверено {checked} из {EXPECTED_SOURCE_COUNT}.",
            severity="critical",
        ))
    if checked and reachable == 0:
        findings.append(finding(
            "all_sources_unreachable",
            "Недоступны все Telegram-источники",
            f"Проверено {checked}, доступно 0, ошибок {source_errors}.",
            severity="critical",
        ))
    elif checked and reachable < checked:
        sources = health.get("sources") if isinstance(health, dict) and isinstance(health.get("sources"), dict) else {}
        bad = [
            str(source) for source, entry in sources.items()
            if isinstance(entry, dict) and str(entry.get("status") or "") not in {"ok", ""}
        ]
        findings.append(finding(
            "partial_source_failure",
            "Часть Telegram-источников недоступна",
            f"Доступно {reachable} из {checked}. Проблемные: {', '.join('@' + item for item in bad[:15]) or 'см. source_health.json'}.",
        ))


def check_notification_routing(details: dict[str, Any], findings: list[dict[str, Any]]) -> None:
    config, exists = notification_router.load_config()
    admins = notification_router.recipients(config, exists, "admin")
    users = notification_router.recipients(config, exists, "user")
    details["notification_routing"] = {
        "admin_recipients": admins,
        "user_recipients": users,
        "error_category": notification_router.classify("⚠️ Ошибка BB V.G."),
    }
    if notification_router.classify("⚠️ Ошибка BB V.G.") != "admin":
        findings.append(finding(
            "notification_routing",
            "Ошибки могут попасть обычным пользователям",
            "Маршрутизатор не классифицировал тестовое сообщение об ошибке как административное.",
            severity="critical",
        ))
    admin_ids = notification_router.admin_user_ids(config)
    for chat_id in admins:
        user_id, _ = notification_router.user_for_chat(config, chat_id)
        if user_id and user_id not in admin_ids:
            findings.append(finding(
                "non_admin_error_recipient",
                "Обычный пользователь включён в получателей ошибок",
                f"Chat ID {chat_id} не имеет роли администратора.",
                severity="critical",
                subject=chat_id,
            ))


def deliver_pending_notifications(state: dict[str, Any], details: dict[str, Any]) -> None:
    opened = incident_manager.pending_open(state)
    resolved = incident_manager.pending_resolved(state)
    delivery = {"opened": len(opened), "resolved": len(resolved), "open_sent": False, "resolved_sent": False}
    if opened:
        try:
            monitor.send_message(incident_manager.format_open_message(opened))
        except Exception as exc:
            delivery["open_error"] = f"{type(exc).__name__}: {exc}"[:1000]
        else:
            incident_manager.mark_notified([str(entry.get("key")) for entry in opened], "open")
            delivery["open_sent"] = True
    if resolved:
        try:
            monitor.send_message(incident_manager.format_resolved_message(resolved))
        except Exception as exc:
            delivery["resolved_error"] = f"{type(exc).__name__}: {exc}"[:1000]
        else:
            incident_manager.mark_notified([str(entry.get("key")) for entry in resolved], "resolved")
            delivery["resolved_sent"] = True
    details["incident_delivery"] = delivery


def main() -> int:
    findings: list[dict[str, Any]] = []
    details: dict[str, Any] = {
        "version": 1,
        "checked_at": now_utc().isoformat(),
        "primary_telegram_domain": telegram_transport.PRIMARY_DOMAIN,
    }
    check_inventory(details, findings)
    check_telegram_web(details, findings)
    check_bot_api(details, findings)
    check_monitor_runtime(details, findings)
    check_notification_routing(details, findings)
    state = incident_manager.reconcile(findings, scope=SCOPE)
    details["active_incidents"] = int(state.get("active_count", 0) or 0)
    details["incident_sequence"] = int(state.get("sequence", 0) or 0)
    deliver_pending_notifications(state, details)
    details["status"] = "ok" if not findings else "degraded"
    details["finding_count"] = len(findings)
    details["findings"] = findings
    save_json(CHECK_STATE_PATH, details)
    print(
        f"BB V.G. system checks: status={details['status']}; "
        f"findings={len(findings)}; sequence={details['incident_sequence']}"
    )
    return 0


def self_test() -> None:
    assert classify_transport_error(requests.exceptions.SSLError("certificate"))[0] == "telegram_tls"
    assert classify_transport_error(requests.exceptions.ConnectTimeout("timeout"))[0] == "telegram_timeout"
    assert finding("x", "y", "z")["kind"] == "x"
    print("system_checks self-test passed")


if __name__ == "__main__":
    if "--self-test" in os.sys.argv:
        self_test()
    else:
        raise SystemExit(main())
