from __future__ import annotations

import hashlib
import html
import json
import re
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any
from urllib.parse import quote

from bs4 import BeautifulSoup

import admin_bot as legacy
import telegram_transport
from admin_panel_v2 import BLOCKED_SOURCES, USERNAME_RE, WHEEL_LINK_RE
from bbvg.bot.interface import PanelInterfaceRuntime

UTC = timezone.utc
SOURCE_REQUESTS_PATH = "source_requests.json"
SOURCE_REQUEST_PREFIX = "source_"


def default_source_requests() -> dict[str, Any]:
    return {"version": 1, "requests": {}}


class SourceRequestRuntime(PanelInterfaceRuntime):
    """User source proposals, public-channel verification and moderation."""

    def __init__(self) -> None:
        super().__init__()
        self._bot_username_cache: str | None = None

    def bot_username(self) -> str:
        if self._bot_username_cache is not None:
            return self._bot_username_cache
        try:
            result = self.telegram_api("getMe").get("result") or {}
            username = str(result.get("username") or "").strip().lstrip("@")
        except Exception as exc:
            print(f"WARNING get bot username: {type(exc).__name__}: {exc}")
            username = ""
        self._bot_username_cache = username
        return username

    def miniapp_url_for_chat(self) -> str:
        deployment = self.miniapp_deployment()
        status = str(deployment.get("status") or "")
        base = str(deployment.get("url") or "").strip()
        if status != "deployed" or not base.startswith("https://"):
            base = "https://slavagostev2-betboom-monitor.pages.dev/"
        username = self.bot_username()
        if not username:
            return base
        separator = "&" if "?" in base else "?"
        return f"{base}{separator}bot={quote(username)}"

    def show_app_entry(self) -> None:
        url = self.miniapp_url_for_chat()
        self.send(
            "📱 <b>Приложение BetBoom Monitor</b>\n\n"
            "Актуальные колёса, статистика и запросы на добавление источников.",
            reply_markup=self.with_nav(
                [
                    [{"text": "📱 Открыть внутри Telegram", "web_app": {"url": url}}],
                    [{"text": "🌐 Открыть в браузере", "url": url}],
                ]
            ),
        )

    def load_source_requests(self) -> dict[str, Any]:
        try:
            value = self.get_json_file(
                SOURCE_REQUESTS_PATH,
                default_source_requests(),
            )
        except Exception:
            value = default_source_requests()
        if not isinstance(value, dict):
            value = default_source_requests()
        requests = value.get("requests")
        value["requests"] = requests if isinstance(requests, dict) else {}
        value["version"] = 1
        return value

    def save_source_requests(self, value: dict[str, Any], message: str) -> None:
        content = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        self.update_file(SOURCE_REQUESTS_PATH, content, message)

    def moderator_chat_ids(self) -> list[str]:
        access = self.load_access(force=True)
        admin_ids = {
            str(access.get("owner_id") or ""),
            *{str(value) for value in access.get("admins", []) if str(value)},
        }
        users = access.get("users") if isinstance(access.get("users"), dict) else {}
        values = {
            str((users.get(user_id) or {}).get("chat_id") or user_id)
            for user_id in admin_ids
            if user_id
        }
        if str(legacy.ADMIN_USER_ID or ""):
            values.add(str(legacy.ADMIN_USER_ID))
        return sorted(values)

    def can_moderate_source_requests(self) -> bool:
        return self.is_admin() or str(self.current_user_id or "") in set(
            self.moderator_chat_ids()
        )

    @staticmethod
    def requester_name(sender: dict[str, Any]) -> str:
        full = " ".join(
            value
            for value in (
                str(sender.get("first_name") or "").strip(),
                str(sender.get("last_name") or "").strip(),
            )
            if value
        )
        username = str(sender.get("username") or "").strip()
        if username:
            return f"{full or 'Пользователь'} (@{username})"
        return full or "Пользователь"

    def inspect_source(self, source: str) -> dict[str, Any]:
        url = telegram_transport.public_source_url(source)
        try:
            response = self.http.get(
                url,
                headers={"User-Agent": "Mozilla/5.0 (compatible; BetBoomMonitor/1.0)"},
                timeout=legacy.REQUEST_TIMEOUT,
            )
        except Exception as exc:
            return {
                "public": False,
                "detail": f"не удалось открыть Telegram: {type(exc).__name__}",
                "http_status": None,
                "messages": 0,
                "wheel_links": [],
                "title": "",
            }

        body = response.text or ""
        soup = BeautifulSoup(body, "html.parser")
        messages = soup.select(".tgme_widget_message")
        info = soup.select_one(".tgme_channel_info")
        title_node = soup.select_one(".tgme_channel_info_header_title")
        title = title_node.get_text(" ", strip=True) if title_node else ""
        wheel_links = sorted(
            {
                link if link.startswith("http") else f"https://{link}"
                for link in WHEEL_LINK_RE.findall(body)
            }
        )
        public = response.status_code == 200 and bool(messages or info)
        if response.status_code != 200:
            detail = f"Telegram вернул HTTP {response.status_code}"
        elif not public:
            detail = "публичные сообщения канала не обнаружены"
        elif wheel_links:
            detail = (
                "публичный канал; в доступных сообщениях найдены ссылки на колёса"
            )
        else:
            detail = (
                "публичный канал; в доступных сообщениях ссылок на колёса не найдено"
            )
        return {
            "public": public,
            "detail": detail,
            "http_status": response.status_code,
            "messages": len(messages),
            "wheel_links": wheel_links[:10],
            "title": title,
        }

    def request_id(self, source: str, user_id: str) -> str:
        raw = f"{source.casefold()}:{user_id}:{datetime.now(UTC).isoformat()}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]

    def notify_moderators(
        self,
        request_id: str,
        request: dict[str, Any],
    ) -> None:
        source = str(request.get("source") or "")
        check = request.get("check") if isinstance(request.get("check"), dict) else {}
        links = (
            check.get("wheel_links")
            if isinstance(check.get("wheel_links"), list)
            else []
        )
        sample = "\n".join(
            f"• <code>{html.escape(str(link))}</code>" for link in links[:3]
        )
        text = (
            "📨 <b>Запрос пользователя на добавление источника</b>\n\n"
            f"Канал: <b>@{html.escape(source)}</b>\n"
            f"Название: {html.escape(str(check.get('title') or 'не найдено'))}\n"
            f"Пользователь: {html.escape(str(request.get('requester_name') or 'неизвестно'))}\n"
            f"Telegram ID: <code>{html.escape(str(request.get('requester_id') or ''))}</code>\n\n"
            "<b>Автоматическая проверка</b>\n"
            f"Публичный канал: {'да' if check.get('public') else 'нет'}\n"
            f"Доступных сообщений: {int(check.get('messages') or 0)}\n"
            f"Найдено ссылок на колёса: {len(links)}\n"
            f"Результат: {html.escape(str(check.get('detail') or 'нет данных'))}"
        )
        if sample:
            text += "\n\n<b>Примеры ссылок</b>\n" + sample
        markup = {
            "inline_keyboard": [
                [
                    {
                        "text": "Открыть канал",
                        "url": telegram_transport.profile_url(source),
                    }
                ],
                [
                    {
                        "text": "⚡ В основные",
                        "callback_data": f"sr:fast:{request_id}",
                    },
                    {
                        "text": "🌙 В ночное наблюдение",
                        "callback_data": f"sr:nightly:{request_id}",
                    },
                ],
                [
                    {
                        "text": "Отклонить",
                        "callback_data": f"sr:reject:{request_id}",
                    }
                ],
            ]
        }
        for chat_id in self.moderator_chat_ids():
            try:
                self.send(text, reply_markup=markup, chat_id=chat_id)
            except Exception as exc:
                print(
                    f"WARNING source request notify {chat_id}: "
                    f"{type(exc).__name__}: {exc}"
                )

    def submit_source_request(
        self,
        source: str,
        message: dict[str, Any],
    ) -> None:
        source = self.safe_source(source)
        sender = message.get("from") if isinstance(message.get("from"), dict) else {}
        chat = message.get("chat") if isinstance(message.get("chat"), dict) else {}
        requester_id = str(sender.get("id") or "")
        requester_chat_id = str(chat.get("id") or requester_id)

        if not USERNAME_RE.fullmatch(source):
            self.send(
                "Некорректный username. Отправьте имя публичного канала без ссылки."
            )
            return
        if source.casefold() in {value.casefold() for value in BLOCKED_SOURCES}:
            self.send(
                "Этот источник исключён из мониторинга и не может быть предложен повторно."
            )
            return

        try:
            snap = self.snapshot(force=True)
            known = {value.casefold() for value in snap.fast + snap.nightly}
        except Exception:
            known = set()
        if source.casefold() in known:
            self.send(f"@{html.escape(source)} уже находится в списке источников.")
            return

        try:
            self.telegram_api(
                "sendChatAction",
                {"chat_id": requester_chat_id, "action": "typing"},
            )
        except Exception:
            pass
        check = self.inspect_source(source)
        if not check.get("public"):
            self.send(
                f"⚠️ Запрос на @{html.escape(source)} не отправлен.\n\n"
                f"Проверка: {html.escape(str(check.get('detail') or 'канал недоступен'))}."
            )
            return

        state = self.load_source_requests()
        for existing in state["requests"].values():
            if not isinstance(existing, dict):
                continue
            if (
                str(existing.get("source") or "").casefold() == source.casefold()
                and str(existing.get("requester_id") or "") == requester_id
                and str(existing.get("status") or "") == "pending"
            ):
                self.send(
                    f"Запрос на @{html.escape(source)} уже ожидает решения администратора."
                )
                return

        request_id = self.request_id(source, requester_id)
        request = {
            "id": request_id,
            "source": source,
            "status": "pending",
            "created_at": datetime.now(UTC).isoformat(),
            "requester_id": requester_id,
            "requester_chat_id": requester_chat_id,
            "requester_name": self.requester_name(sender),
            "requester_username": str(sender.get("username") or ""),
            "check": check,
        }
        state["requests"][request_id] = request
        self.save_source_requests(
            state,
            f"Add source request @{source} [skip ci]",
        )
        self.notify_moderators(request_id, request)
        self.send(
            f"✅ Запрос на <b>@{html.escape(source)}</b> отправлен администратору.\n\n"
            "Канал подтверждён как публичный. После решения бот пришлёт результат."
        )

    def decide_source_request(
        self,
        action: str,
        request_id: str,
    ) -> tuple[str, dict[str, Any]]:
        if not self.can_moderate_source_requests():
            raise PermissionError("Недостаточно прав")
        state = self.load_source_requests()
        request = state["requests"].get(request_id)
        if not isinstance(request, dict):
            raise KeyError("Запрос не найден")
        if str(request.get("status") or "") != "pending":
            return "Запрос уже обработан", request

        source = str(request.get("source") or "")
        now = datetime.now(UTC).isoformat()
        if action in {"fast", "nightly"}:
            self.set_source_mode(source, action)
            request["status"] = "accepted"
            request["destination"] = "primary" if action == "fast" else "nightly"
            request["decision_text"] = (
                "добавлен в основные"
                if action == "fast"
                else "добавлен в ночное наблюдение"
            )
            try:
                self.dispatch("monitor.yml", {"continuous": "true"})
            except Exception as exc:
                print(
                    f"WARNING restart after source request: "
                    f"{type(exc).__name__}: {exc}"
                )
        elif action == "reject":
            request["status"] = "rejected"
            request["destination"] = ""
            request["decision_text"] = "отклонён"
        else:
            raise ValueError("Неизвестное действие")

        request["decided_at"] = now
        request["decided_by"] = str(self.current_user_id or "")
        state["requests"][request_id] = request
        self.save_source_requests(
            state,
            f"Resolve source request @{source} [skip ci]",
        )

        user_text = (
            f"✅ Источник <b>@{html.escape(source)}</b> "
            f"{html.escape(str(request['decision_text']))}."
            if request["status"] == "accepted"
            else f"Запрос на <b>@{html.escape(source)}</b> отклонён администратором."
        )
        try:
            self.send(
                user_text,
                chat_id=str(
                    request.get("requester_chat_id")
                    or request.get("requester_id")
                    or ""
                ),
            )
        except Exception as exc:
            print(f"WARNING source request result: {type(exc).__name__}: {exc}")
        return str(request["decision_text"]), request

    def handle_message(self, message: dict[str, Any]) -> None:
        if not self.private_chat(message):
            return
        chat = message.get("chat") or {}
        sender = message.get("from") or {}
        self.set_context(chat.get("id"), sender.get("id"))

        web_data = message.get("web_app_data")
        if isinstance(web_data, dict):
            self.register_user(message)
            self.set_context(chat.get("id"), sender.get("id"))
            try:
                payload = json.loads(str(web_data.get("data") or "{}"))
            except json.JSONDecodeError:
                payload = {}
            if payload.get("type") == "source_request":
                self.submit_source_request(
                    str(payload.get("source") or ""),
                    message,
                )
                return

        text = str(message.get("text") or "").strip()
        if text:
            start_match = re.fullmatch(
                r"/start(?:@\w+)?\s+source_(\w+)",
                text,
                re.I,
            )
            source_match = re.fullmatch(
                r"/source(?:@\w+)?\s+@?([A-Za-z][A-Za-z0-9_]{3,31})",
                text,
                re.I,
            )
            if start_match or source_match:
                self.register_user(message)
                self.set_context(chat.get("id"), sender.get("id"))
                self.submit_source_request(
                    (start_match or source_match).group(1),
                    message,
                )
                return

        super().handle_message(message)

    def handle_callback(self, query: dict[str, Any]) -> None:
        data = str(query.get("data") or "")
        if not data.startswith("sr:"):
            super().handle_callback(query)
            return

        message = query.get("message") or {}
        chat = message.get("chat") or {}
        sender = query.get("from") or {}
        query_id = str(query.get("id") or "")
        message_id = int(message.get("message_id") or 0)
        self.set_context(chat.get("id"), sender.get("id"))
        self._edit_message_id = message_id or None
        try:
            _, action, request_id = data.split(":", 2)
            decision, request = self.decide_source_request(action, request_id)
            self.answer(query_id, "Готово")
            source = str(request.get("source") or "")
            self.send(
                "📨 <b>Запрос пользователя на источник</b>\n\n"
                f"Канал: <b>@{html.escape(source)}</b>\n"
                f"Решение: <b>{html.escape(decision)}</b>\n"
                "Обработал Telegram ID: "
                f"<code>{html.escape(str(self.current_user_id or ''))}</code>",
                reply_markup={
                    "inline_keyboard": [
                        [
                            {
                                "text": "Открыть канал",
                                "url": telegram_transport.profile_url(source),
                            }
                        ]
                    ]
                },
            )
        except PermissionError:
            self.answer(query_id, "Недостаточно прав")
        except KeyError:
            self.answer(query_id, "Запрос не найден")
        except Exception as exc:
            self.answer(query_id, "Ошибка")
            print(
                f"ERROR source request callback: {type(exc).__name__}: {exc}"
            )
        finally:
            self._edit_message_id = None


def self_test() -> None:
    assert USERNAME_RE.fullmatch("valid_channel")
    assert not USERNAME_RE.fullmatch("bad-channel")
    assert SOURCE_REQUEST_PREFIX == "source_"
    assert len("sr:nightly:" + "a" * 12) < 64
    assert default_source_requests() == {"version": 1, "requests": {}}
    assert SourceRequestRuntime.requester_name(
        {"first_name": "Test", "username": "tester"}
    ) == "Test (@tester)"

    panel = object.__new__(SourceRequestRuntime)
    panel.current_user_id = "1"
    panel.current_chat_id = "1"
    panel.current_role = "admin"
    panel._edit_message_id = None
    panel.safe_source = lambda value: str(value).strip().lstrip("@")  # type: ignore[method-assign]
    panel.snapshot = lambda force=False: SimpleNamespace(  # type: ignore[method-assign]
        fast=[],
        nightly=[],
    )
    panel.telegram_api = lambda *args, **kwargs: {"ok": True}  # type: ignore[method-assign]
    panel.inspect_source = lambda source: {  # type: ignore[method-assign]
        "public": True,
        "detail": "ok",
        "messages": 1,
        "wheel_links": [],
        "title": "Test",
    }
    state = default_source_requests()
    panel.load_source_requests = lambda: state  # type: ignore[method-assign]
    saved: list[dict[str, Any]] = []
    panel.save_source_requests = lambda value, message: saved.append(value)  # type: ignore[method-assign]
    panel.notify_moderators = lambda request_id, request: None  # type: ignore[method-assign]
    sent: list[str] = []
    panel.send = lambda text, **kwargs: sent.append(text) or {"ok": True}  # type: ignore[method-assign]
    panel.submit_source_request(
        "valid_channel",
        {
            "chat": {"id": 1, "type": "private"},
            "from": {"id": 1, "first_name": "Test"},
        },
    )
    assert saved and len(state["requests"]) == 1
    assert sent and "отправлен администратору" in sent[-1]
    print("BB V.G. source request subsystem self-test passed")


if __name__ == "__main__":
    self_test()
