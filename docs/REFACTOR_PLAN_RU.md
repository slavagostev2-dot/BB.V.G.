# План глобальной очистки и рефакторинга BB V.G.

> Актуализация: 20.07.2026.
> Статус: **этап 1 и этап 2A — завершены; следующий архитектурный блок — этап 2B**.
> Текущий снимок `main`, использованный для завершения этапа 1: `e91f7608b4377bd8bbdf539d75c266c1278084db`.
> Исходная rollback-точка: `backup/before-global-repository-cleanup-2026-07-20`, созданная от `1da3115319305fa5e237cd90124186c12ab98753`.
> Рабочая ветка: `cleanup/global-repository-audit-2026-07-20`.

## Главный принцип

Сначала понять и зафиксировать зависимости → создать/проверить backup → менять небольшим функциональным блоком → запускать проверки → обновлять документацию → только после этого удалять старый слой.

Массовое удаление по названию файла запрещено.

## Этап 0. Безопасная исходная точка — выполнено

- [x] Прочитан `AGENTS.md`.
- [x] Проверен `docs/PROJECT_CHANGELOG_RU.md`.
- [x] Проверен механизм backup rotation.
- [x] Подтверждён лимит трёх ordinary `backup/*`.
- [x] Создан `backup/before-global-repository-cleanup-2026-07-20`.
- [x] На момент создания backup был идентичен `main` SHA `1da3115319305fa5e237cd90124186c12ab98753`.
- [x] Создана отдельная ветка глобального аудита.

## Этап 1. Полная инвентаризация — завершено

### 1.1 Production entrypoints

- [x] Telegram Control Center: `admin-bot.yml → admin_panel_runtime_v41.py → bbvg/bot/runtime.py`.
- [x] Wheel monitor: `monitor.yml → bbvg_monitor_main.py`.
- [x] System Health: `system-health.yml → system_checks_v3.py → system_checks_v2.py → system_checks.py`.
- [x] Auto-participation: `auto-participation.yml → betboom_auto_participation.py / auto_participation_worker.py`.
- [x] Backup: `bot-state-backup.yml → backup_rotation.py`.

### 1.2 Versioned runtime

- [x] Выделена legacy Mini App-era chain `admin_panel_runtime_v16–v24`.
- [x] Выделена bot-only compatibility chain `v25→v26→v28→v29→v30→v31→v32→v36→v37→v38`.
- [x] Подтверждено, что stable `bbvg.bot.runtime.TelegramPanelRuntime` уже не наследует versioned runtime classes.
- [x] Подтверждено, что старую лестницу всё ещё удерживают validation/recovery workflows.
- [x] Подтверждено, что `admin_panel_runtime_v41.py` содержит уникальную production-логику и пока не является чистым thin wrapper.

### 1.3 Workflows

- [x] Разделены production, operational/maintenance, validation/recovery и frozen archive workflows.
- [x] Зафиксированы stale names `activate-66-sources.yml`, `migrate-all-sources.yml`, `v22-checks.yml`.
- [x] Подтверждён архивный статус `cloudflare-pages.yml`, `state-api.yml`, `migrate-private-state.yml`, `monitor-66-live.yml`.

### 1.4 Runtime state

- [x] Ownership JSON зафиксирован по `monitor_data.JSON_STATE_CONTRACTS`.
- [x] Разделены authoritative, diagnostic, config, cache, compatibility и archive state.
- [x] Подтверждено, что высокочастотные runtime commits в `main` являются отдельной архитектурной проблемой, а не основанием удалить state-файлы.

### 1.5 Mini App

- [x] Mini App/State API подтверждены как frozen archive.
- [x] Выявлено stale-требование `preflight.py` к архивным static assets.
- [x] Выявлено, что `miniapp-archive-guard.yml` блокировал любую новую техническую Markdown-документацию внутри `docs/`.
- [x] В ветке аудита guard сужен: Markdown-документы разрешены, static Mini App/State API остаются защищёнными.

### 1.6 Tests, recovery и probes

- [x] Зафиксированы основные validation/recovery workflows и root-level acceptance helpers.
- [x] Подтверждено CI-удержание старых panel runtime files.
- [x] Обнаружен broken optional auto-participation probe path: workflow ссылается на отсутствующие `auto_participation_probe.py`, trigger и result-файл.

### 1.7 Результат этапа 1

Полный технический результат находится в:

- `docs/CODE_INVENTORY_RU.md`;
- `docs/RUNTIME_METHOD_INVENTORY_RU.md`.

На этапе 1 production-код и compatibility-слои намеренно не удалялись. Теперь есть проверяемая карта, позволяющая выполнять очистку небольшими блоками.

## Handoff-PDF после этапа 1 — выполнено

Самодостаточный PDF сформирован после инвентаризации и используется как карта дальнейших глав.

## Этап 2A. Диагностика и стабилизация baseline CI — завершено

- [x] Три исходных красных контура PR #108 разобраны по шагам GitHub Actions.
- [x] Устаревший Chapter 4 acceptance приведён к фактической цепочке `admin-bot.yml → scripts/validate_control_center.sh → telegram_ui.py`.
- [x] Исправлена реальная ошибка `natural_language_admin`: несовместимая длина записей `critical_patterns` больше не роняет обычные текстовые команды owner/admin.
- [x] `ai_runtime_state.json` зарегистрирован в `monitor_data.JSON_STATE_CONTRACTS` как diagnostic state владельца `ai-core`; inventory теперь содержит 29 JSON.
- [x] CI-контракты обновлены под фактическое сохранение `bot_private_state.enc.json` и существующую кнопку `page:profile` без перестановки прежних кнопок.
- [x] Временная CI-диагностика и одноразовые patch-шаги удалены.
- [x] На чистом head после исправлений все пять основных PR-проверок прошли: Validate current, current checks, bot-only recovery, recovery smoke и Telegram transport.

## Этап 2B. Telegram Control Center

1. Составить метод-карту уникальной логики `TelegramPanelRuntimeV41`.
2. Перенести её по стабильным владельцам `bbvg/bot/*`.
3. Сохранить состав, порядок и `callback_data` кнопок.
4. Запустить button matrix, current contracts, full pytest и acceptance.
5. Сделать `v41` thin compatibility entrypoint.
6. После этого убрать validation/recovery-зависимость от `v25–v38` и удалить подтверждённо ненужные слои отдельным блоком.

## Этап 3 после PDF. System Health

1. Зафиксировать все monkey-patch подмены `system_checks`, `v2`, `v3`.
2. Перенести актуальные реализации в один стабильный модуль.
3. Сохранить `details/findings`, incident delivery и AI health contracts.
4. Обновить workflows/tests.
5. Удалить wrappers только после отсутствия входящих ссылок.

## Этап 4 после PDF. Архив и stale workflows

- убрать frozen Mini App assets из production `preflight.py` и оставить отдельную archive validation;
- разобрать legacy Mini App-era panel runtime;
- исправить broken auto-participation probe;
- переименовать stale workflows;
- проверить необходимость manual-only `daily-report.yml`.

## Этап 5 после PDF. Остальные versioned modules

По функциональным кластерам:

- admin actions;
- notifications;
- source tier maintenance;
- wheel lifecycle/publications;
- Telegram post links;
- acceptance wrappers.

Правило: актуальная реализация сначала переносится в стабильное предметное имя, затем удаляется versioned compatibility layer.

## Этап 6 после PDF. Runtime state architecture

Отдельно спроектировать, что должно:

- оставаться version-controlled;
- храниться в encrypted state;
- жить в отдельном state storage/branch/artifact;
- восстанавливаться после workflow restart.

До этого authoritative runtime JSON не удалять.

## Финальный критерий глобальной очистки

1. У каждой production-функции один понятный владелец.
2. Versioned-файлы остаются только там, где есть подтверждённая совместимость.
3. Старые CI-контракты не удерживают ненужный код.
4. Названия файлов и workflows соответствуют назначению.
5. Runtime state не загрязняет кодовую историю без необходимости.
6. README, `AGENTS.md`, inventories, changelog и PDF соответствуют коду.
7. UI и функциональность сохранены.
8. CI/acceptance и живые heartbeat подтверждены.
