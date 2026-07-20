from __future__ import annotations

import vk_start_welcome


def test_start_words_are_recognized() -> None:
    assert vk_start_welcome.is_start_message("Старт")
    assert vk_start_welcome.is_start_message("  START  ")
    assert vk_start_welcome.is_start_message("/start")
    assert vk_start_welcome.is_start_message("Начать")
    assert not vk_start_welcome.is_start_message("Привет")


def test_welcome_random_id_is_stable_and_peer_scoped() -> None:
    first = vk_start_welcome.welcome_random_id(100, 200)
    assert first == vk_start_welcome.welcome_random_id(100, 200)
    assert first != vk_start_welcome.welcome_random_id(101, 200)
    assert first > 0


def test_welcome_text_is_short_and_describes_purpose() -> None:
    text = vk_start_welcome.WELCOME_TEXT.casefold()
    assert "bb v.g." in text
    assert "колёс" in text
    assert "betboom" in text
    assert "уведомлен" in text
    assert len(vk_start_welcome.WELCOME_TEXT) < 500
