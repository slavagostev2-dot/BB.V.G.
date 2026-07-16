from pathlib import Path


def replace_once(path_text: str, old: str, new: str) -> None:
    path = Path(path_text)
    text = path.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise SystemExit(
            f"{path_text}: expected one occurrence of {old!r}, found {count}"
        )
    path.write_text(text.replace(old, new), encoding="utf-8")


replace_once(
    "bbvg/bot/runtime.py",
    "    from admin_panel_runtime_v41 import TelegramPanelRuntimeV41\n\n",
    "",
)
replace_once(
    "bbvg/bot/runtime.py",
    """    legacy_capture: list[tuple[str, dict[str, Any]]] = []
    current_capture: list[tuple[str, dict[str, Any]]] = []
    legacy = TelegramPanelRuntimeV41()
    current = TelegramPanelRuntime()
    _configured_panel(legacy, legacy_capture)
    _configured_panel(current, current_capture)
    legacy.show_active()
    current.show_active()
    assert current_capture[-1] == legacy_capture[-1]
""",
    """    current_capture: list[tuple[str, dict[str, Any]]] = []
    current = TelegramPanelRuntime()
    _configured_panel(current, current_capture)
    current.show_active()
    assert current_capture
""",
)

replace_once(
    "tests/test_current_contracts.py",
    "import admin_panel_runtime_v41\n",
    "",
)
replace_once(
    "tests/test_current_contracts.py",
    "    def test_runtime_chain_contracts_used_by_v41(self) -> None:\n",
    "    def test_current_runtime_chain_contracts(self) -> None:\n",
)
replace_once(
    "tests/test_current_contracts.py",
    "        admin_panel_runtime_v41.self_test()\n",
    "",
)

replace_once(
    "tests/production_acceptance.py",
    "from admin_panel_runtime_v41 import TelegramPanelRuntimeV41, self_test as panel_self_test\n",
    "from bbvg.bot.runtime import TelegramPanelRuntime, self_test as panel_self_test\n",
)
production_path = Path("tests/production_acceptance.py")
production = production_path.read_text(encoding="utf-8")
if production.count("TelegramPanelRuntimeV41") != 4:
    raise SystemExit(
        "tests/production_acceptance.py: unexpected TelegramPanelRuntimeV41 count "
        f"{production.count('TelegramPanelRuntimeV41')}"
    )
production = production.replace("TelegramPanelRuntimeV41", "TelegramPanelRuntime")
production = production.replace(
    '    assert "run: python admin_panel_runtime_v41.py" in workflow\n',
    '    assert "run: python -m bbvg.bot.runtime" in workflow\n',
)
production = production.replace(
    '    assert "admin_panel_runtime_v41.py" in workflow\n',
    '    assert "admin_panel_runtime_v41.py" not in workflow\n',
)
production_path.write_text(production, encoding="utf-8")

admin_path = Path(".github/workflows/admin-bot.yml")
admin = admin_path.read_text(encoding="utf-8")
admin_replacements = {
    "admin_panel_runtime_v38.py admin_panel_runtime_v41.py \\\n": "admin_panel_runtime_v38.py \\\n",
    "          python admin_panel_runtime_v41.py --self-test\n": "",
    "          from admin_panel_runtime_v41 import TelegramPanelRuntimeV41\n": "",
    "          assert TelegramPanelRuntimeV41.RUNTIME_VERSION == 41\n": (
        "          assert TelegramPanelRuntime.RUNTIME_VERSION == 41\n"
    ),
    "for row in TelegramPanelRuntimeV41.source_menu_rows(False)": (
        "for row in TelegramPanelRuntime.source_menu_rows(False)"
    ),
    "      - name: Run BB V.G. control center v41\n": (
        "      - name: Run BB V.G. control center\n"
    ),
    "        run: python admin_panel_runtime_v41.py\n": (
        "        run: python -m bbvg.bot.runtime\n"
    ),
}
for old, new in admin_replacements.items():
    count = admin.count(old)
    if count != 1:
        raise SystemExit(f"admin-bot.yml: expected one {old!r}, found {count}")
    admin = admin.replace(old, new)
admin_path.write_text(admin, encoding="utf-8")

for workflow_path in (
    ".github/workflows/bot-recovery-smoke.yml",
    ".github/workflows/v22-checks.yml",
    ".github/workflows/validate-private-state.yml",
):
    path = Path(workflow_path)
    text = path.read_text(encoding="utf-8")
    token = "admin_panel_runtime_v41.py "
    if text.count(token) != 1:
        raise SystemExit(
            f"{workflow_path}: expected one compile token, found {text.count(token)}"
        )
    text = text.replace(token, "")
    self_test_line = "          python admin_panel_runtime_v41.py --self-test\n"
    if text.count(self_test_line) != 1:
        raise SystemExit(
            f"{workflow_path}: expected one self-test line, found {text.count(self_test_line)}"
        )
    text = text.replace(self_test_line, "")
    text = text.replace(
        "run: python admin_panel_runtime_v41.py",
        "run: python -m bbvg.bot.runtime",
    )
    path.write_text(text, encoding="utf-8")

replace_once(
    "AGENTS.md",
    "- Telegram-панель: `bbvg/bot/runtime.py` через временные совместимые runtime-слои; production-совместимая команда пока остаётся `admin_panel_runtime_v41.py`.\n",
    "- Telegram-панель: `python -m bbvg.bot.runtime` (`bbvg/bot/runtime.py`) через оставшиеся совместимые runtime-слои.\n",
)

replace_once(
    "docs/CODE_INVENTORY_RU.md",
    "- Telegram-панель: `.github/workflows/admin-bot.yml` → `admin_panel_runtime_v41.py`;\n",
    "- Telegram-панель: `.github/workflows/admin-bot.yml` → `python -m bbvg.bot.runtime`;\n",
)
replace_once(
    "docs/CODE_INVENTORY_RU.md",
    """Обнаружено 40 файлов `admin_panel_runtime_v*.py`.

- 38 файлов входят в импортную цепочку текущей v41;
- `admin_panel_runtime_v23.py` и `admin_panel_runtime_v24.py` не входят в текущую цепочку;
- v41 наследует v40, v40 наследует v39, далее цепочка охватывает почти всю историю проекта;
- несколько старых классов импортируются напрямую тестами, `preflight.py` и `system_checks.py`.
""",
    """Исходно было обнаружено 40 файлов `admin_panel_runtime_v*.py`. После поэтапного переноса осталось 19 исторических runtime-файлов, и все они входят в фактическую цепочку единого `bbvg.bot.runtime`.

- production и acceptance запускают модуль без номера версии;
- исторические alias-файлы удаляются только после перевода всех workflow, тестов и эксплуатационных проверок;
- актуальная MRO и владельцы методов находятся в `docs/RUNTIME_METHOD_INVENTORY_RU.md`.
""",
)

replace_once(
    "tools/cleanup_audit.py",
    '    current = "admin_panel_runtime_v41"\n',
    '    current = "bbvg.bot.runtime"\n',
)
