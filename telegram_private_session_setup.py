from __future__ import annotations

import getpass
import json
import sys


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


def main() -> int:
    try:
        from telethon.sessions import StringSession
        from telethon.sync import TelegramClient
    except ImportError:
        print(
            "Telethon не установлен. Выполните: python -m pip install -r requirements.txt",
            file=sys.stderr,
        )
        return 2

    print("Локальная настройка доступа BB.V.G. к закрытому Telegram-каналу.")
    print("Секреты остаются на вашем компьютере; не отправляйте session string в чат.")
    api_id = _numeric_api_id(input("Telegram API ID: "))
    api_hash = getpass.getpass("Telegram API hash: ").strip()
    phone = input("Номер Telegram в международном формате (+...): ").strip()
    if not api_hash or not phone:
        print("API hash и номер телефона обязательны.", file=sys.stderr)
        return 2

    client = TelegramClient(StringSession(), api_id, api_hash)
    try:
        client.start(phone=phone)
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
        print("2. TELEGRAM_API_HASH = значение API hash")
        print("3. TELEGRAM_USER_SESSION = строка ниже")
        print(session)
        print("4. TELEGRAM_PRIVATE_SOURCES_JSON =")
        print(source_json)
        print(f"\nВыбран канал: {title}")
        print("TELEGRAM_USER_SESSION равнозначен ключу входа. Никому его не отправляйте.")
        return 0
    finally:
        client.disconnect()


if __name__ == "__main__":
    raise SystemExit(main())
