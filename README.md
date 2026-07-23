# BB V.G. — монитор колёс BetBoom

BB V.G. проверяет одобренные публичные Telegram-источники, подтверждает найденные
колёса через BetBoom API, ведёт жизненный цикл события и отправляет
пользовательские уведомления в Telegram и VK. Telegram-панель также управляет
источниками, личным участием, уведомлениями и диагностикой.

## Рабочий контур

- production-ветка: `main`;
- Telegram Control Center: `python notification_button_recovery.py`;
- основной монитор: `python bbvg_monitor_main.py`;
- единый набор acceptance-проверок:
  `python tests/production_acceptance.py --section all`;
- полный набор тестов: `python -m pytest -q`;
- preflight: `python preflight.py`;
- аудит безопасности: `python security_audit.py --current`.

`notification_button_recovery.py` — единственная production-команда панели.
Она использует `admin_panel_runtime_v41.py` как тонкий compatibility-слой над
предметными модулями `bbvg/bot/`. Второй consumer Telegram `getUpdates`
запрещён.

## Структура

| Путь | Ответственность |
|---|---|
| `bbvg/bot/` | Telegram-интерфейс, пользователи, источники, колёса и приватное состояние |
| `bbvg/monitor/` | Поиск источников и подозрительных публикаций |
| `monitor*.py`, `bbvg_monitor_*.py` | Сканирование, композиция и непрерывный runtime |
| `wheel_*.py`, `recurring_wheel_events.py` | Идентичность и жизненный цикл колеса |
| `notification_*.py`, `bot_notification_state.py` | Маршрутизация, предпочтения и дедупликация доставки |
| `auto_participation_*.py`, `betboom_*participation*.py` | Автоучастие, recovery и объединение результатов аккаунтов |
| `system_checks*.py`, `monitor_health.py`, `incident_manager.py` | Диагностика и эксплуатационное состояние |
| `tests/` | Unit, integration, regression и production acceptance |
| `.github/workflows/` | CI, production, maintenance и ручные fallback-запуски |
| `docs/` | Документация проекта и замороженный архив Mini App |
| `state_api/` | Замороженный архив Worker/D1; не входит в production |

Подробная карта находится в
[`engineering/CODE_INVENTORY_RU.md`](engineering/CODE_INVENTORY_RU.md), владельцы методов
панели — в
[`engineering/RUNTIME_METHOD_INVENTORY_RU.md`](engineering/RUNTIME_METHOD_INVENTORY_RU.md).

## Локальная проверка

```bash
python -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m pytest -q
.venv/bin/python tests/production_acceptance.py --section all
.venv/bin/python preflight.py
.venv/bin/python security_audit.py --current
```

Для Control Center дополнительно используется:

```bash
bash scripts/validate_control_center.sh
```

Production Control Center запускается не с плавающего `main`, а с точного
40-символьного SHA из `control_center_release.txt`. Validator оставляет checkout
на этом commit, а live-job получает тот же SHA как job output.

Для проверки локального candidate-коммита без изменения production-маркера:

```bash
CONTROL_CENTER_RELEASE_SHA="$(git rev-parse HEAD)" \
  bash scripts/validate_control_center.sh
```

## Важные ограничения

- Реферальные колёса сохраняются и участвуют в автоучастии, но не отправляют
  пользовательские уведомления, напоминания и итоги ни в Telegram, ни в VK.
- Настройка уведомлений автоучастия не отключает саму попытку участия.
- Runtime JSON отслеживаются намеренно: это эксплуатационное состояние, а не
  временный мусор.
- Mini App, Worker и D1 архивированы. Их код не изменяется без отдельной прямой
  команды.
- Токены, Telegram ID, chat ID и незашифрованное приватное состояние нельзя
  добавлять в публичный репозиторий.

## Документы

- [`AGENTS.md`](AGENTS.md) — обязательные правила изменения репозитория;
- [`engineering/CHAT_CONTEXT_RU.md`](engineering/CHAT_CONTEXT_RU.md) — актуальные продуктовые
  договорённости;
- [`engineering/REFACTOR_PLAN_RU.md`](engineering/REFACTOR_PLAN_RU.md) — план и статус
  технического долга;
- [`docs/PROJECT_CHANGELOG_RU.md`](docs/PROJECT_CHANGELOG_RU.md) — журнал
  значимых изменений;
- [`SECURITY_RU.md`](SECURITY_RU.md) — безопасность и приватное состояние;
- [`MINI_APP_ARCHIVED.md`](MINI_APP_ARCHIVED.md) — границы архива Mini App.
