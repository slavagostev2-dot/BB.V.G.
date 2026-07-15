from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
from pathlib import Path
from typing import Any

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

import privacy_retention

ROOT = Path(__file__).resolve().parent
STATE_PATH = ROOT / "bot_private_state.enc.json"
FORMAT_V1 = "bbvg-bot-state-v1"
FORMAT_V2 = "bbvg-bot-state-v2"
FORMAT = FORMAT_V2
AAD = FORMAT_V2.encode("ascii")


class BotStateError(RuntimeError):
    pass


class BotStateKeyError(BotStateError):
    pass


class BotStateIntegrityError(BotStateError):
    pass


def dedicated_key_configured() -> bool:
    return bool(str(os.getenv("BOT_STATE_KEY") or "").strip())


def _v2_secret(value: str | None = None, *, mode: str | None = None) -> tuple[bytes, str]:
    if value is not None:
        raw = str(value).strip()
        if not raw:
            raise BotStateKeyError("Explicit bot state key is empty")
        return raw.encode("utf-8"), "explicit"
    selected_mode = str(mode or "").strip()
    if selected_mode in {"", "dedicated"}:
        raw = str(os.getenv("BOT_STATE_KEY") or "").strip()
        if raw:
            return raw.encode("utf-8"), "dedicated"
        if selected_mode == "dedicated":
            raise BotStateKeyError("BOT_STATE_KEY is required to read dedicated v2 state")
    if selected_mode in {"", "bot_token_compat"}:
        raw = str(os.getenv("BOT_TOKEN") or "").strip()
        if raw:
            return raw.encode("utf-8"), "bot_token_compat"
    raise BotStateKeyError("BOT_STATE_KEY is required; BOT_TOKEN compatibility is only temporary")


def _v1_secret(value: str | None = None) -> bytes:
    raw = str(
        value
        or os.getenv("BOT_STATE_LEGACY_KEY")
        or os.getenv("BOT_TOKEN")
        or ""
    ).strip()
    if not raw:
        raise BotStateKeyError("BOT_STATE_LEGACY_KEY or BOT_TOKEN is required to read v1 state")
    return raw.encode("utf-8")


def _aead_key(secret: bytes) -> bytes:
    return HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=b"bbvg-bot-state/aes-gcm/salt-v2",
        info=b"bbvg-bot-state/aes-gcm/key-v2",
    ).derive(secret)


def _decode(value: Any, field: str) -> bytes:
    try:
        return base64.b64decode(str(value or ""), altchars=b"-_", validate=True)
    except (ValueError, TypeError) as exc:
        raise BotStateIntegrityError(f"Invalid base64 in {field}") from exc


def state_format(text: str) -> str:
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise BotStateIntegrityError("Encrypted state is not valid JSON") from exc
    if not isinstance(payload, dict):
        raise BotStateIntegrityError("Encrypted state root must be an object")
    return str(payload.get("format") or "")


def state_key_mode(text: str) -> str:
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise BotStateIntegrityError("Encrypted state is not valid JSON") from exc
    if not isinstance(payload, dict):
        raise BotStateIntegrityError("Encrypted state root must be an object")
    if str(payload.get("format") or "") == FORMAT_V1:
        return "legacy_v1"
    return str(payload.get("key_mode") or "bot_token_compat")


def seal(value: dict[str, Any], secret: str | None = None) -> str:
    plaintext = json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    nonce = secrets.token_bytes(12)
    master, key_mode = _v2_secret(secret)
    ciphertext = AESGCM(_aead_key(master)).encrypt(nonce, plaintext, AAD)
    payload = {
        "format": FORMAT_V2,
        "algorithm": "AES-256-GCM",
        "key_mode": key_mode,
        "nonce": base64.urlsafe_b64encode(nonce).decode("ascii"),
        "ciphertext": base64.urlsafe_b64encode(ciphertext).decode("ascii"),
    }
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def _unseal_v2(payload: dict[str, Any], secret: str | None = None) -> dict[str, Any]:
    if str(payload.get("algorithm") or "") != "AES-256-GCM":
        raise BotStateIntegrityError("Unsupported v2 encryption algorithm")
    nonce = _decode(payload.get("nonce"), "nonce")
    ciphertext = _decode(payload.get("ciphertext"), "ciphertext")
    if len(nonce) != 12:
        raise BotStateIntegrityError("AES-GCM nonce must be 12 bytes")
    mode = str(payload.get("key_mode") or "bot_token_compat")
    master, _ = _v2_secret(secret, mode=mode)
    try:
        plaintext = AESGCM(_aead_key(master)).decrypt(nonce, ciphertext, AAD)
    except InvalidTag as exc:
        raise BotStateIntegrityError(
            "Bot private state authentication failed; key is wrong or data is damaged"
        ) from exc
    try:
        value = json.loads(plaintext.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BotStateIntegrityError("Decrypted bot state is invalid") from exc
    if not isinstance(value, dict):
        raise BotStateIntegrityError("Bot private state root must be an object")
    return value


def _legacy_key(secret: bytes, label: bytes) -> bytes:
    return hmac.new(secret, label, hashlib.sha256).digest()


def _legacy_keystream(key: bytes, nonce: bytes, length: int) -> bytes:
    output = bytearray()
    counter = 0
    while len(output) < length:
        output.extend(hmac.new(key, nonce + counter.to_bytes(8, "big"), hashlib.sha256).digest())
        counter += 1
    return bytes(output[:length])


def _xor(left: bytes, right: bytes) -> bytes:
    return bytes(a ^ b for a, b in zip(left, right, strict=True))


def _unseal_v1(payload: dict[str, Any], secret: str | None = None) -> dict[str, Any]:
    nonce = _decode(payload.get("nonce"), "nonce")
    ciphertext = _decode(payload.get("ciphertext"), "ciphertext")
    supplied_tag = _decode(payload.get("tag"), "tag")
    master = _v1_secret(secret)
    authentication_key = _legacy_key(master, b"bbvg-bot-state/authentication")
    expected_tag = hmac.new(
        authentication_key,
        FORMAT_V1.encode("ascii") + nonce + ciphertext,
        hashlib.sha256,
    ).digest()
    if not hmac.compare_digest(supplied_tag, expected_tag):
        raise BotStateIntegrityError(
            "Legacy bot private state authentication failed; key is wrong or data is damaged"
        )
    encryption_key = _legacy_key(master, b"bbvg-bot-state/encryption")
    plaintext = _xor(ciphertext, _legacy_keystream(encryption_key, nonce, len(ciphertext)))
    try:
        value = json.loads(plaintext.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BotStateIntegrityError("Legacy decrypted bot state is invalid") from exc
    if not isinstance(value, dict):
        raise BotStateIntegrityError("Bot private state root must be an object")
    return value


def unseal(text: str, secret: str | None = None) -> dict[str, Any]:
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise BotStateIntegrityError("Encrypted state is not valid JSON") from exc
    if not isinstance(payload, dict):
        raise BotStateIntegrityError("Encrypted state root must be an object")
    format_name = str(payload.get("format") or "")
    if format_name == FORMAT_V2:
        return _unseal_v2(payload, secret)
    if format_name == FORMAT_V1:
        return _unseal_v1(payload, secret)
    raise BotStateIntegrityError(f"Unsupported bot private state format: {format_name or 'missing'}")


def default_bundle(
    access: dict[str, Any] | None = None,
    source_requests: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "version": 2,
        "access": dict(access or {}),
        "source_requests": dict(source_requests or {"version": 1, "requests": {}}),
    }


def _normalized_bundle(
    value: dict[str, Any],
    access_default: dict[str, Any] | None,
    source_requests_default: dict[str, Any] | None,
) -> dict[str, Any]:
    access = value.get("access") if isinstance(value.get("access"), dict) else dict(access_default or {})
    requests = (
        value.get("source_requests")
        if isinstance(value.get("source_requests"), dict)
        else dict(source_requests_default or {"version": 1, "requests": {}})
    )
    return {"version": 2, "access": access, "source_requests": requests}


def load_text(
    text: str,
    *,
    access_default: dict[str, Any] | None = None,
    source_requests_default: dict[str, Any] | None = None,
    secret: str | None = None,
) -> dict[str, Any]:
    value = unseal(text, secret)
    return _normalized_bundle(value, access_default, source_requests_default)


def load_file(
    *,
    access_default: dict[str, Any] | None = None,
    source_requests_default: dict[str, Any] | None = None,
    secret: str | None = None,
) -> dict[str, Any]:
    try:
        text = STATE_PATH.read_text(encoding="utf-8")
    except FileNotFoundError:
        return default_bundle(access_default, source_requests_default)
    except OSError as exc:
        raise BotStateError(f"Unable to read encrypted bot state: {type(exc).__name__}") from exc
    return load_text(
        text,
        access_default=access_default,
        source_requests_default=source_requests_default,
        secret=secret,
    )


def save_file(value: dict[str, Any], secret: str | None = None) -> str:
    privacy_retention.prune_bundle(value)
    text = seal(value, secret)
    temporary = STATE_PATH.with_suffix(STATE_PATH.suffix + ".tmp")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(STATE_PATH)
    return text


def self_test() -> None:
    value = {
        "version": 2,
        "access": {"owner_id": "1", "users": {"1": {"username": "tester"}}},
        "source_requests": {"version": 1, "requests": {}},
    }
    text = seal(value, "test-secret")
    assert state_format(text) == FORMAT_V2
    assert state_key_mode(text) == "explicit"
    assert unseal(text, "test-secret") == value
    payload = json.loads(text)
    payload["ciphertext"] = payload["ciphertext"][:-2] + "AA"
    try:
        unseal(json.dumps(payload), "test-secret")
    except BotStateIntegrityError:
        pass
    else:
        raise AssertionError("Tampered private state was accepted")
    try:
        load_text("{}", secret="test-secret")
    except BotStateIntegrityError:
        pass
    else:
        raise AssertionError("Invalid state silently became an empty bundle")
    print("BB V.G. AES-GCM bot private state self-test passed")


if __name__ == "__main__":
    self_test()
