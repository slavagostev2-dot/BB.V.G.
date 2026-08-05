from __future__ import annotations

import copy
from typing import Any, Callable

from bbvg.bot.users import UserSettingsMixin

INTEGRATION_VERSION = 3
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


def _notification_options_for_role(
    role: str,
) -> tuple[tuple[str, str, str], ...]:
    normalized_role = str(role or "").casefold()
    values = [
        item
        for item in UserSettingsMixin._notification_options_for_role(role)
        if str(item[0]) != AUTO_NOTIFICATION_KEY
    ]
    if normalized_role == "owner":
        values.append(
            (
                AUTO_NOTIFICATION_KEY,
                AUTO_NOTIFICATION_LABEL,
                AUTO_NOTIFICATION_DESCRIPTION,
            )
        )
    return tuple(values)


def _is_owner(access: dict[str, Any], user_id: str) -> bool:
    owner_id = str(access.get("owner_id") or "").strip()
    return bool(owner_id and str(user_id or "").strip() == owner_id)


def install(panel_class: type[Any]) -> None:
    if getattr(panel_class, "_bbvg_xflarxx_runtime_integration_installed", False):
        return

    panel_class._notification_options_for_role = staticmethod(
        _notification_options_for_role
    )

    original_preferences: Callable = panel_class.notification_preferences
    original_toggle: Callable = panel_class.toggle_notification
    original_show_settings: Callable = panel_class.show_settings
    original_render_page: Callable = panel_class.render_page

    def notification_preferences(
        self: Any,
        user_id: str | None = None,
    ) -> dict[str, bool]:
        result = dict(original_preferences(self, user_id))
        target = str(user_id or self.current_user_id or "")
        access = self.load_access()
        if not _is_owner(access, target):
            result[AUTO_NOTIFICATION_KEY] = False
        return result

    def toggle_notification(self: Any, key: str) -> None:
        if key == AUTO_NOTIFICATION_KEY:
            access = self.load_access()
            if not _is_owner(access, str(self.current_user_id or "")):
                raise PermissionError(
                    "Уведомления об автоучастии доступны только владельцу аккаунтов"
                )
        original_toggle(self, key)

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

    panel_class.notification_preferences = notification_preferences
    panel_class.toggle_notification = toggle_notification
    panel_class.show_settings = show_settings
    panel_class.render_page = render_page
    panel_class._bbvg_xflarxx_runtime_integration_installed = True


def self_test() -> None:
    assert INTEGRATION_VERSION == 3
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
    assert any(
        item[0] == AUTO_NOTIFICATION_KEY
        for item in _notification_options_for_role("owner")
    )
    assert not any(
        item[0] == AUTO_NOTIFICATION_KEY
        for item in _notification_options_for_role("user")
    )
    assert not any(
        item[0] == AUTO_NOTIFICATION_KEY
        for item in _notification_options_for_role("admin")
    )
    assert _is_owner({"owner_id": "1"}, "1")
    assert not _is_owner({"owner_id": "1"}, "2")

    class DummyPanel:
        current_user_id = "2"

        def notification_preferences(
            self,
            _user_id: str | None = None,
        ) -> dict[str, bool]:
            return {AUTO_NOTIFICATION_KEY: True}

        def toggle_notification(self, key: str) -> None:
            self.toggled = key

        def show_settings(self) -> None:
            return None

        def render_page(self, _page: str) -> None:
            return None

        def load_access(self) -> dict[str, Any]:
            return {"owner_id": "1"}

        def send(self, _text: str, **_kwargs: Any) -> dict:
            return {"ok": True}

        def _normalize_page(self, page: str) -> str:
            return page

    install(DummyPanel)
    user_panel = DummyPanel()
    assert user_panel.notification_preferences()[AUTO_NOTIFICATION_KEY] is False
    try:
        user_panel.toggle_notification(AUTO_NOTIFICATION_KEY)
    except PermissionError:
        pass
    else:
        raise AssertionError("A regular user must not enable owner auto notifications")

    owner_panel = DummyPanel()
    owner_panel.current_user_id = "1"
    assert owner_panel.notification_preferences()[AUTO_NOTIFICATION_KEY] is True
    owner_panel.toggle_notification(AUTO_NOTIFICATION_KEY)
    assert owner_panel.toggled == AUTO_NOTIFICATION_KEY
    print("xFLARXx runtime integration self-test passed")


if __name__ == "__main__":
    self_test()
