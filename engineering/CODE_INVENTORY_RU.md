# BB V.G. — инвентарь кода и данных

Актуально на 25 июля 2026 года. Документ описывает действующих владельцев
ответственности. Исторические детали хранятся в `PROJECT_CHANGELOG_RU.md`, а не
в отдельных файлах «глава», `changes`, `plan` или versioned-runtime.

Подробный фактический порядок контура колёс перед новым рефакторингом зафиксирован
в `engineering/WHEEL_PIPELINE_BASELINE_RU.md`. Этот baseline является
обязательным входом для изменений поиска, уведомлений, lifecycle, автоучастия и
recovery.

## Production entrypoints

| Команда | Назначение |
|---|---|
| `python notification_button_recovery.py` | Единственный Telegram Control Center |
| `python bbvg_monitor_main.py` | Основной монитор колёс |
| `python auto_participation_worker.py` | Первая попытка автоучастия |
| `python auto_participation_recovery.py` | Независимое восстановление пропущенных попыток |
| `python nightly_discovery_entry.py` | Ручная/плановая проверка ночного списка |
| `python source_intelligence_entry.py` | Разведка кандидатов источников |
| `python daily_report_entry.py` | Формирование выбранной сводки |

## Telegram-панель

| Модуль | Ответственность |
|---|---|
| `notification_button_recovery.py` | Production-композиция, recovery старых wheel-кнопок, финальные итоги автоучастия |
| `admin_panel_runtime_v41.py` | Тонкая совместимость интерфейса версии 41 |
| `bbvg/bot/runtime.py` | Финальная композиция панели и маршрутизация |
| `bbvg/bot/foundation.py` | Базовые Telegram/GitHub операции |
| `bbvg/bot/interface.py` | Экраны, меню, аналитика и модерация кандидатов |
| `bbvg/bot/users.py` | Пользователи, роли и настройки уведомлений |
| `bbvg/bot/sources.py` | Основные/ночные источники и реестр |
| `bbvg/bot/source_requests.py` | Пользовательские заявки источников |
| `bbvg/bot/wheels.py` | Активные колёса и callback-действия |
| `bbvg/bot/storage.py` | Зашифрованное приватное состояние и merge |
| `personal_wheel_voting.py` | Личная отметка, HMAC actor и multi-source рейтинг |

`admin_panel_runtime_v2.py`–`admin_panel_runtime_v40.py` не являются
compatibility API и запрещены preflight. В production сохраняется только
`admin_panel_runtime_v41.py`.

## Монитор и жизненный цикл

| Группа | Модули |
|---|---|
| Базовый монитор | `monitor.py`, `monitor_data.py`, `monitor_entry.py` |
| Production-композиция | `bbvg_monitor_runtime.py`, `bbvg_monitor_main.py` |
| Событие колеса | `wheel_event_runtime.py`, `wheel_lifecycle_v2.py`, `wheel_link_lifecycle.py`, `recurring_wheel_events.py` |
| Качество обнаружения | `wheel_detection_reliability.py`, `wheel_metadata_quality.py`, `wheel_publications_v2.py`, `telegram_post_links_v2.py` |
| Напоминания | `personal_reminder_filter.py`, `notification_navigation.py` |
| Замороженный baseline | `engineering/WHEEL_PIPELINE_BASELINE_RU.md`, `tests/scenarios/test_wheel_pipeline_baseline.py` |

Суффикс `v2` у перечисленных активных предметных модулей пока является частью
импортного контракта. Эти файлы нельзя удалять как «старые» без отдельной
миграции всех импортов и state-совместимости.

## Уведомления и автоучастие

| Группа | Модули |
|---|---|
| Маршрутизация | `notification_router.py`, `notification_preferences_v2.py` |
| Идемпотентность | `notification_integrity_v2.py`, `notification_remote_checkpoint.py`, `bot_notification_state.py` |
| Идентичность Telegram-источника | `telegram_transport.py`, `personal_wheel_voting.py` |
| Автоучастие | `auto_participation_worker.py`, `auto_participation_recovery.py`, `auto_participation_dispatch.py` |
| Итоги | `auto_participation_bot_sync.py`, `auto_participation_owner_sync.py`, `auto_participation_notifications.py` |
| Аккаунты | `betboom_account_participation.py`, `xflarxx_account_participation.py`, `xflarxx_runtime_integration.py` |
| VK | `vk_wheel_notifications.py`, `vk_dynamic_subscribers.py`, `vk_start_welcome.py` |

## Диагностика и эксплуатация

- `system_checks.py`, `system_checks_v2.py`, `system_checks_v3.py` — действующая
  композиция health-проверок;
- `monitor_health.py` — heartbeat и сигнал controlled recovery для watchdog;
- `incident_manager.py` — жизненный цикл инцидентов;
- `preflight.py` — обязательные структурные и production-контракты;
- `security_audit.py` — аудит публичного состояния и секретов;
- `backup_rotation.py` — безопасная ротация backup-веток;
- `scripts/validate_control_center.sh` — exact-SHA проверка релиза панели.

## Данные

Отслеживаемые JSON разделяются на:

- authoritative runtime state (`state.json`, `source_stats.json`,
  `source_health.json`);
- зашифрованное приватное состояние (`bot_private_state.enc.json`);
- очередь и delivery ledger (`admin_action_queue.json`,
  `notification_delivery_state.json`);
- heartbeat/status (`admin_panel_status.json`, `monitor_status.json`);
- intelligence/discovery/registry state.

Они не считаются мусором. Владельцы и допустимые схемы проверяются тестом
`test_all_tracked_json_has_an_owner_and_compatible_schema`.

## Workflow

Основные действующие workflow:

- `admin-bot.yml` — Control Center на exact SHA из release-marker;
- `monitor.yml` — монитор; успешная смена запускает преемника, ошибочную
  восстанавливает только `monitor-watchdog.yml`;
- `monitor-watchdog.yml` — единственный controlled recovery монитора;
- `auto-participation.yml` — автоучастие владельца;
- `xflarxx-auto-participation.yml` — отдельный аккаунт xFLARXx;
- `telegram-source-transport.yml` — доступность текущего inventory;
- `telegram-domain-policy.yml` — политика единственного домена `telegram.me`;
- `system-health.yml` — полная диагностика;
- `validate-current.yml`, `current-checks.yml`, `validate-private-state.yml`,
  `bot-recovery-smoke.yml`, `telegram-resilience-check.yml` — PR/CI-контракты.

Workflow с явной пометкой `archived` и Mini App archive guard сохраняются
намеренно, но не имеют schedule и не входят в production-контур.
