from __future__ import annotations

import subprocess
from pathlib import Path


OLD_REF = "origin/cleanup/chapter-2c-legacy-panel-removal-2026-07-21"
COPY_PATHS = (
    ".github/workflows/bot-recovery-smoke.yml",
    ".github/workflows/system-health.yml",
    ".github/workflows/v22-checks.yml",
    ".github/workflows/validate-private-state.yml",
    "preflight.py",
    "scripts/validate_control_center.sh",
    "tests/test_current_contracts.py",
)


def git_show(path: str) -> str:
    return subprocess.check_output(
        ["git", "show", f"{OLD_REF}:{path}"], text=True, encoding="utf-8"
    )


def replace_once(path: str, old: str, new: str) -> None:
    target = Path(path)
    text = target.read_text(encoding="utf-8")
    if new in text:
        return
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"Expected one exact anchor in {path}, found {count}")
    target.write_text(text.replace(old, new, 1), encoding="utf-8")


for path in COPY_PATHS:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(git_show(path), encoding="utf-8")

for version in range(25, 41):
    path = Path(f"admin_panel_runtime_v{version}.py")
    if not path.exists():
        raise SystemExit(f"Expected historical runtime before deletion: {path}")
    path.unlink()

replace_once(
    "AGENTS.md",
    '''- `admin_panel_runtime_v41.py` — только тонкий переходник на `bbvg.bot.runtime`, а не отдельная реализация.
- Предметные владельцы панели: `bbvg/bot/interface.py` (экраны и
  навигация), `users.py` (пользователи, роли и настройки),
  `sources.py` (источники), `wheels.py` (колёса и callback), `storage.py`
  (зашифрованное состояние), `runtime.py` (финальная композиция,
  lifecycle и очередь admin actions).
- Production MRO `bbvg.bot.runtime.TelegramPanelRuntime` не содержит
  классов из `admin_panel_runtime_v*`. Versioned-файлы остаются только
  как временная совместимость до главы 9 и не являются
  владельцами production-поведения.''',
    '''- `admin_panel_runtime_v41.py` — compatibility entrypoint над `bbvg.bot.runtime`; он может сохранять узкие presentation-совместимости, но не создаёт параллельный production-runtime.
- Предметные владельцы панели: `bbvg/bot/interface.py` (экраны и
  навигация), `users.py` (пользователи, роли и настройки),
  `sources.py` (источники), `wheels.py` (колёса и callback), `storage.py`
  (зашифрованное состояние), `runtime.py` (финальная композиция,
  lifecycle и очередь admin actions). Текущий MRO также использует базовый
  `admin_panel_v2.py` для совместимых общих отчётных методов.
- Production MRO `bbvg.bot.runtime.TelegramPanelRuntime` не содержит
  классов из `admin_panel_runtime_v*`.
- Историческая bot-only цепочка `admin_panel_runtime_v25.py`–`v40.py`
  удалена в главе 2C после переноса внешних preflight/CI/recovery-ссылок.
  Возвращать эту лестницу или подключать к production более ранние versioned-
  runtime запрещено; необходимые совместимости реализуются в действующих
  предметных владельцах и покрываются regression-контрактами.''',
)
replace_once("AGENTS.md", "всех 28 JSON", "всех 29 JSON")
replace_once(
    "AGENTS.md",
    '''| `admin_panel_status.json` | diagnostic | control-center workflow |
| `bot_access.json` | compatibility | encrypted private state |''',
    '''| `admin_panel_status.json` | diagnostic | control-center workflow |
| `ai_runtime_state.json` | authoritative | AI Core |
| `bot_access.json` | compatibility | encrypted private state |''',
)

changelog = Path("docs/PROJECT_CHANGELOG_RU.md")
text = changelog.read_text(encoding="utf-8")
heading = "## 2026-07-21 — Глава 2C: удалена историческая цепочка Telegram-панели v25–v40"
if heading not in text:
    anchor = "---\n\n"
    if text.count(anchor) != 1:
        raise SystemExit("Unexpected changelog header structure")
    entry = '''## 2026-07-21 — Глава 2C: удалена историческая цепочка Telegram-панели v25–v40

После переноса production-поведения в `bbvg/bot/*` файлы
`admin_panel_runtime_v25.py`–`admin_panel_runtime_v40.py` образовывали замкнутую
историческую лестницу. Production MRO их не использовал; внешние ссылки
оставались только в устаревших preflight, CI и recovery-контрактах.

Удалены 16 versioned-файлов и 5 394 строки. `preflight.py`, Control Center
validation, current checks, recovery smoke, private-state validation и System
Health переведены на `bbvg/bot/*`, `admin_panel_v2.py` и совместимую production-
команду `admin_panel_runtime_v41.py`. Добавлен отрицательный regression-контракт,
запрещающий возврат v25–v40. Callback-данные, порядок кнопок, JSON-state и
логика колёс не изменялись.

Глава построена поверх уже восстановленного зелёного baseline-коммита
`5d26c5e9afd3e168046cad5f012beb1079ba57fb`; его изменения не дублируются и не
откатываются.

Pre-update backup:
`backup/before-chapter-2c-legacy-panel-removal-2026-07-21` →
`ebd84b148a8b0aa6457106d729d86925a3a77393`.
Safety-точка после физического удаления:
`safety/chapter-2c-deletion-head-2026-07-21` →
`ef2a7661b7c70b8c26951079223f4a7c990a7651`.

Откат: вернуть merge главы целиком либо восстановить pre-update backup; не
восстанавливать отдельные versioned-файлы вручную поверх нового runtime.

'''
    changelog.write_text(text.replace(anchor, anchor + entry, 1), encoding="utf-8")

print("Chapter 2C unique changes transferred onto current main")
