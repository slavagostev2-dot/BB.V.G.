from pathlib import Path


def replace(path_text: str, old: str, new: str) -> None:
    path = Path(path_text)
    text = path.read_text(encoding="utf-8")
    if old in text:
        path.write_text(text.replace(old, new), encoding="utf-8")


replace(
    "bbvg/bot/runtime.py",
    "    from admin_panel_runtime_v41 import TelegramPanelRuntimeV41\n\n",
    "",
)
replace(
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
replace("tests/test_current_contracts.py", "import admin_panel_runtime_v41\n", "")
replace(
    "tests/test_current_contracts.py",
    "    def test_runtime_chain_contracts_used_by_v41(self) -> None:\n",
    "    def test_current_runtime_chain_contracts(self) -> None:\n",
)
replace(
    "tests/test_current_contracts.py",
    "        admin_panel_runtime_v41.self_test()\n",
    "",
)
replace(
    "tests/production_acceptance.py",
    "from admin_panel_runtime_v41 import TelegramPanelRuntimeV41, self_test as panel_self_test\n",
    "from bbvg.bot.runtime import TelegramPanelRuntime, self_test as panel_self_test\n",
)
production_path = Path("tests/production_acceptance.py")
production = production_path.read_text(encoding="utf-8")
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
for old, new in {
    "admin_panel_runtime_v38.py admin_panel_runtime_v41.py \\\n": "admin_panel_runtime_v38.py \\\n",
    "          python admin_panel_runtime_v41.py --self-test\n": "",
    "          from admin_panel_runtime_v41 import TelegramPanelRuntimeV41\n": "",
    "          assert TelegramPanelRuntimeV41.RUNTIME_VERSION == 41\n": "          assert TelegramPanelRuntime.RUNTIME_VERSION == 41\n",
    "TelegramPanelRuntimeV41.source_menu_rows(False)": "TelegramPanelRuntime.source_menu_rows(False)",
    "      - name: Run BB V.G. control center v41\n": "      - name: Run BB V.G. control center\n",
    "        run: python admin_panel_runtime_v41.py\n": "        run: python -m bbvg.bot.runtime\n",
}.items():
    admin = admin.replace(old, new)
admin_path.write_text(admin, encoding="utf-8")

for workflow_path in (
    ".github/workflows/bot-recovery-smoke.yml",
    ".github/workflows/v22-checks.yml",
    ".github/workflows/validate-private-state.yml",
):
    path = Path(workflow_path)
    text = path.read_text(encoding="utf-8")
    text = text.replace("admin_panel_runtime_v41.py ", "")
    text = text.replace("          python admin_panel_runtime_v41.py --self-test\n", "")
    text = text.replace(
        "run: python admin_panel_runtime_v41.py",
        "run: python -m bbvg.bot.runtime",
    )
    path.write_text(text, encoding="utf-8")

replace(
    "AGENTS.md",
    "- Telegram-панель: `bbvg/bot/runtime.py` через временные совместимые runtime-слои; production-совместимая команда пока остаётся `admin_panel_runtime_v41.py`.\n",
    "- Telegram-панель: `python -m bbvg.bot.runtime` (`bbvg/bot/runtime.py`) через оставшиеся совместимые runtime-слои.\n",
)
replace(
    "docs/CODE_INVENTORY_RU.md",
    "- Telegram-панель: `.github/workflows/admin-bot.yml` → `admin_panel_runtime_v41.py`;\n",
    "- Telegram-панель: `.github/workflows/admin-bot.yml` → `python -m bbvg.bot.runtime`;\n",
)
replace(
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
replace(
    "tools/cleanup_audit.py",
    '    current = "admin_panel_runtime_v41"\n',
    '    current = "bbvg.bot.runtime"\n',
)
