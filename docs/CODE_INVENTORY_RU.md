# Инвентарь кода и структуры BB V.G.

> Статус: **этап 1 глобальной ревизии и этап 2A стабилизации baseline CI завершены**.
> Дата фиксации: 20.07.2026.
> Актуальный `main`, с которым синхронизирована рабочая ветка аудита: `e91f7608b4377bd8bbdf539d75c266c1278084db`.
> Исходная rollback-точка: `backup/before-global-repository-cleanup-2026-07-20`, созданная от SHA `1da3115319305fa5e237cd90124186c12ab98753`.

Документ фиксирует фактических владельцев production-поведения, compatibility/legacy-слои, workflows, runtime-state, архив и кандидатов на последующую очистку. Статус CANDIDATE не означает разрешение на немедленное удаление.

## 1. Статусы

- **PRODUCTION** — используется текущим runtime/workflow.
- **OPERATIONAL** — эксплуатационная, backup, recovery или maintenance-функция.
- **VALIDATION** — CI/test/acceptance-контракт.
- **COMPATIBILITY** — старое имя или слой, который всё ещё удерживается ссылками.
- **LEGACY** — историческая реализация, не владеющая текущим production-поведением.
- **ARCHIVE** — замороженная функциональность.
- **STATE** — изменяемое состояние/диагностика.
- **CANDIDATE** — кандидат на перенос, объединение, переименование или удаление после проверки.

## 2. Telegram Control Center

### PRODUCTION

- `.github/workflows/admin-bot.yml` — основной long-running workflow.
- `admin_panel_runtime_v41.py` — фактическая production-команда workflow.
- `bbvg/bot/runtime.py` — стабильный `TelegramPanelRuntime`; production MRO не наследует `admin_panel_runtime_v*`.
- `bbvg/bot/foundation.py` — фундамент runtime.
- `bbvg/bot/interface.py` — экраны и навигация.
- `bbvg/bot/users.py` — пользователи, роли и настройки.
- `bbvg/bot/sources.py` — источники.
- `bbvg/bot/source_requests.py` — заявки на источники.
- `bbvg/bot/storage.py` — encrypted private state.
- `bbvg/bot/wheels.py` — wheel UI/callback.
- `bbvg/bot/profile.py` — профиль.
- `bbvg/bot/natural_language_admin.py` — AI admin interface.
- `admin_runtime.py` — runtime-операции источников.
- `admin_action_queue.py` — очередь административных действий.

### Подтверждённое расхождение

`admin_panel_runtime_v41.py` ранее описывался как тонкий compatibility entrypoint, но фактически содержит собственную production-логику: меню, фильтрацию callback, аналитику, удаление/стирание пользователей и callback участия.

**Статус:** PRODUCTION + COMPATIBILITY + CANDIDATE. Цель следующего cleanup-блока — перенести уникальную логику в тематические `bbvg/bot/*`, сохранив UI и callback-контракты.

## 3. Исторические runtime-слои панели

### Legacy Mini App-era chain

В текущем репозитории подтверждена связанная наследованием цепочка как минимум `v16 → v17 → v18 → v19 → v20 → v21 → v22 → v23 → v24`. Эти файлы содержат старые Mini App URL и интерфейсные контракты. `v25` эту MRO-цепочку уже не продолжает и строится от `bbvg.bot.storage.PrivateStateRuntime`.

**Статус `v16–v24`:** LEGACY / CANDIDATE. Перед удалением нужна финальная проверка всех внешних ссылок и recovery-contracts.

### Bot-only compatibility chain

Текущие validation/recovery workflows явно компилируют:

`v25`, `v26`, `v28`, `v29`, `v30`, `v31`, `v32`, `v36`, `v37`, `v38`, `v41`.

Подтверждённая лестница наследования: `v25 → v26 → v28 → v29 → v30 → v31 → v32 → v36 → v37 → v38`.

Стабильный `bbvg.bot.runtime.TelegramPanelRuntime` уже не использует эту MRO-лестницу, но CI/recovery продолжает удерживать файлы.

**Статус `v25–v38`:** COMPATIBILITY / VALIDATION-RETAINED / CANDIDATE.

### Отдельный монолит

`admin_panel_v2.py` — старая крупная реализация панели; текущий `v22-checks.yml` всё ещё компилирует файл, хотя production entrypoint им не является.

**Статус:** LEGACY / VALIDATION-RETAINED / CANDIDATE.

## 4. Мониторинг колёс

`.github/workflows/monitor.yml` запускает цикл через `python bbvg_monitor_main.py`.

Активные компоненты:

- `bbvg_monitor_main.py`;
- `monitor.py`;
- `monitor_entry.py`;
- `bbvg_monitor_runtime.py`;
- `monitor_data.py`;
- `monitor_health.py`;
- `telegram_transport.py`;
- `telegram_post_links_v2.py`;
- `recurring_wheel_events.py`;
- `wheel_event_runtime.py`;
- `wheel_metadata_quality.py`;
- `wheel_publications_v2.py`;
- `wheel_lifecycle_v2.py`;
- `wheel_link_lifecycle.py`;
- `personal_reminder_filter.py`;
- `personal_wheel_voting.py`;
- `notification_preferences_v2.py`;
- `notification_navigation.py`;
- `notification_integrity_v2.py`;
- `rating_policy.py`;
- `chapter2_unified_logic.py`.

Production monitor собирается через install/patch-модули и проверяет владельца функций через `__module__`. Поэтому `*_v2` здесь — PRODUCTION + CANDIDATE FOR CONSOLIDATION, а не прямые кандидаты на удаление.

## 5. System Health

- `.github/workflows/system-health.yml` — PRODUCTION.
- `system_checks.py` — базовый legacy health layer.
- `system_checks_v2.py` — подменяет проверки базового слоя.
- `system_checks_v3.py` — production entrypoint и дополнительная подмена discovery/delivery logic.
- `incident_manager.py` — lifecycle инцидентов.
- `bbvg/health_inspector.py` — AI health-inspector.
- `bbvg/ai_core.py` — AI infrastructure.

Фактическая цепочка: `system_checks_v3.py → system_checks_v2.py → system_checks.py`.

**Статус:** PRODUCTION + CANDIDATE FOR CONSOLIDATION с высоким приоритетом.

## 6. Источники Telegram

- `public_sources.txt` — основной утверждённый tier.
- `source_catalog.txt` — вручную утверждённый nightly tier.
- `partners_catalog.json` — partner metadata.
- `identifier_sources.json` — mappings.
- `source_registry.py` — registry.
- `source_intelligence.py`, `source_intelligence_entry.py`, `source_intelligence_alerts.py` — intelligence.
- `nightly_discovery.py`, `nightly_discovery_entry.py` — nightly discovery.
- `source_transport_smoke.py` — transport verification.
- `source_tier_maintenance.py` — audit logic.
- `source_tier_maintenance_v2.py` — текущий workflow entrypoint; COMPATIBILITY / CANDIDATE.

### Stale workflow names

- `activate-66-sources.yml` фактически выполняет `Verify configured Telegram source transport`.
- `migrate-all-sources.yml` всё ещё говорит про 66 источников, но проверяет текущий пул и требует только `>= 66`.

Оба файла — NAMING CANDIDATE; переименование требует синхронного обновления dispatch/test/self-path ссылок.

## 7. Auto-participation

PRODUCTION:

- `.github/workflows/auto-participation.yml`;
- `betboom_auto_participation.py`;
- `auto_participation_dispatch.py`;
- `auto_participation_worker.py`.

### Broken probe path

Workflow имеет optional `probe` и пытается использовать `auto_participation_probe.py`, `auto_participation_probe.trigger` и `auto_participation_probe_result.json`. Все три пути отсутствовали в актуальном `main` при проверке этапа 1.

Обычный режим от probe не зависит, но ручной probe-режим фактически неработоспособен.

**Статус:** BROKEN LEGACY DIAGNOSTIC / CANDIDATE — удалить режим либо восстановить отдельный диагностический инструмент.

## 8. VK

- `.github/workflows/vk-wheel-notification.yml`;
- `.github/workflows/vk-start-welcome.yml`;
- `vk_wheel_notifications.py`;
- `vk_dynamic_subscribers.py`;
- `vk_start_welcome.py`.

**Статус:** PRODUCTION/OPERATIONAL. Позже проверить общий VK transport на дублирование, не смешивая разные бизнес-сценарии.

## 9. Backup и private state

Критические компоненты:

- `.github/workflows/bot-state-backup.yml`;
- `backup_rotation.py`;
- `bot_private_state.py`;
- `bot_private_state.enc.json`;
- `migrate_bot_private_state.py`;
- `privacy_retention.py`;
- `security_audit.py`.

Подтверждённый ordinary backup contract: лимит 3 `backup/*`; новый backup должен сохраниться; до удаления проверяются ancestry и отсутствие unique commits; ошибка проверки отменяет удаление; после ротации inventory проверяется повторно.

## 10. Runtime JSON/state ownership

Машинный inventory хранится в `monitor_data.JSON_STATE_CONTRACTS`.

### Authoritative

`admin_action_queue.json`, `bot_private_state.enc.json`, `candidate_moderation.json`, `discovery_state.json`, `intelligence_state.json`, `notification_delivery_state.json`, `source_health.json`, `source_stats.json`, `state.json`, `unknown_timer_samples.json`.

### Diagnostic

`ai_runtime_state.json`, `admin_panel_status.json`, `incident_state.json`, `monitor_recovery_status.json`, `monitor_status.json`, `source_tier_state.json`, `source_transport_state.json`, `system_check_state.json`.

### Config/cache/compatibility

`identifier_sources.json`, `partners_catalog.json`, `source_registry.json`, `bot_access.json`, `source_requests.json`.

### Frozen archive state

`activation_runtime_state.json`, `miniapp_deploy_runtime.json`, `miniapp_deployment.json`, `private_state_deployment.json`, `state_api/package.json`, `state_api_runtime.json`.

Большая часть автоматических `[skip ci]` коммитов — запись authoritative/diagnostic state в `main`. Перенос state из основной Git-истории требует отдельного проекта персистентности; прямое удаление запрещено.

## 11. GitHub Actions

### Production long-running

- `monitor.yml`;
- `admin-bot.yml`;
- `system-health.yml`.

### Operational / maintenance

- `monitor-watchdog.yml`;
- `bot-state-backup.yml`;
- `admin-action.yml`;
- `auto-participation.yml`;
- `nightly-discovery.yml`;
- `source-intelligence.yml`;
- `source-registry.yml`;
- `source-tier-maintenance.yml`;
- `activate-66-sources.yml`;
- `migrate-all-sources.yml`;
- `daily-report.yml` — manual-only compatibility/review;
- `rotate-bot-state-key.yml`.

### Validation / recovery

- `validate-current.yml`;
- `v22-checks.yml` — stale filename; current consolidated checks;
- `bot-recovery-smoke.yml`;
- `validate-private-state.yml`;
- `telegram-resilience-check.yml`.

### Frozen archive

- `cloudflare-pages.yml` — disabled Mini App deployment;
- `state-api.yml` — disabled State API;
- `migrate-private-state.yml` — disabled D1 migration;
- `monitor-66-live.yml` — archived legacy monitor, job permanently disabled;
- `miniapp-archive-guard.yml` — активная защита frozen archive.

На этапе 1 выявлено, что archive guard блокировал весь `docs/**`, поэтому технические Markdown-документы ошибочно считались изменением Mini App. В рабочей ветке guard сужен: Markdown-документация разрешена, static Mini App assets и State API остаются защищёнными.

## 12. Mini App и State API

Mini App и State API подтверждены как ARCHIVE:

- deployment/state workflows явно archived and disabled;
- `MINI_APP_ARCHIVED.md` фиксирует архивный статус;
- `state_api/**`, static assets и archive JSON сохранены как frozen archive.

При этом `preflight.py` всё ещё требует static Mini App assets как часть общего repository preflight.

**Статус:** static Mini App = ARCHIVE; требования production preflight к нему = STALE ACTIVE CONTRACT / CANDIDATE.

## 13. Tests, acceptance и probes

`validate-current.yml` запускает compileall, preflight, chapter acceptance и полный pytest с coverage gate. Поэтому `tests/**` остаются VALIDATION, даже если отдельные названия исторические (`chapter`, `hotfix`).

Подтверждённые группы: current contracts, button/UI matrix, lifecycle, notifications, personal voting, source discovery, suspicious posts, nightly policy, concurrency/CI, Actions security, AI, VK и production acceptance.

Root-level helpers:

- `test_bot_state_fixture.py` — используется private-state validation;
- `self_test.py` — используется monitor workflow;
- `wheel_scenario_suite.py` — recovery/domain validation;
- `chapter1_stability.py` … `chapter5_acceptance.py` — acceptance contracts;
- `sitecustomize.py` — включён private-state validation.

Подтверждённый broken probe-path: auto-participation probe.

## 14. Кандидаты следующей очистки

Высокий приоритет:

1. Перенос уникальной логики `admin_panel_runtime_v41.py` в stable `bbvg/bot/*`.
2. Сведение `system_checks.py → v2 → v3` к одному стабильному модулю.
3. Устранение CI-удержания лестницы `admin_panel_runtime_v25–v38` после переноса нужных recovery contracts.
4. Отделение legacy Mini App-era `admin_panel_runtime_v16–v24`.
5. Удаление или восстановление broken auto-participation probe mode.
6. Удаление frozen Mini App assets из production `preflight.py` с сохранением отдельной archive-проверки.

Средний приоритет:

7. Переименование `activate-66-sources.yml`.
8. Переименование/пересмотр `migrate-all-sources.yml`.
9. Переименование `v22-checks.yml`.
10. Проверка необходимости отдельного `daily-report.yml`.
11. Последовательное сворачивание остальных `*_v2` после переноса реализации.

Отдельный архитектурный проект:

12. Разделение Git history исходного кода и высокочастотного runtime state без потери persistence.

## 15. Что удалено на этапе 1

Production/compatibility файлы **не удалялись**. Этап 1 завершает инвентаризацию, а не совмещает аудит с массовой очисткой.

## 16. Итог этапа 1

- production entrypoints определены;
- versioned runtime разделены на production, compatibility и legacy группы;
- workflows классифицированы;
- ownership runtime JSON зафиксирован;
- Mini App подтверждён как frozen archive;
- archive guard для технической Markdown-документации исправлен в рабочей ветке;
- найден broken auto-participation probe path;
- stale workflow names зафиксированы;
- список следующих cleanup-блоков сформирован.

Следующий запрошенный пользователем результат после этапа 1 — актуальный PDF по проекту и итогам этой инвентаризации.
