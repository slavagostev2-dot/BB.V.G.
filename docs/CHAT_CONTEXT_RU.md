# Контекст проекта BB V.G. для нового чата / AI-агента

> Состояние на 20.07.2026.
> Этап 1 глобальной ревизии и этап 2A стабилизации baseline CI завершены. Handoff-PDF создан. Следующий архитектурный блок по плану — этап 2B (Telegram Control Center), но начинать его только по отдельной команде пользователя.

## 1. Репозиторий

`slavagostev2-dot/betboom-wheel-monitor`

Основная ветка: `main`.

Снимок `main`, использованный для завершения этапа 1: `e91f7608b4377bd8bbdf539d75c266c1278084db`.

Исходная backup-точка глобальной ревизии: `backup/before-global-repository-cleanup-2026-07-20`.

Она была создана от SHA `1da3115319305fa5e237cd90124186c12ab98753` и на момент создания проверена как идентичная `main`.

Рабочая ветка аудита: `cleanup/global-repository-audit-2026-07-20`.

## 2. Что делает проект

BB V.G. мониторит утверждённые Telegram-источники, обнаруживает колёса BetBoom, повторно проверяет lifecycle через BetBoom API, уведомляет пользователей, ведёт персональное участие и рейтинг источников, предоставляет Telegram Control Center, выполняет auto-participation, имеет VK-интеграцию, health/incidents и AI health-inspection, хранит encrypted private state и использует GitHub Actions как production/operational runtime.

## 3. Что обязательно читать

1. `AGENTS.md`.
2. `docs/PROJECT_CHANGELOG_RU.md`.
3. `docs/REFACTOR_PLAN_RU.md`.
4. `docs/CODE_INVENTORY_RU.md`.
5. `docs/RUNTIME_METHOD_INVENTORY_RU.md`.
6. этот файл.

## 4. Результат этапа 1

Инвентаризация завершена. Подтверждено:

- Telegram production: `admin-bot.yml → admin_panel_runtime_v41.py → bbvg/bot/runtime.py`;
- monitor production: `monitor.yml → bbvg_monitor_main.py`;
- health production: `system-health.yml → system_checks_v3.py → v2 → system_checks.py`;
- legacy Mini App-era panel chain как минимум `v16–v24`;
- bot-only compatibility chain `v25→v26→v28→v29→v30→v31→v32→v36→v37→v38`;
- stable `bbvg.bot.runtime.TelegramPanelRuntime` уже не наследует versioned classes;
- старую runtime-chain удерживают validation/recovery workflows;
- `admin_panel_runtime_v41.py` всё ещё содержит уникальную production-логику;
- Mini App/State API заморожены и отключены;
- production `preflight.py` всё ещё требует frozen Mini App static assets;
- source workflows содержат stale `66`-названия;
- auto-participation workflow содержит сломанный optional probe path;
- JSON ownership описан в `monitor_data.JSON_STATE_CONTRACTS`;
- высокочастотные `[skip ci]` commits создаются runtime-state writers в `main`.

## 5. Archive guard

`miniapp-archive-guard.yml` раньше блокировал любое изменение `docs/**`, кроме `PROJECT_CHANGELOG_RU.md`. Поэтому новые технические Markdown-документы ошибочно считались изменением frozen Mini App.

В рабочей ветке аудита guard изменён: Markdown-документация разрешена, static Mini App assets, State API и архивные deployment-файлы остаются защищёнными.

## 6. Важный UI-контракт

При будущем refactor Control Center нельзя самовольно менять состав и порядок кнопок, `callback_data`, роль-доступность и навигацию. После каждого блока проверять button matrix и panel contracts.

## 7. Источники

`public_sources.txt` — основной утверждённый tier.

`source_catalog.txt` — вручную утверждённый nightly tier.

Discovery не должен автоматически добавлять произвольные кандидаты. Сохраняется отдельно зафиксированное разрешение на promotion подтверждённого nightly source при реально активном колесе.

## 8. Backup

Критические файлы: `.github/workflows/bot-state-backup.yml` и `backup_rotation.py`.

Ordinary backup refs ограничены тремя. До удаления проверяются ancestry и отсутствие unique commits. При ошибке удаления не происходит.

## 9. Mini App

Текущий статус — frozen archive.

- `cloudflare-pages.yml` disabled;
- `state-api.yml` disabled;
- `migrate-private-state.yml` disabled;
- `monitor-66-live.yml` archived и не выполняется;
- `MINI_APP_ARCHIVED.md` фиксирует архивный статус.

Следующий cleanup после PDF должен отделить archive validation от production `preflight.py`.

## 10. Broken auto-participation probe

`auto-participation.yml` имеет `probe=true`, но актуальный `main` не содержит `auto_participation_probe.py`, `auto_participation_probe.trigger` и `auto_participation_probe_result.json`.

Обычный auto-participation path от этого не зависит.

## 11. Этап 2A — завершён

Baseline CI PR #108 диагностирован и стабилизирован. Выявлены и исправлены: устаревший interface acceptance, реальная ошибка natural-language admin, отсутствующий ownership-контракт `ai_runtime_state.json`, устаревший тест способа сохранения encrypted state и UI-ожидание без уже существующего профиля. Временные диагностические изменения CI удалены.

## 12. Что делать дальше

По PDF следующий блок — этап 2B:

1. перенести уникальную логику `admin_panel_runtime_v41.py` в stable `bbvg/bot/*`;
2. сохранить точный состав и порядок существующих кнопок и `callback_data`;
3. после каждого небольшого переноса запускать button matrix, current contracts, full pytest и acceptance;
4. только после доказанной эквивалентности сделать `v41` thin compatibility entrypoint;
5. дальнейшие главы (`system_checks`, legacy runtime, stale workflows, runtime-state architecture) не начинать без отдельной команды пользователя.

## 13. Правило достоверности

Не считать файл мусором только по имени. Для удаления нужны подтверждённые отсутствие production/workflow/test ссылок, перенос полезной логики и успешные проверки.
