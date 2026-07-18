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
start = text.find("    # Most wheel posts contain one identifier.")
end = text.find("\n    return messages_by_source, source_errors, empty_sources", start)
replacement = '''    # Preserve original messages in each source stream. Assessment wrappers
    # still use the canonical publication for API/deadline semantics, while
    # statistics and wheel_publications retain every posting channel.
    for source, messages in list(messages_by_source.items()):
        rewritten: list[monitor.Message] = []
        seen_messages: set[tuple[str, int]] = set()
        for message in messages:
            marker = (message.source.casefold(), message.message_id)
            if marker in seen_messages:
                continue
            seen_messages.add(marker)
            rewritten.append(message)
        messages_by_source[source] = rewritten
'''
if start < 0 or end < 0:
    raise RuntimeError("monitor_entry canonical rewrite marker missing")
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
