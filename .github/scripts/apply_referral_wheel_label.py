from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def replace_once(text: str, old: str, new: str, label: str) -> str:
    if new in text:
        return text
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected one anchor, found {count}")
    return text.replace(old, new, 1)


def replace_exact_count(
    text: str,
    old: str,
    new: str,
    expected: int,
    label: str,
) -> str:
    if new in text:
        return text
    count = text.count(old)
    if count != expected:
        raise SystemExit(f"{label}: expected {expected} anchors, found {count}")
    return text.replace(old, new, expected)


def patch_publication_contract() -> None:
    path = ROOT / "wheel_publications_v2.py"
    text = path.read_text(encoding="utf-8")
    if "import re\n" not in text:
        text = text.replace("from datetime import datetime, timezone\n", "from datetime import datetime, timezone\nimport re\n", 1)

    anchor = "UTC = timezone.utc\n"
    block = '''UTC = timezone.utc

REFERRAL_RESTRICTED_NOTICE_TEXT = (
    "Колесо только для рефералов. Для участия аккаунт должен быть зарегистрирован "
    "по реферальной ссылке или промокоду автора."
)
REFERRAL_RESTRICTED_NOTICE_HTML = (
    "⚠️ <b>Колесо только для рефералов</b>\\n"
    "Для участия аккаунт должен быть зарегистрирован по реферальной ссылке "
    "или промокоду автора."
)
REFERRAL_RESTRICTED_SHORT_HTML = "⚠️ <b>Колесо только для рефералов</b>"
_REFERRAL_RESTRICTION_PATTERNS = (
    re.compile(r"\\bтолько\\s+(?:для\\s+)?реф(?:ерал\\w*|ов)\\b", re.IGNORECASE),
    re.compile(r"\\b(?:для|моим?|нашим?)\\s+реферал\\w*\\b", re.IGNORECASE),
    re.compile(
        r"\\b(?:участ\\w*|доступ\\w*|колес\\w*)[^.\\n]{0,140}"
        r"\\b(?:только|лишь|исключительно)[^.\\n]{0,140}"
        r"\\b(?:реферал\\w*|реферальн\\w*\\s+ссылк\\w*|промокод\\w*)",
        re.IGNORECASE,
    ),
    re.compile(
        r"\\b(?:участ\\w*|доступ\\w*)[^.\\n]{0,140}"
        r"\\b(?:зарегистрирован\\w*|регистрац\\w*)[^.\\n]{0,120}"
        r"\\b(?:по|через)\\s+(?:моей\\s+|наш\\w*\\s+)?"
        r"(?:реферальн\\w*\\s+)?(?:ссылк\\w*|промокод\\w*)",
        re.IGNORECASE,
    ),
    re.compile(r"\\b(?:only\\s+for\\s+referrals?|referral[-\\s]?only)\\b", re.IGNORECASE),
)


def is_referral_restricted(text: str) -> bool:
    """Recognize an explicit referral/promo eligibility restriction in a post."""

    value = " ".join(str(text or "").split())
    return bool(value and any(pattern.search(value) for pattern in _REFERRAL_RESTRICTION_PATTERNS))


def entry_is_referral_restricted(entry: Any) -> bool:
    if not isinstance(entry, dict):
        return False
    if entry.get("referral_restricted") is True:
        return True
    return is_referral_restricted(str(entry.get("message_text") or ""))


def referral_restriction_notice(text: str, *, html_mode: bool = True) -> str:
    if not is_referral_restricted(text):
        return ""
    return REFERRAL_RESTRICTED_NOTICE_HTML if html_mode else REFERRAL_RESTRICTED_NOTICE_TEXT
'''
    if "REFERRAL_RESTRICTED_NOTICE_TEXT" not in text:
        text = replace_once(text, anchor, block, "publication referral contract")
    path.write_text(text, encoding="utf-8")


def patch_monitor() -> None:
    path = ROOT / "monitor.py"
    text = path.read_text(encoding="utf-8")
    if "import wheel_publications_v2\n" not in text:
        text = text.replace(
            "import telegram_transport\n",
            "import telegram_transport\nimport wheel_publications_v2\n",
            1,
        )

    old = '''            "button_token": button_context_token(message, link),
            "participating": is_participating(state, key),
'''
    new = '''            "button_token": button_context_token(message, link),
            "referral_restricted": (
                bool(entry.get("referral_restricted"))
                or wheel_publications_v2.is_referral_restricted(message.text)
            ),
            "participating": is_participating(state, key),
'''
    text = replace_once(text, old, new, "remember referral restriction")

    old = '''        lines.append(
            f"{index}. <code>{identifier}</code> — {html.escape(timing)} — {participation}\\n"
            f"   источник: @{source}"
        )
'''
    new = '''        referral = (
            "\\n   ⚠️ колесо только для рефералов"
            if wheel_publications_v2.entry_is_referral_restricted(entry)
            else ""
        )
        lines.append(
            f"{index}. <code>{identifier}</code> — {html.escape(timing)} — {participation}\\n"
            f"   источник: @{source}{referral}"
        )
'''
    text = replace_once(text, old, new, "legacy active wheel referral label")

    old = '''    verification = (
        "🟡 <b>Проверка активности временно недоступна</b>\\n"
        if verification_status == WHEEL_VERIFICATION_FAILED
        else ""
    )
    send_message(
'''
    new = '''    verification = (
        "🟡 <b>Проверка активности временно недоступна</b>\\n"
        if verification_status == WHEEL_VERIFICATION_FAILED
        else ""
    )
    referral_notice = wheel_publications_v2.referral_restriction_notice(message.text)
    referral_line = f"{referral_notice}\\n" if referral_notice else ""
    send_message(
'''
    text = replace_exact_count(text, old, new, 2, "new/activation referral notice")

    old = '        f"{verification}"\n        f"{timing}",\n'
    new = '        f"{verification}"\n        f"{referral_line}"\n        f"{timing}",\n'
    text = replace_exact_count(text, old, new, 2, "notification referral line")

    old = '''                    f"⏳ До прокрутки: <b>{html.escape(human_remaining(deadline))}</b>\\n\\n"
                    "Вы ещё не отметили участие.",
'''
    new = '''                    f"⏳ До прокрутки: <b>{html.escape(human_remaining(deadline))}</b>\\n"
                    + (
                        f"{wheel_publications_v2.REFERRAL_RESTRICTED_NOTICE_HTML}\\n\\n"
                        if wheel_publications_v2.entry_is_referral_restricted(entry)
                        else "\\n"
                    )
                    + "Вы ещё не отметили участие.",
'''
    text = replace_once(text, old, new, "known reminder referral notice")

    old = '''                    "⏳ Время прокрутки пока не найдено.\\n\\n"
                    "Вы ещё не отметили участие; следующее напоминание будет через 30 минут.",
'''
    new = '''                    "⏳ Время прокрутки пока не найдено.\\n"
                    + (
                        f"{wheel_publications_v2.REFERRAL_RESTRICTED_NOTICE_HTML}\\n\\n"
                        if wheel_publications_v2.entry_is_referral_restricted(entry)
                        else "\\n"
                    )
                    + "Вы ещё не отметили участие; следующее напоминание будет через 30 минут.",
'''
    text = replace_once(text, old, new, "unknown reminder referral notice")
    path.write_text(text, encoding="utf-8")


def patch_final_reminder() -> None:
    path = ROOT / "wheel_lifecycle_v2.py"
    text = path.read_text(encoding="utf-8")
    old = '''                    f"Источники: {_source_text(state, normalized, entry)}\\n"
                    f"⏳ Осталось: <b>{html.escape(monitor_module.human_remaining(deadline))}</b>\\n\\n"
                    "Вы ещё не отметили участие. Откройте колесо сейчас — оно скоро завершится.",
'''
    new = '''                    f"Источники: {_source_text(state, normalized, entry)}\\n"
                    f"⏳ Осталось: <b>{html.escape(monitor_module.human_remaining(deadline))}</b>\\n"
                    + (
                        f"{wheel_publications_v2.REFERRAL_RESTRICTED_NOTICE_HTML}\\n\\n"
                        if wheel_publications_v2.entry_is_referral_restricted(entry)
                        else "\\n"
                    )
                    + "Вы ещё не отметили участие. Откройте колесо сейчас — оно скоро завершится.",
'''
    text = replace_once(text, old, new, "final reminder referral notice")
    path.write_text(text, encoding="utf-8")


def patch_control_center() -> None:
    path = ROOT / "bbvg/bot/wheels.py"
    text = path.read_text(encoding="utf-8")
    if "import wheel_publications_v2\n" not in text:
        text = text.replace(
            "import telegram_ui\n",
            "import telegram_ui\nimport wheel_publications_v2\n",
            1,
        )
    old = '''                    f"📡 @{html.escape(source)}",
                    "✅ Участие отмечено" if joined else "❌ Участие не отмечено",
'''
    new = '''                    f"📡 @{html.escape(source)}",
                    *(
                        [wheel_publications_v2.REFERRAL_RESTRICTED_SHORT_HTML]
                        if wheel_publications_v2.entry_is_referral_restricted(item)
                        else []
                    ),
                    "✅ Участие отмечено" if joined else "❌ Участие не отмечено",
'''
    text = replace_once(text, old, new, "Control Center referral label")
    path.write_text(text, encoding="utf-8")


def patch_tests() -> None:
    path = ROOT / "tests/test_chapter5_lifecycle.py"
    text = path.read_text(encoding="utf-8")
    if "import monitor\n" not in text:
        text = text.replace("import admin_action_v3\n", "import admin_action_v3\nimport monitor\n", 1)
    if "from bbvg.bot.wheels import WheelInteractionRuntime\n" not in text:
        text = text.replace(
            "import wheel_lifecycle_v2\n",
            "import wheel_lifecycle_v2\nfrom bbvg.bot.wheels import WheelInteractionRuntime\n",
            1,
        )

    anchor = '''    def test_event_identity_stays_stable_when_a_second_source_is_merged(self) -> None:
'''
    block = '''    def test_referral_restriction_is_visible_in_notification_and_active_list(self) -> None:
        restricted = (
            "Участие доступно только пользователям, зарегистрированным "
            "по промокоду автора. https://betboom.ru/freestream/ref-wheel"
        )
        regular = (
            "Новое колесо BetBoom и промокод на бонус. "
            "https://betboom.ru/freestream/regular-wheel"
        )
        self.assertTrue(wheel_publications_v2.is_referral_restricted(restricted))
        self.assertFalse(wheel_publications_v2.is_referral_restricted(regular))

        message = monitor.Message(
            source="refsource",
            message_id=77,
            date=datetime(2026, 7, 22, 8, 0, tzinfo=UTC),
            text=restricted,
            message_url="https://telegram.me/refsource/77",
        )
        state: dict[str, Any] = {
            "active_wheels": {},
            "participating_wheels": {},
            "wheel_action_history": {},
            "button_contexts": {},
        }
        monitor.remember_active_wheel(
            state,
            message,
            "https://betboom.ru/freestream/ref-wheel",
            None,
            "active",
            "test",
        )
        entry = state["active_wheels"]["ref-wheel"]
        self.assertTrue(entry["referral_restricted"])
        self.assertIn("только для рефералов", monitor.active_wheels_text(state).casefold())

        sent: list[str] = []
        original_send = monitor.send_message
        try:
            monitor.send_message = lambda text, **kwargs: sent.append(text) or {"ok": True}  # type: ignore[assignment]
            monitor.notify_new_link(
                message,
                "https://betboom.ru/freestream/ref-wheel",
                None,
                "test",
                [],
            )
        finally:
            monitor.send_message = original_send
        self.assertIn("Колесо только для рефералов", sent[0])

        panel_messages: list[str] = []
        panel = WheelInteractionRuntime.__new__(WheelInteractionRuntime)
        panel._collect_current_wheels = lambda: [dict(entry, _key="ref-wheel")]  # type: ignore[method-assign]
        panel.snapshot = lambda force=False: SimpleNamespace(state={"participating_wheels": {}})  # type: ignore[method-assign]
        panel._joined_wheel_keys = lambda snap: set()  # type: ignore[method-assign]
        panel.is_admin = lambda: False  # type: ignore[method-assign]
        panel.parse_dt = lambda value: None  # type: ignore[method-assign]
        panel.remaining = lambda value: ""  # type: ignore[method-assign]
        panel.with_nav = lambda rows=None: {"inline_keyboard": rows or []}  # type: ignore[method-assign]
        panel.send = lambda text, **kwargs: panel_messages.append(text) or {}  # type: ignore[method-assign]
        panel.show_active()
        self.assertIn("Колесо только для рефералов", panel_messages[0])

'''
    if "test_referral_restriction_is_visible_in_notification_and_active_list" not in text:
        text = replace_once(text, anchor, block + anchor, "referral restriction regression test")
    path.write_text(text, encoding="utf-8")


def patch_changelog() -> None:
    path = ROOT / "docs/PROJECT_CHANGELOG_RU.md"
    text = path.read_text(encoding="utf-8")
    heading = "## 2026-07-22 — Реферальные колёса получили явную приписку"
    if heading in text:
        return
    marker = "---\n\n"
    entry = '''## 2026-07-22 — Реферальные колёса получили явную приписку

Публикации, в которых участие явно ограничено рефералами либо регистрацией по реферальной ссылке или промокоду автора, теперь получают признак `referral_restricted`. В первичном уведомлении, уведомлении об активации и напоминаниях показывается предупреждение: «Колесо только для рефералов. Для участия аккаунт должен быть зарегистрирован по реферальной ссылке или промокоду автора».

Признак сохраняется в `active_wheels`, поэтому та же отметка видна в разделе «Активные колёса» даже после восстановления состояния. Обычное упоминание промокода без условия участия не помечает колесо как реферальное. Callback-данные, дедупликация, рейтинг и логика автоучастия не изменены.

**Pre-update backup:** `backup/2026-07-22-before-referral-wheel-label` → `cab645a3e3fdd2dc838b6cf156c23e7ef689c3e6`.

'''
    if marker not in text:
        raise SystemExit("changelog insertion marker not found")
    path.write_text(text.replace(marker, marker + entry, 1), encoding="utf-8")


def main() -> None:
    patch_publication_contract()
    patch_monitor()
    patch_final_reminder()
    patch_control_center()
    patch_tests()
    patch_changelog()


if __name__ == "__main__":
    main()
