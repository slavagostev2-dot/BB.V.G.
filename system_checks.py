from __future__ import annotations

import json
import os
import re
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
ADMIN_PANEL_STATUS_PATH = ROOT / "admin_panel_status.json"
HEALTH_PATH = ROOT / "source_health.json"
CHECK_STATE_PATH = ROOT / "system_check_state.json"
SOURCE_TIER_STATE_PATH = ROOT / "source_tier_state.json"
SOURCE_TRANSPORT_STATE_PATH = ROOT / "source_transport_state.json"
SOURCE_STATS_PATH = ROOT / "source_stats.json"
RUNTIME_STATE_PATH = ROOT / "state.json"
PUBLIC_SOURCES_PATH = ROOT / "public_sources.txt"
NIGHTLY_SOURCES_PATH = ROOT / "source_catalog.txt"
DISCOVERY_PATH = ROOT / "discovery_state.json"
INTELLIGENCE_PATH = ROOT / "intelligence_state.json"
MINIAPP_DEPLOYMENT_PATH = ROOT / "miniapp_deployment.json"
MINIAPP_INDEX_PATH = ROOT / "docs" / "index.html"
MINIAPP_APP_PATH = ROOT / "docs" / "app.js"
MINIAPP_CONTROLS_PATH = ROOT / "docs" / "bbvg-controls.js"
MINIAPP_STYLES_PATH = ROOT / "docs" / "styles.css"
ACTIVE_DOMAIN_FILES = (
    ROOT / "monitor.py",
    ROOT / "nightly_discovery.py",
    ROOT / "admin_panel_runtime_v17.py",
    ROOT / "admin_panel_runtime_v21.py",
    ROOT / "docs" / "app.js",
    ROOT / "docs" / "bbvg-controls.js",
    ROOT / "docs" / "views-secondary.js",
)
UTC = timezone.utc
EXPECTED_SOURCE_COUNT = max(1, int(os.getenv("EXPECTED_SOURCE_COUNT", "66")))
MONITOR_MAX_AGE_MINUTES = max(5, int(os.getenv("MONITOR_MAX_AGE_MINUTES", "20")))
ADMIN_PANEL_MAX_AGE_MINUTES = max(
    10, int(os.getenv("ADMIN_PANEL_MAX_AGE_MINUTES", "20"))
)
DISCOVERY_MAX_AGE_HOURS = max(6, int(os.getenv("DISCOVERY_MAX_AGE_HOURS", "48")))
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
    operational_nightly = data_store.operational_sources(nightly, "nightly")
    all_keys = [source.casefold() for source in operational + operational_nightly]
    duplicates = len(all_keys) - len(set(all_keys))
    details["inventory"] = {
        "expected": EXPECTED_SOURCE_COUNT,
        "configured": len(configured),
        "operational": len(operational),
        "nightly": len(nightly),
        "total": len(set(all_keys)),
        "duplicates": duplicates,
        "domain": telegram_transport.PRIMARY_DOMAIN,
    }
    if len(set(all_keys)) < EXPECTED_SOURCE_COUNT:
        findings.append(finding(
            "source_inventory",
            "Неверное количество источников",
            f"Ожидалось не меньше {EXPECTED_SOURCE_COUNT} в общем пуле, найдено {len(set(all_keys))}.",
            severity="critical",
        ))
    if not operational:
        findings.append(finding(
            "source_policy",
            "Основной мониторинг остался без источников",
            "Все утверждённые источники оказались в ночном режиме.",
            severity="critical",
        ))
    if duplicates:
        findings.append(finding(
            "source_duplicates",
            "Обнаружены дубли источников",
            f"Количество повторов: {duplicates}.",
        ))


def check_telegram_web(details: dict[str, Any], findings: list[dict[str, Any]]) -> None:
    candidates = unique_sources(PUBLIC_SOURCES_PATH) + unique_sources(NIGHTLY_SOURCES_PATH)
    probe_source = candidates[0] if candidates else "telegram"
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
    expected_primary = len(data_store.operational_sources(unique_sources(PUBLIC_SOURCES_PATH), "fast"))
    if checked and checked < expected_primary:
        findings.append(finding(
            "monitor_source_count",
            "Основной монитор проверяет не все источники",
            f"В последней итерации проверено {checked} из {expected_primary} источников основного режима.",
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


def check_admin_panel_runtime(
    details: dict[str, Any], findings: list[dict[str, Any]]
) -> None:
    status = load_json(ADMIN_PANEL_STATUS_PATH, {})
    heartbeat = parse_datetime(
        (status.get("last_heartbeat_at") or status.get("started_at"))
        if isinstance(status, dict)
        else None
    )
    panel = {
        "status": status.get("status") if isinstance(status, dict) else None,
        "version": status.get("version") if isinstance(status, dict) else None,
        "started_at": status.get("started_at") if isinstance(status, dict) else None,
        "last_heartbeat_at": (
            status.get("last_heartbeat_at") if isinstance(status, dict) else None
        ),
        "update_consumer": (
            status.get("update_consumer") if isinstance(status, dict) else None
        ),
    }
    details["admin_panel"] = panel
    if heartbeat is None:
        if int(status.get("heartbeat_version", 0) or 0) < 1:
            panel["heartbeat_state"] = "pending_activation"
            return
        findings.append(
            finding(
                "admin_panel_heartbeat_missing",
                "Нет живого сигнала панели управления",
                "Бот не подтвердил работу обработчика кнопок и команд.",
                severity="critical",
            )
        )
        return
    age = now_utc() - heartbeat
    panel["age_minutes"] = max(0, int(age.total_seconds() // 60))
    if status.get("status") != "running":
        findings.append(
            finding(
                "admin_panel_not_running",
                "Панель управления ботом остановлена",
                f"Текущее состояние: {status.get('status') or 'неизвестно'}.",
                severity="critical",
            )
        )
    if age > timedelta(minutes=ADMIN_PANEL_MAX_AGE_MINUTES):
        findings.append(
            finding(
                "admin_panel_stale",
                "Бот давно не принимает команды пользователей",
                f"Последний живой сигнал был {int(age.total_seconds() // 60)} минут назад.",
                severity="critical",
            )
        )


def check_source_health(details: dict[str, Any], findings: list[dict[str, Any]]) -> None:
    health = load_json(HEALTH_PATH, {})
    sources = health.get("sources") if isinstance(health, dict) and isinstance(health.get("sources"), dict) else {}
    primary_sources = unique_sources(PUBLIC_SOURCES_PATH)
    nightly_sources = unique_sources(NIGHTLY_SOURCES_PATH)
    configured = primary_sources + nightly_sources
    configured_keys = {source.casefold(): source for source in configured}
    required_health_keys = {source.casefold(): source for source in primary_sources}
    statuses: dict[str, int] = {}
    problem_sources: list[str] = []
    problem_details: list[dict[str, str]] = []
    transport_buckets = {
        "dns": 0,
        "tls": 0,
        "timeout": 0,
        "http": 0,
        "empty_or_html": 0,
        "other": 0,
    }
    for source, entry in sources.items():
        if not isinstance(entry, dict) or source.casefold() not in configured_keys:
            continue
        status = str(entry.get("status") or "unknown").casefold()
        statuses[status] = statuses.get(status, 0) + 1
        if status in {"ok", "unknown"}:
            continue
        problem_sources.append(str(source))
        failure_code = str(entry.get("failure_code") or "").strip()
        failure_reason = str(entry.get("failure_reason") or "").strip()
        if not failure_code or not failure_reason:
            failure_code, failure_reason = data_store.classify_source_problem(
                "empty" if status == "empty" else "error",
                str(entry.get("last_error") or entry.get("last_transport_error") or ""),
            )
        problem_details.append({
            "source": str(source),
            "status": status,
            "failure_code": failure_code,
            "reason": failure_reason,
        })
        findings.append(finding(
            f"source_{failure_code}",
            f"Источник @{source} не проверяется",
            failure_reason,
            severity="critical" if status == "quarantined" else "warning",
            subject=str(source),
        ))
        error = str(entry.get("last_error") or entry.get("last_transport_error") or "").casefold()
        if "resolve" in error or "dns" in error or "name or service" in error:
            transport_buckets["dns"] += 1
        elif "certificate" in error or "ssl" in error or "tls" in error:
            transport_buckets["tls"] += 1
        elif "timeout" in error:
            transport_buckets["timeout"] += 1
        elif "http" in error or "status" in error:
            transport_buckets["http"] += 1
        elif status in {"empty", "html_changed"}:
            transport_buckets["empty_or_html"] += 1
        else:
            transport_buckets["other"] += 1
    missing = [
        source
        for key, source in required_health_keys.items()
        if not any(str(existing).casefold() == key for existing in sources)
    ]
    nightly_pending = [
        source
        for source in nightly_sources
        if not any(str(existing).casefold() == source.casefold() for existing in sources)
    ]
    details["source_health_summary"] = {
        "records": len(sources),
        "configured_records": len(configured) - len(missing) - len(nightly_pending),
        "required_primary_records": len(primary_sources) - len(missing),
        "statuses": statuses,
        "problem_sources": problem_sources[:30],
        "problem_details": problem_details[:66],
        "transport_failure_types": transport_buckets,
        "missing_sources": missing,
        # A newly added nightly source intentionally waits for the scheduled
        # nightly pass.  That wait is an idle state, not a health incident.
        "nightly_pending_first_check": nightly_pending,
    }
    if missing:
        findings.append(finding(
            "source_health_missing",
            "Не для всех источников есть данные здоровья",
            f"Нет записей для {len(missing)} источников: {', '.join('@' + item for item in missing[:15])}.",
        ))
    quarantined = statuses.get("quarantined", 0)
    if quarantined:
        findings.append(finding(
            "sources_quarantined",
            "Источники попали в карантин",
            f"В карантине: {quarantined}. Источники остаются в общем пуле и будут перепроверяться.",
        ))


def check_discovery_runtime(details: dict[str, Any], findings: list[dict[str, Any]]) -> None:
    discovery = load_json(DISCOVERY_PATH, {})
    intelligence = load_json(INTELLIGENCE_PATH, {})
    summary: dict[str, Any] = {
        "domain": discovery.get("telegram_domain") if isinstance(discovery, dict) else None,
        "active_size": int(discovery.get("active_size", 0) or 0) if isinstance(discovery, dict) else 0,
        "catalog_size": int(discovery.get("catalog_size", 0) or 0) if isinstance(discovery, dict) else 0,
        "discovery_errors": int(discovery.get("error_count", 0) or 0) if isinstance(discovery, dict) else 0,
        "discovery_last_run_at": discovery.get("last_run_at") if isinstance(discovery, dict) else None,
        "intelligence_last_run_at": intelligence.get("last_run_at") if isinstance(intelligence, dict) else None,
        "intelligence_domain": intelligence.get("telegram_domain") if isinstance(intelligence, dict) else None,
        "candidate_count": len(intelligence.get("candidates", {})) if isinstance(intelligence, dict) and isinstance(intelligence.get("candidates"), dict) else 0,
        "intelligence_summary": intelligence.get("last_run_summary", {}) if isinstance(intelligence, dict) and isinstance(intelligence.get("last_run_summary"), dict) else {},
    }
    details["discovery"] = summary
    if summary["domain"] != telegram_transport.PRIMARY_DOMAIN:
        findings.append(finding(
            "discovery_domain",
            "Поиск источников использует неверный домен",
            f"Ожидался {telegram_transport.PRIMARY_DOMAIN}, записано {summary['domain'] or 'нет данных'}.",
            severity="critical",
        ))
    if summary["intelligence_domain"] != telegram_transport.PRIMARY_DOMAIN:
        findings.append(finding(
            "intelligence_domain",
            "Разведка новых источников использует неверный домен",
            f"Ожидался {telegram_transport.PRIMARY_DOMAIN}, записано {summary['intelligence_domain'] or 'нет данных'}.",
            severity="critical",
        ))
    discovered_pool = summary["active_size"] + summary["catalog_size"]
    if discovered_pool and discovered_pool < EXPECTED_SOURCE_COUNT:
        findings.append(finding(
            "discovery_inventory",
            "Ночная проверка видит не весь утверждённый пул",
            f"В состоянии поиска записано {discovered_pool}, ожидается не меньше {EXPECTED_SOURCE_COUNT}.",
        ))
    intelligence_summary = summary["intelligence_summary"]
    scanned = int(intelligence_summary.get("sources_scanned", 0) or 0)
    intelligence_errors = int(intelligence_summary.get("errors", 0) or 0)
    if intelligence_errors or (intelligence_summary and scanned < EXPECTED_SOURCE_COUNT):
        findings.append(finding(
            "discovery_scan_failure",
            "Поиск новых источников не смог просканировать базу",
            f"Просканировано {scanned} из {EXPECTED_SOURCE_COUNT}; ошибок {intelligence_errors}. "
            f"Поиск должен выполняться через {telegram_transport.PRIMARY_DOMAIN}.",
            severity="critical",
        ))
    for label, raw in (
        ("поиск кандидатов", summary["discovery_last_run_at"]),
        ("разведка упоминаний", summary["intelligence_last_run_at"]),
    ):
        timestamp = parse_datetime(raw)
        if timestamp is None or now_utc() - timestamp > timedelta(hours=DISCOVERY_MAX_AGE_HOURS):
            age_text = "нет времени запуска" if timestamp is None else f"старше {DISCOVERY_MAX_AGE_HOURS} ч."
            findings.append(finding(
                f"discovery_stale_{label.split()[0]}",
                f"Давно не обновлялся {label}",
                age_text,
            ))


def check_domain_compliance(details: dict[str, Any], findings: list[dict[str, Any]]) -> None:
    offenders: list[str] = []
    for path in ACTIVE_DOMAIN_FILES:
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            offenders.append(f"{path.relative_to(ROOT)}: отсутствует")
            continue
        if "https://t.me/" in text:
            offenders.append(str(path.relative_to(ROOT)))
    details["domain_compliance"] = {
        "primary": telegram_transport.PRIMARY_DOMAIN,
        "checked_files": [str(path.relative_to(ROOT)) for path in ACTIVE_DOMAIN_FILES],
        "legacy_url_offenders": offenders,
    }
    if offenders:
        findings.append(finding(
            "legacy_domain_in_runtime",
            "В рабочем коде остались ссылки на заблокированный домен",
            ", ".join(offenders),
            severity="critical",
        ))


def check_miniapp_release(details: dict[str, Any], findings: list[dict[str, Any]]) -> None:
    deployment = load_json(MINIAPP_DEPLOYMENT_PATH, {})
    try:
        index = MINIAPP_INDEX_PATH.read_text(encoding="utf-8")
        app_source = MINIAPP_APP_PATH.read_text(encoding="utf-8")
        controls_source = MINIAPP_CONTROLS_PATH.read_text(encoding="utf-8")
        styles_source = MINIAPP_STYLES_PATH.read_text(encoding="utf-8")
    except OSError as exc:
        details["miniapp"] = {"ok": False, "error": str(exc)}
        findings.append(finding(
            "miniapp_files_missing",
            "Не найдены файлы Mini App",
            str(exc),
            severity="critical",
        ))
        return
    version_match = re.search(r"const VERSION='([^']+)'", app_source)
    version = version_match.group(1) if version_match else ""
    assets = re.findall(r"(?:app\.js|styles\.css)\?v=([0-9.]+)", index)
    features = {
        "light_theme": all(marker in app_source for marker in ("lightTheme", "applyTheme", "setHeaderColor", "setBackgroundColor", "setBottomBarColor")),
        "haptics": "HapticFeedback" in app_source and "app.settings.haptics" in app_source,
        "unified_notifications": "data-action=\"notifications\"" in app_source or "data-action=\"notifications\"" in (ROOT / "docs" / "bbvg-controls.js").read_text(encoding="utf-8"),
        "unified_sources": "[...app.data.primary,...app.data.nightly]" in controls_source and "data-source-mode=\"nightly\"" not in controls_source,
        "admin_ratings": "adminRatingsActive" in app_source and "quality_score" in app_source,
        "responsive_charts": "--chart-columns" in styles_source and ".chart-30" in styles_source,
        "participation_history": "participationHistory" in app_source and "Всего участий" in controls_source,
        "single_splash_art": index.count("splash-3d.webp") >= 2 and "splash-progress" not in index,
    }
    url = str(deployment.get("url") or "") if isinstance(deployment, dict) else ""
    details["miniapp"] = {
        "version": version,
        "asset_versions": assets,
        "deployment_status": deployment.get("status") if isinstance(deployment, dict) else None,
        "deployment_url": url,
        "features": features,
    }
    if not version or not assets or any(asset != version for asset in assets):
        findings.append(finding(
            "miniapp_cache_version",
            "Версии ресурсов Mini App не совпадают",
            f"app={version or 'не найдена'}, assets={assets or 'не найдены'}.",
            severity="critical",
        ))
    missing = [name for name, enabled in features.items() if not enabled]
    if missing:
        findings.append(finding(
            "miniapp_features",
            "В Mini App отсутствуют обязательные функции",
            ", ".join(missing),
            severity="critical",
        ))
    if deployment.get("status") != "deployed" or not url.startswith("https://slavagostev2-betboom-monitor.pages.dev/"):
        findings.append(finding(
            "miniapp_deployment",
            "Mini App открывается не с актуального домена",
            f"status={deployment.get('status')}; url={url or 'не задан'}.",
            severity="critical",
        ))


def check_notification_routing(details: dict[str, Any], findings: list[dict[str, Any]]) -> None:
    config, exists = notification_router.load_config()
    admins = notification_router.recipients(config, exists, "admin")
    users = notification_router.recipients(config, exists, "user")
    admin_kinds = {
        kind: notification_router.recipients(config, exists, kind)
        for kind in sorted(notification_router.ADMIN_NOTIFICATION_KINDS)
    }
    user_kinds = {
        kind: notification_router.recipients(config, exists, kind)
        for kind in sorted(notification_router.USER_NOTIFICATION_KINDS)
    }
    details["notification_routing"] = {
        "admin_recipients": admins,
        "user_recipients": users,
        "admin_kinds": admin_kinds,
        "user_kinds": user_kinds,
        "error_category": notification_router.classify("⚠️ Ошибка BB V.G."),
        "error_kind": notification_router.notification_kind("⚠️ Ошибка BB V.G."),
        "duplicate_window_seconds": notification_router.DELIVERY_DEDUP_SECONDS,
    }
    if notification_router.classify("⚠️ Ошибка BB V.G.") != "admin":
        findings.append(finding(
            "notification_routing",
            "Ошибки могут попасть обычным пользователям",
            "Маршрутизатор не классифицировал тестовое сообщение об ошибке как административное.",
            severity="critical",
        ))
    admin_ids = notification_router.admin_user_ids(config)
    for kind, targets in admin_kinds.items():
        for chat_id in targets:
            user_id, _ = notification_router.user_for_chat(config, chat_id)
            if user_id and user_id not in admin_ids:
                findings.append(finding(
                    "non_admin_error_recipient",
                    "Обычный пользователь включён в получателей ошибок",
                    f"Chat ID {chat_id} не имеет роли администратора, категория {kind}.",
                    severity="critical",
                    subject=chat_id,
                ))


def check_automation_state(details: dict[str, Any], findings: list[dict[str, Any]]) -> None:
    tier = load_json(SOURCE_TIER_STATE_PATH, {})
    transport = load_json(SOURCE_TRANSPORT_STATE_PATH, {})
    tier_at = parse_datetime(tier.get("last_run_at") if isinstance(tier, dict) else None)
    transport_at = parse_datetime(transport.get("checked_at") if isinstance(transport, dict) else None)
    details["automation_state"] = {
        "source_tier_policy": tier.get("policy") if isinstance(tier, dict) else None,
        "source_tier_last_run_at": tier.get("last_run_at") if isinstance(tier, dict) else None,
        "transport_status": transport.get("status") if isinstance(transport, dict) else None,
        "transport_checked_at": transport.get("checked_at") if isinstance(transport, dict) else None,
        "transport_domain": transport.get("domain") if isinstance(transport, dict) else None,
        "transport_accounted_sources": transport.get("accounted_sources") if isinstance(transport, dict) else None,
    }
    if tier.get("policy") != "seven_day_dynamic_primary_and_nightly":
        findings.append(finding(
            "source_tier_policy_stale",
            "Не включён автоматический ночной режим источников",
            "Состояние обслуживания не подтверждает перенос после 7 полных дней без колёс.",
            severity="critical",
        ))
    if tier_at is None or now_utc() - tier_at > timedelta(hours=36):
        findings.append(finding(
            "source_tier_maintenance_stale",
            "Давно не запускалось обслуживание режимов источников",
            "Нет свежего запуска за последние 36 часов.",
        ))
    if (
        transport.get("status") != "success"
        or transport.get("domain") != telegram_transport.PRIMARY_DOMAIN
        or int(transport.get("accounted_sources", 0) or 0) < EXPECTED_SOURCE_COUNT
    ):
        findings.append(finding(
            "source_transport_smoke",
            "Полная транспортная проверка 66 источников не подтверждена",
            f"status={transport.get('status')}; domain={transport.get('domain')}; accounted={transport.get('accounted_sources')}.",
            severity="critical",
        ))
    if transport_at is None or now_utc() - transport_at > timedelta(hours=36):
        findings.append(finding(
            "source_transport_stale",
            "Давно не выполнялась полная проверка 66 источников",
            "Нет свежего транспортного прогона за последние 36 часов.",
        ))


def check_rating_consistency(details: dict[str, Any], findings: list[dict[str, Any]]) -> None:
    stats = load_json(SOURCE_STATS_PATH, {})
    state = load_json(RUNTIME_STATE_PATH, {})
    decisions = stats.get("admin_wheel_decisions") if isinstance(stats, dict) else {}
    decisions = decisions if isinstance(decisions, dict) else {}
    expected: dict[str, int] = {}
    inactive_decisions: list[str] = []
    for wheel, entry in decisions.items():
        if not isinstance(entry, dict):
            continue
        verdict = str(entry.get("decision") or "")
        points = 40 if verdict == "confirmed" else -45 if verdict == "inactive" else 0
        if verdict == "inactive":
            inactive_decisions.append(str(wheel).casefold())
        for source in entry.get("sources", []):
            key = str(source).casefold()
            if key:
                expected[key] = expected.get(key, 0) + points
    actual = {
        str(source).casefold(): int(entry.get("quality_score", 0) or 0)
        for source, entry in stats.get("sources", {}).items()
        if isinstance(entry, dict) and (entry.get("quality_score") is not None)
    }
    mismatches = sorted(
        key for key in set(expected) | set(actual) if expected.get(key, 0) != actual.get(key, 0)
    )
    active_keys = {str(key).casefold() for key in state.get("active_wheels", {})}
    participating_keys = {str(key).casefold() for key in state.get("participating_wheels", {})}
    inactive_leaks = sorted(set(inactive_decisions) & (active_keys | participating_keys))
    details["rating_consistency"] = {
        "administrator_decisions": len(decisions),
        "rated_sources": len(expected),
        "score_mismatches": mismatches[:30],
        "inactive_wheel_leaks": inactive_leaks[:30],
    }
    if mismatches:
        findings.append(finding(
            "rating_score_mismatch",
            "Рейтинг источников не совпадает с решениями администратора",
            f"Несовпадения: {', '.join('@' + item for item in mismatches[:15])}.",
            severity="critical",
        ))
    if inactive_leaks:
        findings.append(finding(
            "inactive_wheel_leak",
            "Неактивное колесо осталось в пользовательских списках",
            f"Колёса: {', '.join(inactive_leaks[:15])}.",
            severity="critical",
        ))


def deliver_pending_notifications(state: dict[str, Any], details: dict[str, Any]) -> None:
    opened = incident_manager.pending_open(state)
    resolved = incident_manager.pending_resolved(state)
    delivery = {
        "opened": len(opened),
        "resolved": len(resolved),
        "digest_sent": False,
        "messages_attempted": 1 if opened or resolved else 0,
    }
    if opened or resolved:
        try:
            monitor.send_message(incident_manager.format_digest_message(opened, resolved))
        except Exception as exc:
            delivery["error"] = f"{type(exc).__name__}: {exc}"[:1000]
        else:
            if opened:
                incident_manager.mark_notified(
                    [str(entry.get("key")) for entry in opened], "open"
                )
            if resolved:
                incident_manager.mark_notified(
                    [str(entry.get("key")) for entry in resolved], "resolved"
                )
            delivery["digest_sent"] = True
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
    check_admin_panel_runtime(details, findings)
    check_source_health(details, findings)
    check_discovery_runtime(details, findings)
    check_domain_compliance(details, findings)
    check_miniapp_release(details, findings)
    check_notification_routing(details, findings)
    check_automation_state(details, findings)
    check_rating_consistency(details, findings)
    state = incident_manager.reconcile(findings, scope=SCOPE)
    details["active_incidents"] = int(state.get("active_count", 0) or 0)
    details["incident_sequence"] = int(state.get("sequence", 0) or 0)
    deliver_pending_notifications(state, details)
    details["status"] = "ok" if not findings else "degraded"
    details["finding_count"] = len(findings)
    details["findings"] = findings
    details["gpt_diagnostic_snapshot"] = {
        "single_source": "system_check_state.json",
        "status": details["status"],
        "domain": telegram_transport.PRIMARY_DOMAIN,
        "configured_sources": details.get("inventory", {}).get("total", 0),
        "checked_sources": details.get("monitor", {}).get("checked_sources", 0),
        "reachable_sources": details.get("monitor", {}).get("reachable_sources", 0),
        "bot_panel_heartbeat": details.get("admin_panel", {}).get("last_heartbeat_at"),
        "active_incidents": details["active_incidents"],
    }
    details["check_matrix"] = {
        "inventory": "ok" if not any(item["kind"] in {"source_inventory", "source_policy", "source_duplicates"} for item in findings) else "failed",
        "telegram_transport": "ok" if not any(str(item["kind"]).startswith("telegram_") or item["kind"] == "legacy_domain_redirect" for item in findings) else "failed",
        "monitor": "ok" if not any(str(item["kind"]).startswith("monitor_") or item["kind"] in {"all_sources_unreachable", "partial_source_failure"} for item in findings) else "failed",
        "bot_panel": "ok" if not any(str(item["kind"]).startswith("admin_panel_") for item in findings) else "failed",
        "source_health": "ok" if not any(str(item["kind"]).startswith("source_") or item["kind"] == "sources_quarantined" for item in findings) else "failed",
        "discovery": "ok" if not any(str(item["kind"]).startswith("discovery_") for item in findings) else "failed",
        "miniapp": "ok" if not any(str(item["kind"]).startswith("miniapp_") for item in findings) else "failed",
        "notifications": "ok" if not any(item["kind"] in {"notification_routing", "non_admin_error_recipient"} for item in findings) else "failed",
        "automations": "ok" if not any(str(item["kind"]).startswith("source_tier_") or str(item["kind"]).startswith("source_transport_") for item in findings) else "failed",
        "ratings": "ok" if not any(item["kind"] in {"rating_score_mismatch", "inactive_wheel_leak"} for item in findings) else "failed",
    }
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
    assert data_store.classify_source_problem("error", "HTTP 404")[0] == "removed_or_renamed"
    print("system_checks self-test passed")


if __name__ == "__main__":
    if "--self-test" in os.sys.argv:
        self_test()
    else:
        raise SystemExit(main())
