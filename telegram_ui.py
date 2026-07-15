from __future__ import annotations

import re
from typing import Any


TELEGRAM_TEXT_LIMIT = 4096
TELEGRAM_CALLBACK_LIMIT = 64
MAX_COMPACT_BUTTONS_PER_ROW = 3
_TOKEN_RE = re.compile(
    r"(<[^>\n]+>|&(?:#[0-9]+|#x[0-9A-Fa-f]+|[A-Za-z][A-Za-z0-9]+);)"
)
_TAG_RE = re.compile(r"<\s*(/?)\s*([A-Za-z][A-Za-z0-9-]*)[^>]*>\s*$")
_VOID_TAGS = {"br", "hr"}


def truncate_telegram_html(
    value: str,
    limit: int = TELEGRAM_TEXT_LIMIT,
    suffix: str = "\n\n…",
) -> str:
    """Truncate Telegram HTML without cutting a tag or an entity in half."""

    text = str(value or "")
    if len(text) <= limit:
        return text
    if limit <= len(suffix):
        return suffix[:limit]

    output: list[str] = []
    opened: list[str] = []
    truncated = False

    def closing_text(stack: list[str] | None = None) -> str:
        target = opened if stack is None else stack
        return "".join(f"</{name}>" for name in reversed(target))

    parts = _TOKEN_RE.split(text)
    for part in parts:
        if not part:
            continue
        tag_match = _TAG_RE.fullmatch(part)
        if tag_match:
            closing, name = tag_match.groups()
            name = name.casefold()
            prospective = list(opened)
            if closing:
                if name in prospective:
                    reverse_index = prospective[::-1].index(name)
                    prospective.pop(len(prospective) - 1 - reverse_index)
            elif name not in _VOID_TAGS and not part.rstrip().endswith("/>"):
                prospective.append(name)
            budget = limit - len(suffix) - len(closing_text(prospective))
            if sum(map(len, output)) + len(part) > budget:
                truncated = True
                break
            output.append(part)
            opened = prospective
            continue

        budget = limit - len(suffix) - len(closing_text())
        used = sum(map(len, output))
        available = budget - used
        if available <= 0:
            truncated = True
            break
        if len(part) <= available:
            output.append(part)
            continue

        cut = part[:available]
        # Prefer a natural boundary when that does not discard most of the room.
        natural = max(cut.rfind("\n"), cut.rfind(" "))
        if natural >= max(1, available // 2):
            cut = cut[:natural].rstrip()
        output.append(cut)
        truncated = True
        break

    if not truncated:
        # The raw input exceeded the limit but token accounting happened to fit.
        result = "".join(output) + closing_text()
        return result[:limit]

    result = "".join(output).rstrip() + closing_text() + suffix
    return result[:limit]


def markup_issues(
    markup: dict[str, Any] | None,
    *,
    max_buttons_per_row: int = MAX_COMPACT_BUTTONS_PER_ROW,
) -> list[str]:
    """Return human-readable Bot API and compact-phone markup violations."""

    if not markup:
        return []
    rows = markup.get("inline_keyboard")
    if not isinstance(rows, list):
        return ["inline_keyboard is not a list"]

    issues: list[str] = []
    seen: set[tuple[str, str]] = set()
    for row_index, row in enumerate(rows):
        if not isinstance(row, list) or not row:
            issues.append(f"row {row_index} is empty or invalid")
            continue
        if len(row) > max_buttons_per_row:
            issues.append(f"row {row_index} has {len(row)} buttons")
        for button_index, button in enumerate(row):
            if not isinstance(button, dict):
                issues.append(f"button {row_index}:{button_index} is invalid")
                continue
            label = str(button.get("text") or "").strip()
            if not label:
                issues.append(f"button {row_index}:{button_index} has no text")
            targets = [
                (name, button.get(name))
                for name in ("callback_data", "url", "web_app")
                if button.get(name)
            ]
            if len(targets) != 1:
                issues.append(
                    f"button {row_index}:{button_index} must have exactly one target"
                )
                continue
            target_name, target_value = targets[0]
            if target_name == "web_app":
                identity_value = str(target_value.get("url") if isinstance(target_value, dict) else target_value)
            else:
                identity_value = str(target_value)
            if (
                target_name == "callback_data"
                and len(identity_value.encode("utf-8")) > TELEGRAM_CALLBACK_LIMIT
            ):
                issues.append(f"callback {row_index}:{button_index} exceeds 64 bytes")
            identity = (target_name, identity_value)
            if identity in seen:
                issues.append(f"duplicate target {target_name}:{identity_value}")
            seen.add(identity)
    return issues


def self_test() -> None:
    long_value = "<b>Заголовок</b>\n<code>" + ("данные &amp; " * 600) + "</code>"
    clipped = truncate_telegram_html(long_value)
    assert len(clipped) <= TELEGRAM_TEXT_LIMIT
    assert clipped.endswith("…")
    assert clipped.count("<code>") == clipped.count("</code>")
    assert not markup_issues(
        {
            "inline_keyboard": [
                [{"text": "Открыть", "callback_data": "page:active"}],
                [{"text": "Сайт", "url": "https://example.com"}],
            ]
        }
    )


if __name__ == "__main__":
    self_test()
    print("telegram UI helpers self-test passed")
