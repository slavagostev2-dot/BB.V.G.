from __future__ import annotations

import html
import os
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Any, Callable
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


def _active_event_keys(
    state: dict[str, Any], event_key_fn: Callable[[str, dict[str, Any]], str]
) -> set[str]:
    active = state.get("active_wheels")
    active = active if isinstance(active, dict) else {}
    result: set[str] = set()
    for key, raw in active.items():
        if not isinstance(raw, dict):
            continue
        result.add(canonical_event_key(event_key_fn(str(key), raw)))
    return {value for value in result if value}


def current_active_participations(
    state: dict[str, Any],
    user_record: dict[str, Any],
    *,
    include_auto: bool,
    event_key_fn: Callable[[str, dict[str, Any]], str],
) -> int:
    active_keys = _active_event_keys(state, event_key_fn)
    if not active_keys:
        return 0

    personal = user_record.get("participating_wheels")
    if isinstance(personal, list):
        personal_keys = {canonical_event_key(value) for value in personal}
    elif isinstance(personal, dict):
        personal_keys = {canonical_event_key(value) for value in personal}
    else:
        personal_keys = set()
    matched = {value for value in personal_keys if value in active_keys}

    if include_auto:
        global_rows = state.get("participating_wheels")
        global_rows = global_rows if isinstance(global_rows, dict) else {}
        active_by_wheel = {value.split("#", 1)[0]: value for value in active_keys}
        for wheel, row in global_rows.items():
            if not isinstance(row, dict):
                continue
            if str(row.get("participation_source") or "") != "betboom_browser":
                continue
            normalized = str(wheel).casefold()
            if normalized in active_by_wheel:
                matched.add(active_by_wheel[normalized])
    return len(matched)


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
    event_key_fn: Callable[[str, dict[str, Any]], str],
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
        "active": current_active_participations(
            state,
            user_record,
            include_auto=include_auto,
            event_key_fn=event_key_fn,
        ),
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
        f"🎡 Участий по сохранённым событиям: <b>{int(profile.get('total', 0) or 0)}</b>",
        f"✋ Отмечено вручную: <b>{int(profile.get('manual', 0) or 0)}</b>",
    ]
    if include_auto:
        lines.append(
            f"🤖 Подтверждённых автоучастий: <b>{int(profile.get('auto', 0) or 0)}</b>"
        )
    lines.extend(
        [
            f"🔥 Сейчас активных с участием: <b>{int(profile.get('active', 0) or 0)}</b>",
            "",
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
            "Статистика пересчитывается из сохранённого журнала конкретных событий, а не из вручную увеличиваемого счётчика.",
        ]
    )
    return "\n".join(lines)[:4000]


def install(mixin_cls: type) -> None:
    if getattr(mixin_cls, "_bbvg_hunter_profile_installed", False):
        return

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
            event_key_fn=personal_wheel_voting.wheel_event_key,
        )
        self.send(
            format_profile(profile, include_auto=include_auto),
            reply_markup=self.with_nav(
                [
                    [{"text": "🔥 Активные колёса", "callback_data": "page:active"}],
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
            self._prepare_callback_user(query)
            self.answer(str(query.get("id") or ""), "Обновляю профиль")
            self.show_profile()
            return
        super(mixin_cls, self).handle_callback(query)

    mixin_cls.compact_menu_rows = compact_menu_rows_with_profile
    mixin_cls.show_profile = show_profile
    mixin_cls.handle_callback = handle_callback_with_profile
    mixin_cls._bbvg_hunter_profile_installed = True
