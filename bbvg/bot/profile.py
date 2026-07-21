from __future__ import annotations

import html
import os
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

UTC = timezone.utc
SUCCESSFUL_AUTO_STATUSES = frozenset({"participated", "already_participating"})


def _display_timezone() -> ZoneInfo:
    try:
        return ZoneInfo(os.getenv("DISPLAY_TIMEZONE", "Asia/Barnaul"))
    except Exception:
        return ZoneInfo("UTC")


def parse_time(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.astimezone(UTC) if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def canonical_event_key(value: object) -> str:
    """Normalize personal-vote and browser-worker identities to one event key."""

    raw = str(value or "").strip().casefold()
    if not raw:
        return ""
    if "#action:" in raw:
        wheel, tail = raw.split("#action:", 1)
        action = tail.split(":", 1)[0].strip()
        return f"{wheel}#action:{action}" if action else wheel
    for marker in ("#generation:", "#event:"):
        if marker in raw:
            wheel, tail = raw.split(marker, 1)
            identity = tail.split(":", 1)[0].strip()
            return f"{wheel}#id:{identity}" if identity else wheel
    if "#seen:" in raw:
        wheel, tail = raw.split("#seen:", 1)
        return f"{wheel}#seen:{tail.strip()}"
    return raw


def _event_time(entry: dict[str, Any], *fields: str) -> datetime | None:
    for field in fields:
        parsed = parse_time(entry.get(field))
        if parsed is not None:
            return parsed
    return None


def collect_participation_events(
    stats: dict[str, Any],
    state: dict[str, Any],
    *,
    actor: str,
    include_auto: bool,
) -> list[dict[str, Any]]:
    events: dict[str, dict[str, Any]] = {}
    votes = stats.get("personal_wheel_votes")
    votes = votes if isinstance(votes, dict) else {}
    normalized_actor = str(actor or "").casefold()

    for vote in votes.values():
        if not isinstance(vote, dict):
            continue
        if str(vote.get("actor") or "").casefold() != normalized_actor:
            continue
        key = canonical_event_key(vote.get("event_key") or vote.get("wheel_key"))
        if not key:
            continue
        timestamp = _event_time(vote, "voted_at")
        row = events.setdefault(
            key,
            {
                "event_key": key,
                "wheel_key": str(vote.get("wheel_key") or key.split("#", 1)[0]),
                "methods": set(),
                "timestamps": [],
            },
        )
        row["methods"].add("manual")
        if timestamp is not None:
            row["timestamps"].append(timestamp)

    if include_auto:
        auto = state.get("auto_participation_events")
        auto = auto if isinstance(auto, dict) else {}
        for token, record in auto.items():
            if not isinstance(record, dict):
                continue
            if str(record.get("status") or "") not in SUCCESSFUL_AUTO_STATUSES:
                continue
            key = canonical_event_key(token)
            if not key:
                key = canonical_event_key(record.get("wheel_key"))
            if not key:
                continue
            timestamp = _event_time(record, "attempted_at", "recorded_at")
            row = events.setdefault(
                key,
                {
                    "event_key": key,
                    "wheel_key": str(record.get("wheel_key") or key.split("#", 1)[0]),
                    "methods": set(),
                    "timestamps": [],
                },
            )
            row["methods"].add("auto")
            if timestamp is not None:
                row["timestamps"].append(timestamp)

    result: list[dict[str, Any]] = []
    for row in events.values():
        timestamps = row.pop("timestamps", [])
        methods = row.pop("methods", set())
        result.append(
            {
                **row,
                "method": "auto" if "auto" in methods else "manual",
                "participated_at": min(timestamps).isoformat() if timestamps else None,
            }
        )
    result.sort(key=lambda row: str(row.get("participated_at") or ""))
    return result


def participation_day_streaks(
    events: list[dict[str, Any]], *, current: datetime | None = None
) -> tuple[int, int]:
    timezone_value = _display_timezone()
    days = sorted(
        {
            parsed.astimezone(timezone_value).date()
            for parsed in (parse_time(row.get("participated_at")) for row in events)
            if parsed is not None
        }
    )
    if not days:
        return 0, 0

    best = 1
    run = 1
    for previous, following in zip(days, days[1:]):
        if following == previous + timedelta(days=1):
            run += 1
            best = max(best, run)
        else:
            run = 1

    today = (current or datetime.now(UTC)).astimezone(timezone_value).date()
    latest = days[-1]
    if latest not in {today, today - timedelta(days=1)}:
        return 0, best
    current_run = 1
    for index in range(len(days) - 1, 0, -1):
        if days[index] == days[index - 1] + timedelta(days=1):
            current_run += 1
        else:
            break
    return current_run, best


def _best_month(events: list[dict[str, Any]]) -> tuple[str, int]:
    timezone_value = _display_timezone()
    counts: Counter[str] = Counter()
    for row in events:
        parsed = parse_time(row.get("participated_at"))
        if parsed is not None:
            counts[parsed.astimezone(timezone_value).strftime("%Y-%m")] += 1
    if not counts:
        return "", 0
    month, count = min(counts.items(), key=lambda item: (-item[1], item[0]))
    return month, count


def achievements(total: int, auto_count: int, best_streak: int) -> list[str]:
    result: list[str] = []
    if total >= 10:
        result.append("🎯 Первые 10")
    if total >= 50:
        result.append("🏅 Полсотни")
    if total >= 100:
        result.append("💯 Сотник")
    if auto_count >= 10:
        result.append("🤖 На автомате")
    if best_streak >= 3:
        result.append("🔥 Серия 3 дня")
    if best_streak >= 7:
        result.append("🔥 Серия 7 дней")
    return result


def build_profile(
    stats: dict[str, Any],
    state: dict[str, Any],
    user_record: dict[str, Any],
    *,
    actor: str,
    include_auto: bool,
    current: datetime | None = None,
) -> dict[str, Any]:
    events = collect_participation_events(
        stats, state, actor=actor, include_auto=include_auto
    )
    auto_count = sum(row.get("method") == "auto" for row in events)
    manual_count = len(events) - auto_count
    current_streak, best_streak = participation_day_streaks(events, current=current)
    best_month, best_month_count = _best_month(events)
    first_seen = parse_time(user_record.get("first_seen_at"))
    now = current or datetime.now(UTC)
    days_in_bot = max(0, (now - first_seen).days) if first_seen is not None else None
    return {
        "total": len(events),
        "manual": manual_count,
        "auto": auto_count,
        "current_streak": current_streak,
        "best_streak": best_streak,
        "best_month": best_month,
        "best_month_count": best_month_count,
        "first_participation_at": events[0].get("participated_at") if events else None,
        "last_participation_at": events[-1].get("participated_at") if events else None,
        "days_in_bot": days_in_bot,
        "achievements": achievements(len(events), auto_count, best_streak),
    }


def format_profile(profile: dict[str, Any], *, include_auto: bool) -> str:
    lines = [
        "👤 <b>Мой профиль охотника за колёсами</b>",
        "",
        "<b>Участие</b>",
        f"🎡 Всего участий: <b>{int(profile.get('total', 0) or 0)}</b>",
        f"✋ Отмечено вручную: <b>{int(profile.get('manual', 0) or 0)}</b>",
    ]
    if include_auto:
        lines.append(
            f"🤖 Подтверждённых автоучастий: <b>{int(profile.get('auto', 0) or 0)}</b>"
        )
    lines.extend(
        [
            "",
            "<b>Личная активность</b>",
            f"📅 Текущая серия дней: <b>{int(profile.get('current_streak', 0) or 0)}</b>",
            f"🏆 Лучшая серия дней: <b>{int(profile.get('best_streak', 0) or 0)}</b>",
        ]
    )
    if profile.get("best_month"):
        lines.append(
            "📈 Лучший месяц: "
            f"<b>{html.escape(str(profile['best_month']))}</b> — "
            f"{int(profile.get('best_month_count', 0) or 0)} участий"
        )
    if profile.get("days_in_bot") is not None:
        lines.append(f"⏳ В боте: <b>{int(profile['days_in_bot'])}</b> дн.")

    badges = (
        profile.get("achievements")
        if isinstance(profile.get("achievements"), list)
        else []
    )
    lines.extend(["", "<b>Достижения</b>"])
    lines.extend(f"• {html.escape(str(value))}" for value in badges)
    if not badges:
        lines.append("• Пока нет открытых достижений")
    lines.extend(
        [
            "",
            "Профиль строится только по вашим сохранённым отметкам участия.",
        ]
    )
    return "\n".join(lines)[:4000]


_ANALYTICS_SECTION_CUTOFFS = frozenset({
    "<b>Участие и рейтинг</b>",
    "<b>Сейчас</b>",
    "<b>Покрытие источников</b>",
})
_ANALYTICS_DETAIL_CALLBACKS = frozenset({
    "page:ranking",
    "page:sources",
    "page:report:inactive",
})


def analytics_text_for_section(text: str) -> str:
    """Keep period analytics in Analytics; detailed live data has its own pages."""

    lines: list[str] = []
    for line in str(text or "").splitlines():
        if line.strip() in _ANALYTICS_SECTION_CUTOFFS:
            break
        if "Проблемных источников сейчас:" in line:
            continue
        lines.append(
            line.replace(
                "⚠️ Ошибок источников:",
                "ℹ️ Разовых ошибок проверок за период:",
            )
        )
    while lines and not lines[-1].strip():
        lines.pop()
    return "\n".join(lines)[:4000]


def analytics_markup_for_section(
    reply_markup: dict[str, Any] | None,
) -> dict[str, Any] | None:
    if not isinstance(reply_markup, dict):
        return reply_markup

    content_rows: list[list[dict[str, Any]]] = []
    navigation_rows: list[list[dict[str, Any]]] = []
    for raw_row in reply_markup.get("inline_keyboard", []):
        if not isinstance(raw_row, list):
            continue
        row: list[dict[str, Any]] = []
        is_navigation = False
        for raw_button in raw_row:
            if not isinstance(raw_button, dict):
                continue
            button = dict(raw_button)
            callback = str(button.get("callback_data") or "")
            if callback in {"nav:back", "nav:home"}:
                is_navigation = True
            if callback in _ANALYTICS_DETAIL_CALLBACKS:
                continue
            row.append(button)
        if not row:
            continue
        (navigation_rows if is_navigation else content_rows).append(row)

    result = dict(reply_markup)
    result["inline_keyboard"] = content_rows + navigation_rows
    return result


def _analytics_renderer_wrapper(renderer):
    if getattr(renderer, "_bbvg_section_ownership_wrapped", False):
        return renderer

    def wrapped(self, *args, **kwargs):
        original_send = self.send

        def section_send(
            text: str,
            *,
            reply_markup: dict[str, Any] | None = None,
            chat_id: str | None = None,
        ) -> dict:
            return original_send(
                analytics_text_for_section(text),
                reply_markup=analytics_markup_for_section(reply_markup),
                chat_id=chat_id,
            )

        self.send = section_send  # type: ignore[method-assign]
        try:
            return renderer(self, *args, **kwargs)
        finally:
            self.send = original_send  # type: ignore[method-assign]

    wrapped._bbvg_section_ownership_wrapped = True
    wrapped.__name__ = getattr(renderer, "__name__", "show_analytics")
    wrapped.__doc__ = getattr(renderer, "__doc__", None)
    return wrapped


def _install_analytics_section_ownership(mixin_cls: type) -> None:
    """Wrap future runtime subclasses after their own show_analytics is defined."""

    if getattr(mixin_cls, "_bbvg_analytics_section_ownership_installed", False):
        return
    original_init_subclass = mixin_cls.__dict__.get("__init_subclass__")

    @classmethod
    def init_subclass_with_analytics(cls, **kwargs):
        if original_init_subclass is not None:
            original_init_subclass.__func__(cls, **kwargs)
        else:
            super(mixin_cls, cls).__init_subclass__(**kwargs)
        renderer = cls.__dict__.get("show_analytics")
        if callable(renderer):
            cls.show_analytics = _analytics_renderer_wrapper(renderer)

    mixin_cls.__init_subclass__ = init_subclass_with_analytics
    mixin_cls._bbvg_analytics_section_ownership_installed = True


def install(mixin_cls: type) -> None:
    if getattr(mixin_cls, "_bbvg_hunter_profile_installed", False):
        return

    _install_analytics_section_ownership(mixin_cls)

    def compact_menu_rows_with_profile(
        self, admin: bool
    ) -> list[list[dict[str, Any]]]:
        rows = [
            list(row)
            for row in super(mixin_cls, self).compact_menu_rows(admin)
        ]
        rows.append([{"text": "👤 Мой профиль", "callback_data": "page:profile"}])
        return rows

    def show_profile(self) -> None:
        import personal_wheel_voting

        user_id = str(self.current_user_id or "")
        access = self.load_access(force=True)
        users = access.get("users") if isinstance(access.get("users"), dict) else {}
        record = users.get(user_id) if isinstance(users.get(user_id), dict) else {}
        owner_id = str(access.get("owner_id") or "")
        include_auto = bool(user_id and user_id == owner_id)
        try:
            actor = personal_wheel_voting.actor_vote_token(user_id)
        except RuntimeError:
            actor = ""
        snap = self.snapshot(force=True)
        profile = build_profile(
            snap.stats if isinstance(snap.stats, dict) else {},
            snap.state if isinstance(snap.state, dict) else {},
            record,
            actor=actor,
            include_auto=include_auto,
        )
        self.send(
            format_profile(profile, include_auto=include_auto),
            reply_markup=self.with_nav(
                [
                    [
                        {
                            "text": "🔄 Обновить профиль",
                            "callback_data": "profile:refresh",
                        }
                    ],
                ]
            ),
        )

    def handle_callback_with_profile(self, query: dict[str, Any]) -> None:
        data = str(query.get("data") or "")
        if data in {"page:profile", "profile:refresh"}:
            message = query.get("message") if isinstance(query, dict) else None
            message = message if isinstance(message, dict) else {}
            previous_edit_message_id = getattr(self, "_edit_message_id", None)
            callback_message_id = int(message.get("message_id") or 0) or None
            if callback_message_id is not None:
                self._edit_message_id = callback_message_id
            try:
                self._prepare_callback_user(query)
                self.answer(str(query.get("id") or ""), "Обновляю профиль")
                self.show_profile()
            finally:
                self._edit_message_id = previous_edit_message_id
            return
        super(mixin_cls, self).handle_callback(query)

    mixin_cls.compact_menu_rows = compact_menu_rows_with_profile
    mixin_cls.show_profile = show_profile
    mixin_cls.handle_callback = handle_callback_with_profile
    mixin_cls._bbvg_hunter_profile_installed = True
