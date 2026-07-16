from pathlib import Path

path = Path("bbvg/bot/interface.py")
text = path.read_text(encoding="utf-8")
replacements = {
    "from admin_panel_runtime_v13 import TelegramPanelRuntimeV13\n": (
        "from admin_panel_runtime_v9 import TelegramPanelRuntimeV9\n"
        "from bbvg.bot.foundation import PanelFoundationMixin\n"
    ),
    "class PanelInterfaceRuntime(TelegramPanelRuntimeV13):\n": (
        "class PanelInterfaceRuntime(PanelFoundationMixin, TelegramPanelRuntimeV9):\n"
    ),
}
for old, new in replacements.items():
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"Expected one occurrence of {old!r}, found {count}")
    text = text.replace(old, new)
path.write_text(text, encoding="utf-8")
