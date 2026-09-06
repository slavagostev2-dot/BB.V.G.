from __future__ import annotations

import json
import sys
from urllib.parse import parse_qs, urlsplit


def _numeric_api_id(value: str) -> int:
    try:
        api_id = int(value.strip())
    except ValueError as exc:
        raise ValueError("API ID должен быть числом") from exc
    if api_id <= 0:
        raise ValueError("API ID должен быть положительным")
    return api_id


def _channel_id(entity: object) -> int:
    value = int(getattr(entity, "id", 0) or 0)
    if value <= 0:
        raise ValueError("у канала нет корректного Telegram ID")
    return value


def _normalize_mtproxy_secret(value: str) -> str:
    secret = value.strip().lower()
    # Telegram links may prefix the 16-byte secret with "dd" to request
    # randomized padding. Telethon's MTProxy connection class expects the
    # underlying 16-byte / 32-hex secret and provides randomized-intermediate
    # framing separately.
    if len(secret) == 34 and secret.startswith("dd"):
        secret = secret[2:]
    if len(secret) != 32:
        raise ValueError(
            "MTProto secret должен содержать 16 байт (32 hex-символа; префикс dd допустим)"
        )
    try:
        bytes.fromhex(secret)
    except ValueError as exc:
        raise ValueError("MTProto secret должен быть шестнадцатеричной строкой") from exc
    return secret


def _parse_mtproxy_link(value: str) -> tuple[str, int, str]:
    raw = value.strip()
    if not raw:
        raise ValueError("пустая ссылка MTProto proxy")

    parsed = urlsplit(raw)
    host = parsed.hostname or ""
    path = parsed.path.rstrip("/").lower()
    if parsed.scheme in {"http", "https"}:
        if host.lower() not in {"t.me", "telegram.me", "www.t.me", "www.telegram.me"}:
            raise ValueError("ожидается ссылка t.me/proxy или telegram.me/proxy")
        if path != "/proxy":
            raise ValueError("ожидается ссылка вида https://t.me/proxy?server=...&port=...&secret=...")
    elif parsed.scheme == "tg":
        if parsed.netloc.lower() != "proxy":
            raise ValueError("ожидается ссылка tg://proxy?...")
    else:
        raise ValueError("неподдерживаемый формат ссылки MTProto proxy")

    query = parse_qs(parsed.query, keep_blank_values=False)
    server = str((query.get("server") or [""])[0]).strip()
    secret_raw = str((query.get("secret") or [""])[0]).strip()
    port_raw = str((query.get("port") or [""])[0]).strip()
    if not server or not secret_raw or not port_raw:
        raise ValueError("в ссылке должны быть server, port и secret")
    try:
        port = int(port_raw)
    except ValueError as exc:
        raise ValueError("port в ссылке proxy должен быть числом") from exc
    if not 1 <= port <= 65535:
        raise ValueError("port в ссылке proxy вне диапазона 1..65535")
    secret = _normalize_mtproxy_secret(secret_raw)
    return server, port, secret


def _visible_2fa_password() -> str:
    while True:
        value = input("Пароль 2FA Telegram: ")
        if value:
            return value
        print(
            "Telegram запросил 2FA, поэтому пустой пароль здесь недопустим. "
            "Введите облачный пароль Telegram."
        )


def main() -> int:
    try:
        from telethon import connection, errors
        from telethon.sessions import StringSession
        from telethon.sync import TelegramClient
    except ImportError:
        print(
            "Telethon не установлен. Выполните: python -m pip install -r requirements.txt",
            file=sys.stderr,
        )
        return 2

    print("Локальная настройка доступа BB.V.G. к закрытому Telegram-каналу.")
    print("Все вводимые значения отображаются в консоли по вашему выбору.")
    api_id = _numeric_api_id(input("Telegram API ID: "))
    api_hash = input("Telegram API hash: ").strip()
    phone = input("Номер Telegram в международном формате (+...): ").strip()
    proxy_link = input(
        "Ссылка MTProto proxy из t.me/proxy (Enter = подключаться напрямую): "
    ).strip()
    if not api_hash or not phone:
        print("API hash и номер телефона обязательны.", file=sys.stderr)
        return 2

    client_kwargs = {}
    if proxy_link:
        try:
            mtproxy = _parse_mtproxy_link(proxy_link)
        except ValueError as exc:
            print(f"Некорректная ссылка MTProto proxy: {exc}", file=sys.stderr)
            return 2
        client_kwargs = {
            "connection": connection.ConnectionTcpMTProxyRandomizedIntermediate,
            "proxy": mtproxy,
        }
        print("MTProto proxy включен для локальной авторизации.")

    client = TelegramClient(StringSession(), api_id, api_hash, **client_kwargs)
    try:
        try:
            client.start(
                phone=phone,
                code_callback=lambda: input("Код Telegram: ").strip(),
                password=_visible_2fa_password,
            )
        except errors.SendCodeUnavailableError:
            print(
                "Telegram сейчас не разрешил повторную отправку кода для этого номера. "
                "Не запускайте авторизацию подряд много раз; повторите позже, когда Telegram снова выдаст новый код.",
                file=sys.stderr,
            )
            return 4
        except errors.FloodWaitError as exc:
            seconds = int(getattr(exc, "seconds", 0) or 0)
            if seconds > 0:
                print(
                    f"Telegram временно ограничил новые попытки авторизации. "
                    f"Указанный Telegram срок: {seconds} сек.",
                    file=sys.stderr,
                )
            else:
                print("Telegram временно ограничил новые попытки авторизации.", file=sys.stderr)
            return 4

        dialogs = []
        for dialog in client.iter_dialogs():
            entity = getattr(dialog, "entity", None)
            if entity is None:
                continue
            if not (
                bool(getattr(entity, "broadcast", False))
                or bool(getattr(entity, "megagroup", False))
            ):
                continue
            try:
                peer_id = _channel_id(entity)
            except ValueError:
                continue
            dialogs.append((str(getattr(dialog, "name", "") or "без названия"), peer_id, entity))

        if not dialogs:
            print("В этом аккаунте не найдено каналов/супергрупп.", file=sys.stderr)
            return 3

        dialogs.sort(key=lambda item: item[0].casefold())
        print("\nКаналы и супергруппы, которые видит этот Telegram-аккаунт:")
        for index, (title, peer_id, entity) in enumerate(dialogs, 1):
            username = str(getattr(entity, "username", "") or "").strip()
            suffix = f" · @{username}" if username else " · закрытый"
            print(f"{index:>3}. {title}{suffix} · id={peer_id}")

        while True:
            raw = input("\nНомер нужного закрытого канала: ").strip()
            try:
                selected = int(raw)
            except ValueError:
                print("Введите номер из списка.")
                continue
            if 1 <= selected <= len(dialogs):
                break
            print("Нет такого номера в списке.")

        title, peer_id, _entity = dialogs[selected - 1]
        alias_default = f"private_{peer_id}"
        alias = input(f"Короткое имя источника [{alias_default}]: ").strip() or alias_default
        session = StringSession.save(client.session)
        source_json = json.dumps({alias: {"peer_id": peer_id}}, ensure_ascii=False)

        print("\nГотово. В GitHub → Settings → Secrets and variables → Actions добавьте 4 secrets:")
        print(f"1. TELEGRAM_API_ID = {api_id}")
        print(f"2. TELEGRAM_API_HASH = {api_hash}")
        print("3. TELEGRAM_USER_SESSION =")
        print(session)
        print("4. TELEGRAM_PRIVATE_SOURCES_JSON =")
        print(source_json)
        print(f"\nВыбран канал: {title}")
        return 0
    finally:
        client.disconnect()


if __name__ == "__main__":
    raise SystemExit(main())
