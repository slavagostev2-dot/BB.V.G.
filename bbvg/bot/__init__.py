from __future__ import annotations

from typing import Any

import personal_wheel_voting
from bbvg.bot import profile as hunter_profile
from bbvg.bot.users import UserManagementRuntime

# Bot-only composition. Monitoring and notification modules do not import this
# package initializer, so profile UI cannot become a dependency of wheel checks.
hunter_profile.install(personal_wheel_voting.PersonalWheelVotingMixin)

# Keep the historic static class API used by menu validation and tests. The
# profile row is appended after every existing row, so previous button order is
# unchanged.
if "compact_menu_rows" in personal_wheel_voting.PersonalWheelVotingMixin.__dict__:
    delattr(personal_wheel_voting.PersonalWheelVotingMixin, "compact_menu_rows")

if not getattr(UserManagementRuntime, "_bbvg_hunter_profile_menu_installed", False):
    _base_compact_menu_rows = UserManagementRuntime.compact_menu_rows

    def _compact_menu_rows_with_profile(admin: bool) -> list[list[dict[str, Any]]]:
        rows = [list(row) for row in _base_compact_menu_rows(admin)]
        rows.append([{"text": "👤 Мой профиль", "callback_data": "page:profile"}])
        return rows

    UserManagementRuntime.compact_menu_rows = staticmethod(_compact_menu_rows_with_profile)
    UserManagementRuntime._bbvg_hunter_profile_menu_installed = True
