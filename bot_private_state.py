from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
STATE_PATH = ROOT / "bot_private_state.enc.json"
FORMAT = "bbvg-bot-state-v1"


def _secret(value: str | None = None) -> bytes:
    raw = str(value or os.getenv("BOT_STATE_KEY") or os.getenv("BOT_TOKEN") or "").strip()
    if not raw:
        raise RuntimeError("BOT_STATE_KEY or BOT_TOKEN is required for bot private state")
    return raw.encode("utf-8")


def _key(secret: bytes, label: bytes) -> bytes:
    return hmac.new(secret, label, hashlib.sha256).digest()


def _keystream(key: bytes, nonce: bytes, length: int) -> bytes:
    output = bytearray()
    counter = 0
    while len(output) < length:
        output.extend(hmac.new(key, nonce + counter.to_bytes(8, "big"), hashlib.sha256).digest())
        counter += 1
    return bytes(output[:length])


def _xor(left: bytes, right: bytes) -> bytes:
    return bytes(a ^ b for a, b in zip(left, right, strict=True))


def seal(value: dict[str, Any], secret: str | None = None) -> str:
    master = _secret(secret)
    plaintext = json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    nonce = secrets.token_bytes(24)
    encryption_key = _key(master, b"bbvg-bot-state/encryption")
    authentication_key = _key(master, b"bbvg-bot-state/authentication")
    ciphertext = _xor(plaintext, _keystream(encryption_key, nonce, len(plaintext)))
    authenticated = FORMAT.encode("ascii") + nonce + ciphertext
    tag = hmac.new(authentication_key, authenticated, hashlib.sha256).digest()
    payload = {
        "format": FORMAT,
        "nonce": base64.urlsafe_b64encode(nonce).decode("ascii"),
        "ciphertext": base64.urlsafe_b64encode(ciphertext).decode("ascii"),
        "tag": base64.urlsafe_b64encode(tag).decode("ascii"),
    }
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def unseal(text: str, secret: str | None = None) -> dict[str, Any]:
    master = _secret(secret)
    payload = json.loads(text)
    if not isinstance(payload, dict) or payload.get("format") != FORMAT:
        raise ValueError("Unsupported bot private state format")
    nonce = base64.urlsafe_b64decode(str(payload.get("nonce") or ""))
    ciphertext = base64.urlsafe_b64decode(str(payload.get("ciphertext") or ""))
    supplied_tag = base64.urlsafe_b64decode(str(payload.get("tag") or ""))
    authentication_key = _key(master, b"bbvg-bot-state/authentication")
    expected_tag = hmac.new(
        authentication_key,
        FORMAT.encode("ascii") + nonce + ciphertext,
        hashlib.sha256,
    ).digest()
    if not hmac.compare_digest(supplied_tag, expected_tag):
        raise ValueError("Bot private state authentication failed")
    encryption_key = _key(master, b"bbvg-bot-state/encryption")
    plaintext = _xor(ciphertext, _keystream(encryption_key, nonce, len(ciphertext)))
    value = json.loads(plaintext.decode("utf-8"))
    if not isinstance(value, dict):
        raise ValueError("Bot private state root must be an object")
    return value


def default_bundle(
    access: dict[str, Any] | None = None,
    source_requests: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "version": 1,
        "access": dict(access or {}),
        "source_requests": dict(source_requests or {"version": 1, "requests": {}}),
    }


def load_text(
    text: str,
    *,
    access_default: dict[str, Any] | None = None,
    source_requests_default: dict[str, Any] | None = None,
    secret: str | None = None,
) -> dict[str, Any]:
    try:
        value = unseal(text, secret)
    except (ValueError, json.JSONDecodeError, UnicodeDecodeError):
        return default_bundle(access_default, source_requests_default)
    access = value.get("access") if isinstance(value.get("access"), dict) else dict(access_default or {})
    requests = (
        value.get("source_requests")
        if isinstance(value.get("source_requests"), dict)
        else dict(source_requests_default or {"version": 1, "requests": {}})
    )
    return {"version": 1, "access": access, "source_requests": requests}


def load_file(
    *,
    access_default: dict[str, Any] | None = None,
    source_requests_default: dict[str, Any] | None = None,
    secret: str | None = None,
) -> dict[str, Any]:
    try:
        text = STATE_PATH.read_text(encoding="utf-8")
    except OSError:
        return default_bundle(access_default, source_requests_default)
    return load_text(
        text,
        access_default=access_default,
        source_requests_default=source_requests_default,
        secret=secret,
    )


def save_file(value: dict[str, Any], secret: str | None = None) -> str:
    text = seal(value, secret)
    temporary = STATE_PATH.with_suffix(STATE_PATH.suffix + ".tmp")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(STATE_PATH)
    return text


def self_test() -> None:
    value = {
        "version": 1,
        "access": {"owner_id": "1", "users": {"1": {"username": "tester"}}},
        "source_requests": {"version": 1, "requests": {}},
    }
    text = seal(value, "test-secret")
    assert unseal(text, "test-secret") == value
    broken = text.replace("ciphertext", "ciphertexx", 1)
    try:
        unseal(broken, "test-secret")
    except (ValueError, KeyError):
        pass
    else:
        raise AssertionError("Tampered private state was accepted")
    print("BB V.G. bot private state self-test passed")


if __name__ == "__main__":
    self_test()
