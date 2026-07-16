from pathlib import Path

agents_path = Path("AGENTS.md")
agents = agents_path.read_text(encoding="utf-8")
old = """### Текущий актуальный бэкап рефакторинга — после объединения пользовательских настроек

- Ветка: `backup/refactor-after-user-settings-cleanup-2026-07-16`
- Commit SHA: `e9746cf43d1a6acbaacf01f25ec65fcd7be8fbb9`
- Последний подтверждённый run: `29489184430`
- Состояние проверки: compile, modules, pytest, compatibility acceptance, consolidated acceptance, dependency audit и MRO inventory — успешно.
- Включает: единые `bbvg/bot/users.py` и `bbvg/bot/storage.py`, актуальные PR/recovery workflow, удалённые v27 и v33–v35.
- Назначение: возврат к подтверждённому состоянию после переноса пользовательских настроек, приватности и owner-managed notification UI в `UserSettingsMixin`.

### Предыдущий этапный бэкап — перед объединением пользовательских настроек
"""
new = """### Текущий актуальный бэкап рефакторинга — после удаления связующих runtime

- Ветка: `backup/refactor-after-foundation-links-cleanup-2026-07-16`
- Commit SHA: `378adc8fd979a1287a41f5b19df9b7161ce38d1f`
- Последний подтверждённый run: `29491536577`
- Состояние проверки: compile, modules, pytest, compatibility acceptance, consolidated acceptance, dependency audit и MRO inventory — успешно.
- Включает: прямую композицию foundation/interface/source-requests/users/wheels и удалённые v13, v17, v19, v21.
- Метрики: 19 исторических runtime-файлов, все 19 в рабочей цепочке, 5 896 строк.
- Назначение: возврат к подтверждённому состоянию после удаления промежуточных связующих runtime.

### Предыдущий подтверждённый бэкап — после объединения пользовательских настроек

- Ветка: `backup/refactor-after-user-settings-cleanup-2026-07-16`
- Commit SHA: `e9746cf43d1a6acbaacf01f25ec65fcd7be8fbb9`
- Последний подтверждённый run: `29489184430`
- Назначение: возврат к состоянию до удаления v13/v17/v19/v21.

### Предыдущий этапный бэкап — перед объединением пользовательских настроек
"""
if agents.count(old) != 1:
    raise SystemExit(f"AGENTS backup block count: {agents.count(old)}")
agents_path.write_text(agents.replace(old, new), encoding="utf-8")

changelog_path = Path("docs/PROJECT_CHANGELOG_RU.md")
changelog = changelog_path.read_text(encoding="utf-8")
marker = "---\n\n"
entry = """## 2026-07-16 — Удалены связующие runtime v13, v17, v19 и v21

**Причина:** после переноса foundation, interface, source requests, wheels и users в предметные модули четыре исторических файла оставались только промежуточными импортами и пустыми классами/aliases.

**Что изменено:**

- `PanelInterfaceRuntime` напрямую объединяет `PanelFoundationMixin` и v9; v13 удалён;
- `WheelInteractionRuntime` напрямую наследует `SourceRequestRuntime`; v19 удалён;
- потребители `default_source_requests`, notification constants и user rendering переведены на `bbvg.bot.source_requests` и `bbvg.bot.users`;
- `system_checks.py` контролирует предметные файлы вместо v17/v21;
- удалены `admin_panel_runtime_v17.py` и `admin_panel_runtime_v21.py`;
- число исторических runtime-файлов уменьшилось до 19, все 19 участвуют в рабочей цепочке; общий объём — 5 896 строк.

**Затронутые модули и файлы:** `bbvg/bot/interface.py`, `wheels.py`, `admin_panel_runtime_v25.py`, `v26.py`, `v30.py`, `v37.py`, `system_checks.py`, `tests/test_nightly_idle_policy.py`, удалённые v13/v17/v19/v21.

**Проверки:** aliases — runs `29489724593`, `29489869972`, `29489941317`; переключение и удаление v19 — `29490210078`, `29490275943`; переключение и удаление v13 — `29490946538`, `29491020250`; прямые зависимости и удаления v21/v17 — `29491395328`, `29491483117`, `29491536577`. Во всех runs успешны все семь частей validation.

**Актуальный backup:** `backup/refactor-after-foundation-links-cleanup-2026-07-16`, SHA `378adc8fd979a1287a41f5b19df9b7161ce38d1f`.

**Откат:** вернуть ветку рефакторинга на SHA `378adc8fd979a1287a41f5b19df9b7161ce38d1f`; для возврата до этапа использовать `backup/refactor-after-user-settings-cleanup-2026-07-16`.

"""
if changelog.count(marker) != 1:
    raise SystemExit(f"Changelog marker count: {changelog.count(marker)}")
changelog_path.write_text(changelog.replace(marker, marker + entry, 1), encoding="utf-8")
