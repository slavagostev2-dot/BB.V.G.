from __future__ import annotations

import re
from pathlib import Path

path = Path(__file__).resolve().parents[1] / "auto_participation_notifications.py"
text = path.read_text(encoding="utf-8")
replacement = '''def _result_message(
    key: str,
    item: dict[str, Any],
    accounts: dict[str, tuple[str, dict[str, Any], bool]],
) -> tuple[str, dict[str, Any]]:
    identifier = html.escape(str(item.get("identifier") or key))
    all_success = all(value[2] for value in accounts.values())
    lines: list[str] = []
    for account_key in (PRIMARY_ACCOUNT_KEY, SECONDARY_ACCOUNT_KEY):
        _token, record, success = accounts[account_key]
        _key, label = _account_identity(record)
        fallback_label = (
            PRIMARY_ACCOUNT_LABEL
            if account_key == PRIMARY_ACCOUNT_KEY
            else SECONDARY_ACCOUNT_LABEL
        )
        escaped_label = html.escape(label or fallback_label)
        if success:
            description = html.escape(_success_description(record))
            lines.append(f"✅ {escaped_label} — {description}")
        else:
            reason = html.escape(_failure_reason(record))
            lines.append(f"❌ {escaped_label} — {reason}")
    title = (
        "✅ <b>Участие принято</b>"
        if all_success
        else "⚠️ <b>Автоучастие выполнено не полностью</b>"
        if any(value[2] for value in accounts.values())
        else "⚠️ <b>Участие не принято</b>"
    )
    text = (
        f"{title}\\n\\n"
        f"Колесо: <code>{identifier}</code>\\n"
        + "\\n".join(lines)
    )
    return text, _navigation()
'''
pattern = r"^def _result_message\(.*?(?=^def sync_once\()"
updated, count = re.subn(
    pattern,
    lambda _match: replacement.rstrip() + "\n\n\n",
    text,
    count=1,
    flags=re.M | re.S,
)
if count != 1:
    raise RuntimeError(f"result message function matches={count}")
path.write_text(updated, encoding="utf-8")
print("auto participation result message repaired")
