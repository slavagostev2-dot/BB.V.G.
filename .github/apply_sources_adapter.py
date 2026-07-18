from pathlib import Path

sources = Path("bbvg/bot/sources.py")
text = sources.read_text(encoding="utf-8")
text = text.replace(
    '_EMPTY_REGISTRY = {"version": 2, "summary": {}, "sources": []}',
    '_EMPTY_REGISTRY = {"version": 2, "generated_at": None, "summary": {}, "sources": []}',
    1,
)
old = '''        summary = value.get("summary")
        sources = value.get("sources")
        return {
            "version": max(2, int(value.get("version", 2) or 2)),
            "summary": summary if isinstance(summary, dict) else {},
            "sources": sources if isinstance(sources, list) else [],
        }
'''
new = '''        summary = value.get("summary")
        rows = value.get("sources")
        summary = summary if isinstance(summary, dict) else {}
        rows = rows if isinstance(rows, list) else []
        return {
            "version": max(2, int(value.get("version", 2) or 2)),
            "generated_at": str(value.get("generated_at") or "").strip() or None,
            "summary": dict(summary),
            "sources": [dict(row) for row in rows if isinstance(row, dict)],
        }
'''
if new not in text:
    if old not in text:
        raise RuntimeError("load_source_registry marker missing")
    text = text.replace(old, new, 1)
old = '''        return {
            "version": 2,
            "summary": {
'''
new = '''        generated = max(
            (str(row.get("last_checked_at") or "") for row in rows),
            default=None,
        )
        return {
            "version": 2,
            "generated_at": generated or None,
            "summary": {
'''
if new not in text:
    if old not in text:
        raise RuntimeError("source_registry_fallback marker missing")
    text = text.replace(old, new, 1)
sources.write_text(text, encoding="utf-8")

entry = Path("monitor_entry.py")
text = entry.read_text(encoding="utf-8")
helper = '''

def _preserve_source_messages(
    messages_by_source: dict[str, list[monitor.Message]],
) -> dict[str, list[monitor.Message]]:
    """Keep each Telegram post attributed to the channel that published it."""

    result: dict[str, list[monitor.Message]] = {}
    for source, messages in messages_by_source.items():
        rewritten: list[monitor.Message] = []
        seen_messages: set[tuple[str, int]] = set()
        for message in messages:
            marker = (message.source.casefold(), message.message_id)
            if marker in seen_messages:
                continue
            seen_messages.add(marker)
            rewritten.append(message)
        result[source] = rewritten
    return result
'''
if "def _preserve_source_messages(" not in text:
    marker = "\n\ndef fetch_all_sources_with_originals(sources):\n"
    if marker not in text:
        raise RuntimeError("fetch_all_sources_with_originals marker missing")
    text = text.replace(marker, helper + marker, 1)

start = text.find("    # Most wheel posts contain one identifier.")
if start < 0:
    start = text.find("    # Preserve original messages in each source stream.")
end = text.find("\n    return messages_by_source, source_errors, empty_sources", start)
replacement = '''    messages_by_source = _preserve_source_messages(messages_by_source)
'''
if start < 0 or end < 0:
    raise RuntimeError("monitor_entry source rewrite marker missing")
text = text[:start] + replacement + text[end:]
entry.write_text(text, encoding="utf-8")

core = Path(".github/apply_analytics_core.py")
text = core.read_text(encoding="utf-8")
start = text.find('replace_once(\n    "monitor_entry.py",')
end = text.find("helper = '''", start)
if start >= 0 and end > start:
    text = text[:start] + text[end:]
start = text.find('replace_once(\n    "bbvg/bot/sources.py",')
end = text.find('replace_once("tests/test_lifecycle.py"', start)
if start >= 0 and end > start:
    text = text[:start] + text[end:]
old_test = '''class MultiSourceDiscoveryTests(unittest.TestCase):
    def test_source_streams_keep_original_publications(self) -> None:
        current = datetime(2026, 7, 17, 10, 30, tzinfo=UTC)
        link = "https://betboom.ru/freestream/zonertg8"
        first = monitor.Message(
            "mechanogun", 500, current, link, "https://telegram.me/mechanogun/500"
        )
        second = monitor.Message(
            "kolesaBB", 131, current + timedelta(minutes=1), link,
            "https://telegram.me/kolesaBB/131",
        )
        original = monitor_entry._original_fetch_all_sources
        try:
            monitor_entry._original_fetch_all_sources = lambda sources: (
                {"mechanogun": [first], "kolesaBB": [second]}, {}, []
            )
            messages, _, _ = monitor_entry.fetch_all_sources_with_originals(
                ["mechanogun", "kolesaBB"]
            )
        finally:
            monitor_entry._original_fetch_all_sources = original

        self.assertEqual(messages["mechanogun"][0].source, "mechanogun")
        self.assertEqual(messages["kolesaBB"][0].source, "kolesaBB")
        self.assertEqual(
            [row["source"] for row in monitor_entry._WHEEL_PUBLICATIONS["zonertg8"]],
            ["mechanogun", "kolesaBB"],
        )
        self.assertEqual(monitor_entry._CANONICAL_MESSAGES["zonertg8"].source, "mechanogun")
'''
new_test = '''class MultiSourceDiscoveryTests(unittest.TestCase):
    def test_source_streams_keep_original_publications(self) -> None:
        current = datetime(2026, 7, 17, 10, 30, tzinfo=UTC)
        link = "https://betboom.ru/freestream/zonertg8"
        first = monitor.Message(
            "mechanogun", 500, current, link, "https://telegram.me/mechanogun/500"
        )
        second = monitor.Message(
            "kolesaBB", 131, current + timedelta(minutes=1), link,
            "https://telegram.me/kolesaBB/131",
        )
        messages = monitor_entry._preserve_source_messages(
            {"mechanogun": [first], "kolesaBB": [second, second]}
        )
        self.assertEqual(messages["mechanogun"][0].source, "mechanogun")
        self.assertEqual(messages["kolesaBB"][0].source, "kolesaBB")
        self.assertEqual(len(messages["kolesaBB"]), 1)
'''
if old_test in text:
    text = text.replace(old_test, new_test, 1)
elif new_test not in text:
    raise RuntimeError("multi-source lifecycle test marker missing")
core.write_text(text, encoding="utf-8")

ui = Path(".github/apply_analytics_ui.py")
text = ui.read_text(encoding="utf-8")
text = text.replace(
    '    "from admin_panel_runtime_v38 import TelegramPanelRuntimeV38\\n",\n    "from admin_panel_runtime_v38 import TelegramPanelRuntimeV38\\nfrom bbvg.bot.sources import load_source_registry, source_registry_fallback\\n",\n)',
    '    "from admin_panel_runtime_v38 import TelegramPanelRuntimeV38\\n",\n    "from admin_panel_runtime_v38 import TelegramPanelRuntimeV38\\n",\n)',
    1,
)
text = text.replace("registry = load_source_registry()", "registry = self.load_source_registry()")
text = text.replace("registry = source_registry_fallback(snap)", "registry = self.source_registry_fallback()")
text = text.replace('"\\n".join', '"\\\\n".join')
ui.write_text(text, encoding="utf-8")
