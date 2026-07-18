from __future__ import annotations

import copy
import hashlib
import hmac
import html
import json
import os
import re
from datetime import datetime, timezone
from typing import Any, Callable

UTC = timezone.utc
PERSONAL_RATING_POLICY = "personal_votes_v1"
ACTOR_TOKEN_RE = re.compile(r"^vote_[0-9a-f]{32}$")


def _clean_wheel_key(value: Any) -> str:
    return str(value or "").strip().casefold()


def _clean_source(value: Any) -> str:
    return str(value or "").strip().lstrip("@")


def wheel_event_key(wheel_key: str, entry: dict[str, Any] | None) -> str:
    """Return an event-scoped key, preferring BetBoom's action_id."""

    normalized = _clean_wheel_key(wheel_key)
    record = entry if isinstance(entry, dict) else {}
    generation_id = str(record.get("generation_id") or "").strip().casefold()
    if generation_id:
        return f"{normalized}#generation:{generation_id[:64]}"
    action_id = str(record.get("action_id") or "").strip()
    if action_id.isdigit() and int(action_id) > 0:
        return f"{normalized}#action:{int(action_id)}"
    event_id = str(record.get("event_id") or "").strip().casefold()
    if event_id:
        return f"{normalized}#event:{event_id[:64]}"
    return normalized


def actor_vote_token(user_id: str, secret: str | None = None) -> str:
    """Create a stable non-reversible actor token without publishing Telegram IDs."""

    raw_secret = str(
        secret
        or os.getenv("BOT_STATE_KEY")
        or os.getenv("BOT_TOKEN")
        or ""
    ).strip()
    if not raw_secret:
        raise RuntimeError("BOT_STATE_KEY is required for personal vote pseudonyms")
    digest = hmac.new(
        raw_secret.encode("utf-8"),
        f"bbvg-personal-wheel-vote-v1\x1f{user_id}".encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return "vote_" + digest[:32]


def normalize_vote_payload(value: Any) -> dict[str, Any]:
    raw = value if isinstance(value, dict) else {}
    wheel_key = _clean_wheel_key(raw.get("wheel_key"))
    event_key = _clean_wheel_key(raw.get("event_key"))
    actor = str(raw.get("actor") or "").strip().casefold()
    role = str(raw.get("role") or "user").strip().casefold()
    try:
        weight = int(raw.get("weight", 0) or 0)
    except (TypeError, ValueError) as exc:
        raise ValueError("Некорректный вес голоса") from exc
    if not wheel_key or not event_key:
        raise ValueError("Колесо или событие не указано")
    if not ACTOR_TOKEN_RE.fullmatch(actor):
        raise ValueError("Некорректный псевдоним участника")
    if role not in {"user", "admin", "owner"}:
        raise ValueError("Некорректная роль участника")
    expected_weight = 5 if role in {"admin", "owner"} else 1
    if weight != expected_weight:
        raise ValueError("Вес голоса не соответствует роли")
    sources: list[str] = []
    seen: set[str] = set()
    for source in raw.get("sources", []) if isinstance(raw.get("sources"), list) else []:
        cleaned = _clean_source(source)
        folded = cleaned.casefold()
        if cleaned and folded not in seen:
            seen.add(folded)
            sources.append(cleaned)
    return {
        "wheel_key": wheel_key,
        "event_key": event_key,
        "actor": actor,
        "role": role,
        "weight": weight,
        "sources": sources[:50],
    }


def _activate_personal_rating_policy(data: dict[str, Any]) -> None:
    if data.get("source_rating_policy") == PERSONAL_RATING_POLICY:
        return
    data["source_rating_policy"] = PERSONAL_RATING_POLICY
    data.pop("admin_wheel_decisions", None)
    for entry in data.setdefault("sources", {}).values():
        if not isinstance(entry, dict):
            continue
        entry.pop("admin_confirmed_wheels", None)
        entry.pop("admin_rejected_wheels", None)
        entry.pop("quality_decisions", None)
        points = entry.get("personal_vote_points")
        score = (
            sum(max(0, int(value or 0)) for value in points.values())
            if isinstance(points, dict)
            else 0
        )
        entry["personal_vote_score"] = score
        entry["quality_score"] = score
    for daily_entry in data.setdefault("daily", {}).values():
        if not isinstance(daily_entry, dict):
            continue
        totals = daily_entry.setdefault("totals", {})
        totals.pop("admin_confirmed_wheels", None)
        totals.pop("admin_rejected_wheels", None)
        for entry in daily_entry.setdefault("sources", {}).values():
            if isinstance(entry, dict):
                entry.pop("admin_confirmed_wheels", None)
                entry.pop("admin_rejected_wheels", None)



def reconcile_personal_vote_sources(
    data: dict[str, Any],
    *,
    event_key: str,
    sources: list[str],
    at: datetime | None = None,
) -> int:
    """Credit newly discovered sources for existing votes on one event."""

    targets: list[str] = []
    seen: set[str] = set()
    for source in sources:
        cleaned = _clean_source(source)
        folded = cleaned.casefold()
        if cleaned and folded not in seen:
            seen.add(folded)
            targets.append(cleaned)
    votes = data.get("personal_wheel_votes")
    if not targets or not isinstance(votes, dict):
        return 0

    current = (at or datetime.now(UTC)).astimezone(UTC)
    changed_pairs = 0
    for vote_id, raw_vote in votes.items():
        if not isinstance(raw_vote, dict):
            continue
        try:
            payload = normalize_vote_payload(raw_vote)
        except (TypeError, ValueError):
            continue
        if payload["event_key"] != _clean_wheel_key(event_key):
            continue
        known = {source.casefold() for source in payload["sources"]}
        missing = [source for source in targets if source.casefold() not in known]
        if not missing:
            continue

        raw_vote["sources"] = payload["sources"] + missing
        try:
            voted = datetime.fromisoformat(
                str(raw_vote.get("voted_at") or "").replace("Z", "+00:00")
            )
            voted = voted.astimezone(UTC) if voted.tzinfo else voted.replace(tzinfo=UTC)
        except ValueError:
            voted = current
        day = voted.date().isoformat()
        daily = data.setdefault("daily", {}).setdefault(
            day, {"sources": {}, "totals": {}}
        )
        totals = daily.setdefault("totals", {})
        metric = "admin_votes" if payload["role"] in {"admin", "owner"} else "user_votes"

        for source in missing:
            entry = data.setdefault("sources", {}).setdefault(source, {})
            points = entry.setdefault("personal_vote_points", {})
            if str(vote_id) in points:
                continue
            points[str(vote_id)] = payload["weight"]
            score = sum(max(0, int(value or 0)) for value in points.values())
            entry["personal_vote_score"] = score
            entry["quality_score"] = score
            entry["personal_votes"] = int(entry.get("personal_votes", 0) or 0) + 1
            entry[metric] = int(entry.get(metric, 0) or 0) + 1
            entry["last_vote_at"] = voted.isoformat()
            entry["last_updated_at"] = current.isoformat()

            source_day = daily.setdefault("sources", {}).setdefault(source, {})
            source_day["personal_votes"] = int(source_day.get("personal_votes", 0) or 0) + 1
            source_day["personal_vote_points"] = int(
                source_day.get("personal_vote_points", 0) or 0
            ) + payload["weight"]
            source_day[metric] = int(source_day.get(metric, 0) or 0) + 1
            totals["personal_vote_points"] = int(
                totals.get("personal_vote_points", 0) or 0
            ) + payload["weight"]
            changed_pairs += 1
    return changed_pairs


def record_personal_vote(
    data: dict[str, Any],
    *,
    event_key: str,
    sources: list[str],
    actor: str,
    role: str,
    weight: int,
    at: datetime | None = None,
) -> bool:
    """Credit every source once for one user's vote on one wheel event."""

    payload = normalize_vote_payload(
        {
            "wheel_key": event_key.split("#", 1)[0],
            "event_key": event_key,
            "actor": actor,
            "role": role,
            "weight": weight,
            "sources": sources,
        }
    )
    current = (at or datetime.now(UTC)).astimezone(UTC)
    _activate_personal_rating_policy(data)
    vote_id = hashlib.sha256(
        f"{payload['event_key']}\x1f{payload['actor']}".encode("utf-8")
    ).hexdigest()[:32]
    votes = data.setdefault("personal_wheel_votes", {})
    if vote_id in votes:
        return False
    if not payload["sources"]:
        raise ValueError("Для голоса не найдены источники колеса")
    votes[vote_id] = {
        "event_key": payload["event_key"],
        "wheel_key": payload["wheel_key"],
        "actor": payload["actor"],
        "role": payload["role"],
        "weight": payload["weight"],
        "sources": payload["sources"],
        "voted_at": current.isoformat(),
    }
    if len(votes) > 5000:
        ordered = sorted(
            votes.items(),
            key=lambda item: str((item[1] or {}).get("voted_at") or ""),
        )
        for old_key, _ in ordered[: len(votes) - 5000]:
            votes.pop(old_key, None)

    day = current.date().isoformat()
    daily_entry = data.setdefault("daily", {}).setdefault(
        day, {"sources": {}, "totals": {}}
    )
    totals = daily_entry.setdefault("totals", {})
    totals["personal_votes"] = int(totals.get("personal_votes", 0) or 0) + 1
    totals["personal_vote_points"] = int(
        totals.get("personal_vote_points", 0) or 0
    ) + payload["weight"] * len(payload["sources"])

    metric = "admin_votes" if payload["role"] in {"admin", "owner"} else "user_votes"
    for source in payload["sources"]:
        entry = data.setdefault("sources", {}).setdefault(source, {})
        points = entry.setdefault("personal_vote_points", {})
        points[vote_id] = payload["weight"]
        score = sum(max(0, int(value or 0)) for value in points.values())
        entry["personal_vote_score"] = score
        entry["quality_score"] = score
        entry["personal_votes"] = int(entry.get("personal_votes", 0) or 0) + 1
        entry[metric] = int(entry.get(metric, 0) or 0) + 1
        entry["last_vote_at"] = current.isoformat()
        entry["last_updated_at"] = current.isoformat()

        source_day = daily_entry.setdefault("sources", {}).setdefault(source, {})
        source_day["personal_votes"] = int(
            source_day.get("personal_votes", 0) or 0
        ) + 1
        source_day["personal_vote_points"] = int(
            source_day.get("personal_vote_points", 0) or 0
        ) + payload["weight"]
        source_day[metric] = int(source_day.get(metric, 0) or 0) + 1
    return True


def install_notification_router(router_module: Any) -> None:
    """Remove all shared deletion controls and keep participation personal."""

    if getattr(router_module, "_bbvg_personal_voting_markup_installed", False):
        return
    original: Callable[..., Any] = router_module.markup_for_chat

    def markup_for_chat_personal(source: Any, *, admin: bool) -> Any:
        rendered = original(source, admin=admin)
        if not isinstance(rendered, dict):
            return rendered
        result = copy.deepcopy(rendered)
        rows: list[list[dict[str, Any]]] = []
        for row in result.get("inline_keyboard", []):
            if not isinstance(row, list):
                continue
            filtered: list[dict[str, Any]] = []
            for button in row:
                if not isinstance(button, dict):
                    continue
                callback = str(button.get("callback_data") or "")
                if callback.startswith(("bb:x:", "wheel:inactive:", "wheel:finished:")):
                    continue
                item = dict(button)
                if callback.startswith(("bb:p:", "wheel:part:")):
                    item["text"] = "✅ Участвую"
                filtered.append(item)
            if filtered:
                rows.append(filtered)
        result["inline_keyboard"] = rows
        return result

    router_module.markup_for_chat = markup_for_chat_personal
    router_module._bbvg_personal_voting_markup_installed = True


class PersonalWheelVotingMixin:
    """Final UI and persistence contract for personal event-scoped wheel votes."""

    def _hidden_wheels(self, user_id: str | None = None) -> dict[str, dict[str, Any]]:
        return {}

    def _active_item(self, key: str) -> tuple[Any, dict[str, Any]]:
        normalized = _clean_wheel_key(key)
        snap = self.snapshot(force=True)
        active = snap.state.get("active_wheels", {})
        if not isinstance(active, dict):
            raise ValueError("Колесо больше не активно")
        for raw_key, raw in active.items():
            if _clean_wheel_key(raw_key) == normalized and isinstance(raw, dict):
                item = dict(raw)
                item["_key"] = str(raw_key)
                return snap, item
        raise ValueError("Колесо больше не активно")

    def _participation_records(self) -> dict[str, dict[str, Any]]:
        access = self.load_access()
        record = access.get("users", {}).get(str(self.current_user_id or ""))
        if not isinstance(record, dict):
            return {}
        raw = record.get("participating_wheels")
        if isinstance(raw, list):
            return {
                _clean_wheel_key(value): {"wheel_key": _clean_wheel_key(value)}
                for value in raw
                if _clean_wheel_key(value)
            }
        if not isinstance(raw, dict):
            return {}
        return {
            _clean_wheel_key(key): dict(value) if isinstance(value, dict) else {}
            for key, value in raw.items()
            if _clean_wheel_key(key)
        }

    def _personal_participating_wheels(self) -> set[str]:
        return set(self._participation_records())

    def _joined_wheel_keys(self, snap: Any) -> set[str]:
        return self._personal_participating_wheels()

    @staticmethod
    def _item_event_key(item: dict[str, Any]) -> str:
        key = str(item.get("_key") or item.get("identifier") or "")
        return wheel_event_key(key, item)

    def _item_joined(self, item: dict[str, Any], joined: set[str]) -> bool:
        event_key = self._item_event_key(item)
        if event_key in joined:
            return True
        key = _clean_wheel_key(item.get("_key") or item.get("identifier"))
        has_identity = bool(item.get("action_id") or item.get("event_id"))
        return bool(key and not has_identity and key in joined)

    def mark_personal_participation(self, key: str) -> dict[str, Any]:
        normalized = _clean_wheel_key(key)
        if not normalized or not self.current_user_id:
            raise ValueError("Колесо или пользователь не определены")
        snap, item = self._active_item(normalized)
        event_key = self._item_event_key(item)
        existing = self._participation_records()
        if event_key in existing:
            return {"changed": False, "queued": False, "event_key": event_key}

        role = str(self.current_role or self.role_for(self.current_user_id) or "user")
        role = role if role in {"owner", "admin", "user"} else "user"
        weight = 5 if role in {"owner", "admin"} else 1
        actor = actor_vote_token(str(self.current_user_id))
        sources = self._sources_for_item(snap, normalized, item)
        payload = normalize_vote_payload(
            {
                "wheel_key": normalized,
                "event_key": event_key,
                "actor": actor,
                "role": role,
                "weight": weight,
                "sources": sources,
            }
        )
        queue_result = self.dispatch_admin_action(
            "record_personal_vote",
            json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True),
        )

        access = self.load_access(force=True)
        users = access.setdefault("users", {})
        record = users.get(str(self.current_user_id))
        if not isinstance(record, dict):
            record = {
                "id": str(self.current_user_id),
                "chat_id": str(self.current_chat_id or self.current_user_id),
            }
            users[str(self.current_user_id)] = record
        joined = self._participation_records()
        for stored_key, stored in list(joined.items()):
            same_wheel = _clean_wheel_key(stored.get("wheel_key")) == normalized
            if same_wheel or stored_key.startswith(normalized + "#"):
                joined.pop(stored_key, None)
        joined[event_key] = {
            "wheel_key": normalized,
            "action_id": item.get("action_id"),
            "event_id": item.get("event_id"),
            "generation_id": item.get("generation_id"),
            "server_start_at": item.get("server_start_at"),
            "joined_at": datetime.now(UTC).isoformat(),
            "vote_weight": weight,
            **(
                {"vote_command_id": str(queue_result.get("command_id") or "")}
                if isinstance(queue_result, dict) and queue_result.get("command_id")
                else {}
            ),
        }
        record["participating_wheels"] = dict(sorted(joined.items()))
        self.save_access("Save personal wheel participation [skip ci]")
        return {
            "changed": True,
            "queued": bool(isinstance(queue_result, dict) and queue_result.get("queued")),
            "event_key": event_key,
            "weight": weight,
        }

    def period_overview(self, snap: Any, days: int) -> dict[str, Any]:
        result = super().period_overview(snap, days)
        joined = self._personal_participating_wheels()
        current = self._collect_current_wheels()
        result["active"] = len(current)
        result["active_with_time"] = sum(
            1 for item in current if self.parse_dt(item.get("deadline")) is not None
        )
        result["participating"] = sum(
            1 for item in current if self._item_joined(item, joined)
        )
        return result

    def show_active(self, page: int = 0) -> None:
        snap = self.snapshot(force=True)
        items = self._collect_current_wheels()
        joined = self._personal_participating_wheels()
        status = self._monitor_status()
        checked_at = status.get("last_successful_iteration_at")
        if not items:
            state_line = (
                f"Обновлено: {self.fmt_dt(checked_at)} ({self.age_text(checked_at)})"
                if checked_at
                else "Ожидаются данные проверки"
            )
            self.send(
                f"🔥 <b>Активных колёс сейчас нет.</b>\n\n{state_line}",
                reply_markup=self.with_nav(
                    [[{"text": "🔄 Обновить", "callback_data": "refresh:active:0"}]]
                ),
            )
            return

        page_size = 6
        pages = max(1, (len(items) + page_size - 1) // page_size)
        page = max(0, min(int(page), pages - 1))
        start = page * page_size
        visible = items[start : start + page_size]
        lines = [f"🔥 <b>Активные колёса: {len(items)}</b>"]
        if pages > 1:
            lines.append(f"Страница: <b>{page + 1} из {pages}</b>")
        lines.append("")
        buttons: list[list[dict[str, str]]] = []

        for offset, item in enumerate(visible):
            index = start + offset + 1
            identifier = str(item.get("identifier") or item.get("_key") or "колесо")
            key = _clean_wheel_key(item.get("_key") or identifier)
            deadline = self.parse_dt(item.get("deadline"))
            available_at = self.parse_dt(item.get("available_at"))
            sources = self._sources_for_item(snap, key, item)
            source_text = ", ".join(f"@{source}" for source in sources) or "источник неизвестен"
            if available_at and available_at > datetime.now(UTC):
                timing = f"Участие откроется через {self.remaining(available_at)}"
            elif deadline:
                timing = self.remaining(deadline)
            else:
                timing = "Время прокрутки неизвестно"
            participating = self._item_joined(item, joined)
            lines.extend(
                [
                    f"<b>{index}. <code>{html.escape(identifier[:100])}</code></b>",
                    f"⏳ {html.escape(timing)}",
                    f"📡 {html.escape(source_text)}",
                    "Участвуете ✅" if participating else "Участие не отмечено",
                    "",
                ]
            )
            url = str(item.get("url") or "")
            if url:
                buttons.append([{"text": f"🎡 {index} · Открыть", "url": url}])
            if not participating:
                buttons.append(
                    [{
                        "text": f"✅ {index} · Участвую",
                        "callback_data": self._wheel_callback("part", key),
                    }]
                )
            if self.is_admin():
                label = "Изменить время" if deadline else "Указать время"
                buttons.append(
                    [{
                        "text": f"⏱ {index} · {label}",
                        "callback_data": self._wheel_callback("time", key),
                    }]
                )

        pager: list[dict[str, str]] = []
        if page > 0:
            pager.append({"text": "◀️ Назад", "callback_data": f"page:active:{page - 1}"})
        if page < pages - 1:
            pager.append({"text": "Вперёд ▶️", "callback_data": f"page:active:{page + 1}"})
        if pager:
            buttons.append(pager)
        buttons.append([{"text": "🔄 Обновить", "callback_data": f"refresh:active:{page}"}])
        self.send("\n".join(lines).rstrip(), reply_markup=self.with_nav(buttons))

    def show_settings(self) -> None:
        rows: list[list[dict[str, Any]]] = [
            [{"text": "🔔 Уведомления", "callback_data": "page:notifications"}],
            [{"text": "🧭 API и Legacy", "callback_data": "page:wheelmode"}],
            [{"text": "⛔ Отключённый функционал", "callback_data": "page:disabled_features"}],
        ]
        lines = [
            "⚙️ <b>Настройки</b>",
            "",
            "Личные настройки применяются только к вашему Telegram-аккаунту.",
            "Проверка активных колёс выполняется через BetBoom API.",
        ]
        if self.is_admin():
            interval = int(
                self.load_access().get("settings", {}).get("monitor_interval_minutes", 5)
            )
            lines.extend(["", f"Интервал постоянной проверки: <b>{interval} мин.</b>"])
            rows.append([{"text": "⏱ Интервал проверки", "callback_data": "page:interval"}])
        if self.is_owner():
            rows.append([{"text": "👥 Доступ и администраторы", "callback_data": "page:access"}])
        else:
            rows.append([{"text": "🗑 Удалить мои данные", "callback_data": "privacy:delete:ask"}])
        self.send("\n".join(lines), reply_markup=self.with_nav(rows))

    def show_wheel_mode(self) -> None:
        text = (
            "🧭 <b>API и Legacy</b>\n\n"
            "<b>API — активный production-режим.</b> BetBoom подтверждает action_id, "
            "таймер и завершение. Только автоматическая API-проверка удаляет колесо из "
            "общего списка.\n\n"
            "<b>Legacy — аварийный архив.</b> Старый HTML-checker сохранён в отдельной "
            "archive-ветке и не включается кнопкой внутри работающего бота. Его возврат "
            "требует отдельного проверенного deploy, чтобы не смешать два источника истины."
        )
        self.send(text, reply_markup=self.with_nav())

    def show_disabled_features(self) -> None:
        text = (
            "⛔ <b>Отключённый функционал</b>\n\n"
            "• <b>Общее «Участвую»</b> — отключено: отметка всегда принадлежит только "
            "нажавшему пользователю.\n"
            "• <b>«Завершено» и «Неактивное»</b> — отключены: они конфликтуют с "
            "авторитетной BetBoom API-проверкой и могли удалить колесо раньше сервера.\n"
            "• <b>Скрытие или удаление пользователем</b> — отключено: общий список должен "
            "быть одинаковым, а личное участие хранится отдельно.\n"
            "• <b>Legacy HTML-checker</b> — не работает параллельно с API, чтобы одна ссылка "
            "не получала противоречивые статусы."
        )
        self.send(text, reply_markup=self.with_nav())

    def show_ranking(self) -> None:
        rows = self.ranked_sources(self.snapshot(force=True).stats)
        lines = [
            "🏆 <b>Рейтинг источников</b>",
            "",
            "Личный голос пользователя даёт каждому источнику колеса <b>1 очко</b>, "
            "голос администратора — <b>5 очков</b>. Один человек учитывается один раз "
            "для одного action_id.",
            "",
        ]
        medals = ["🥇", "🥈", "🥉"]
        for index, (source, score, _confirmed) in enumerate(rows, 1):
            mark = medals[index - 1] if index <= 3 else f"{index}."
            lines.append(f"{mark} <b>@{html.escape(source)}</b> — <b>{score}</b> оч.")
        if not rows:
            lines.append("Рейтинг пока пуст. Он появится после личных отметок участия.")
        self.send(
            "\n".join(lines),
            reply_markup=self.with_nav(
                [[{"text": "🔄 Обновить", "callback_data": "page:ranking"}]]
            ),
        )

    def render_page(self, page: str) -> None:
        normalized = str(page or "")
        if normalized == "wheelmode":
            self.show_wheel_mode()
            return
        if normalized == "disabled_features":
            self.show_disabled_features()
            return
        super().render_page(page)

    def handle_callback(self, query: dict[str, Any]) -> None:
        data = str(query.get("data") or "")
        query_id = str(query.get("id") or "")
        if data.startswith(("bb:p:", "wheel:part:")):
            self._prepare_callback_user(query)
            try:
                if data.startswith("bb:p:"):
                    token = data.split(":", 2)[2]
                    context = self.snapshot(force=True).state.get("button_contexts", {}).get(token)
                    if not isinstance(context, dict):
                        raise ValueError("Контекст кнопки устарел")
                    key = _clean_wheel_key(
                        context.get("wheel_key") or context.get("identifier")
                    )
                else:
                    token = data.split(":", 2)[2]
                    key = self._resolve_wheel_token(token) or ""
                result = self.mark_personal_participation(key)
            except Exception as exc:
                print(f"ERROR personal wheel vote: {type(exc).__name__}: {exc}")
                self.answer(query_id, "Не удалось отметить участие")
                return
            self.answer(
                query_id,
                "Участие уже было отмечено" if not result.get("changed") else "Ваше участие отмечено",
            )
            try:
                self.show_active()
            except Exception:
                pass
            return
        if data.startswith(("bb:x:", "wheel:inactive:", "wheel:finished:")):
            self._prepare_callback_user(query)
            self.answer(query_id, "Отключено: колесо удаляет только BetBoom API")
            return
        if data.startswith("bb:n:"):
            self._prepare_callback_user(query)
            self.answer(query_id, "Участие уже отмечено")
            return
        super().handle_callback(query)


def self_test() -> None:
    first = {"action_id": 10, "event_id": "ignored"}
    second = {"action_id": 11, "event_id": "ignored"}
    assert wheel_event_key("Wheel-A", first) == "wheel-a#action:10"
    assert wheel_event_key("wheel-a", second) != wheel_event_key("wheel-a", first)
    token = actor_vote_token("123456789", secret="test-secret")
    assert ACTOR_TOKEN_RE.fullmatch(token)
    assert "123456789" not in token

    stats: dict[str, Any] = {"version": 1, "sources": {}, "daily": {}}
    assert record_personal_vote(
        stats,
        event_key="wheel-a#action:10",
        sources=["first", "second", "first"],
        actor=token,
        role="user",
        weight=1,
        at=datetime(2026, 7, 16, 12, 0, tzinfo=UTC),
    )
    assert stats["sources"]["first"]["quality_score"] == 1
    assert stats["sources"]["second"]["quality_score"] == 1
    assert not record_personal_vote(
        stats,
        event_key="wheel-a#action:10",
        sources=["first", "second"],
        actor=token,
        role="user",
        weight=1,
        at=datetime(2026, 7, 16, 12, 1, tzinfo=UTC),
    )
    admin = actor_vote_token("1", secret="test-secret")
    assert record_personal_vote(
        stats,
        event_key="wheel-a#action:10",
        sources=["first", "second"],
        actor=admin,
        role="owner",
        weight=5,
        at=datetime(2026, 7, 16, 12, 2, tzinfo=UTC),
    )
    assert stats["sources"]["first"]["quality_score"] == 6
    assert record_personal_vote(
        stats,
        event_key="wheel-a#action:11",
        sources=["first"],
        actor=token,
        role="user",
        weight=1,
        at=datetime(2026, 7, 16, 12, 3, tzinfo=UTC),
    )
    assert stats["sources"]["first"]["quality_score"] == 7
    print("personal wheel voting self-test passed")


if __name__ == "__main__":
    self_test()
