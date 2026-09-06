from __future__ import annotations

import pytest

import telegram_private_session_setup as setup


def test_parse_mtproxy_link_strips_dd_random_padding_prefix() -> None:
    server, port, secret = setup._parse_mtproxy_link(
        "https://t.me/proxy?server=example.com&port=8443&secret=dd00112233445566778899aabbccddeeff"
    )
    assert server == "example.com"
    assert port == 8443
    assert secret == "00112233445566778899aabbccddeeff"


def test_parse_mtproxy_link_keeps_plain_16_byte_secret() -> None:
    server, port, secret = setup._parse_mtproxy_link(
        "tg://proxy?server=proxy.example&port=443&secret=00112233445566778899aabbccddeeff"
    )
    assert server == "proxy.example"
    assert port == 443
    assert secret == "00112233445566778899aabbccddeeff"


def test_parse_mtproxy_link_rejects_bad_secret() -> None:
    with pytest.raises(ValueError, match="16 байт"):
        setup._parse_mtproxy_link(
            "https://t.me/proxy?server=example.com&port=8443&secret=dd0011"
        )
