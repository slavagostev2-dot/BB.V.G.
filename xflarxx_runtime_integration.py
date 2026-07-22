from __future__ import annotations

import copy
from typing import Any, Callable

from bbvg.bot.users import UserSettingsMixin

INTEGRATION_VERSION = 1
AUTO_NOTIFICATION_KEY = "auto_participation"
AUTO_NOTIFICATION_LABEL = "🤖 Автоучастие"
AUTO_NOTIFICATION_DESCRIPTION = "Итоги автоматического участия в колёсах"
REMOVED_SETTINGS_CALLBACKS = {"page:wheelmode", "page:disabled_features"}


def _without_removed_settings(
    reply_markup: dict[str, Any] | None,
) -> dict[str, Any] | None:
    if not isinstance(reply_markup, dict):
        return reply_markup
    result = copy.deepcopy(reply_markup)
    rows: list[list[dict[str, Any]]] = []
    for row in result.get("inline_keyboard", []):
        if not isinstance(row, list):
            continue
        filtered = [
            dict(button)
            for button in row
            if isinstance(button, dict)
            and str(button.get("callback_data") or "")
            not in REMOVED_SETTINGS_CALLBACKS
        ]
        if filtered:
            rows.append(filtered)
    result["inline_keyboard"] = rows
    return result


def install(panel_class: type[Any]) -> None:
    if getattr(panel_class, "_bbvg_xflarxx_runtime_integration_installed", False):
        return

    def notification_options_for_role(
        role: str,
    ) -> tuple[tuple[str, str, str], ...]:
        values = list(UserSettingsMixin._notification_options_for_role(role))
        if not any(str(item[0]) == AUTO_NOTIFICATION_KEY for item in values):
            values.append(
                (
                    AUTO_NOTIFICATION_KEY,
                    AUTO_NOTIFICATION_LABEL,
                    AUTO_NOTIFICATION_DESCRIPTION,
                )
            )
        return tuple(values)

    panel_class._notification_options_for_role = staticmethod(
        notification_options_for_role
    )

    original_show_settings: Callable = panel_class.show_settings
    original_render_page: Callable = panel_class.render_page

    def show_settings(self: Any) -> None:
        original_send = self.send

        def filtered_send(
            text: str,
            *,
            reply_markup: dict[str, Any] | None = None,
            chat_id: str | None = None,
        ) -> dict:
            return original_send(
                text,
                reply_markup=_without_removed_settings(reply_markup),
                chat_id=chat_id,
            )

        self.send = filtered_send
        try:
            original_show_settings(self)
        finally:
            self.send = original_send

    def render_page(self: Any, page: str) -> None:
        normalized = self._normalize_page(page)
        if normalized in {"wheelmode", "disabled_features"}:
            self.show_settings()
            return
        original_render_page(self, normalized)

    panel_class.show_settings = show_settings
    panel_class.render_page = render_page
    panel_class._bbvg_xflarxx_runtime_integration_installed = True


def self_test() -> None:
    assert INTEGRATION_VERSION == 1
    markup = {
        "inline_keyboard": [
            [{"text": "Уведомления", "callback_data": "page:notifications"}],
            [{"text": "API", "callback_data": "page:wheelmode"}],
            [{"text": "Отключено", "callback_data": "page:disabled_features"}],
            [{"text": "Назад", "callback_data": "page:menu"}],
        ]
    }
    cleaned = _without_removed_settings(markup)
    assert cleaned is not None
    callbacks = {
        str(button.get("callback_data") or "")
        for row in cleaned["inline_keyboard"]
        for button in row
    }
    assert not callbacks & REMOVED_SETTINGS_CALLBACKS
    options = list(UserSettingsMixin._notification_options_for_role("owner"))
    options.append(
        (
            AUTO_NOTIFICATION_KEY,
            AUTO_NOTIFICATION_LABEL,
            AUTO_NOTIFICATION_DESCRIPTION,
        )
    )
    assert any(item[0] == AUTO_NOTIFICATION_KEY for item in options)
    print("xFLARXx runtime integration self-test passed")


if __name__ == "__main__":
    self_test()
