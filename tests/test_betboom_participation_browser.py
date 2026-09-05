from __future__ import annotations

import betboom_participation_browser as browser
from betboom_participation_browser import (
    ACCOUNT_STATUS_RE,
    CLICK_RE,
    REFERRAL_INELIGIBLE_LABEL_RE,
    SUCCESS_LABEL_RE,
    _matches_full_label,
    _matches_success_label,
    _success,
    _visible_referral_ineligible,
)


class _Candidate:
    def __init__(self, text: str, *, visible: bool = True) -> None:
        self.text = text
        self.visible = visible

    def is_visible(self) -> bool:
        return self.visible

    def inner_text(self, timeout: int = 0) -> str:
        return self.text


class _Locator:
    def __init__(self, values: list[_Candidate]) -> None:
        self.values = values

    def count(self) -> int:
        return len(self.values)

    def nth(self, index: int) -> _Candidate:
        return self.values[index]

    def filter(self, **_kwargs):
        return self


class _Page:
    def __init__(self, texts: list[str]) -> None:
        self.locator_value = _Locator([_Candidate(value) for value in texts])

    def get_by_text(self, _pattern):
        return self.locator_value

    def locator(self, _selector: str):
        return self.locator_value


def test_participation_button_requires_complete_label() -> None:
    assert _matches_full_label(CLICK_RE, "Участвовать")
    assert _matches_full_label(CLICK_RE, "Принять участие")
    assert not _matches_full_label(
        CLICK_RE,
        "В розыгрыше могут участвовать все зарегистрированные пользователи",
    )


def test_success_requires_self_contained_visible_confirmation_before_click() -> None:
    assert _success(_Page(["Вы уже участвуете"])) is True
    assert _success(_Page(["Вы уже участвуете в розыгрыше!"])) is True
    assert (
        _success(_Page(["Если вы участвуете, дождитесь окончания таймера"]))
        is False
    )


def test_real_deko_status_is_valid_preclick_account_state() -> None:
    real_status = (
        "Отлично! Теперь ты участвуешь в розыгрыше. "
        "Жди завершения таймера, чтобы забрать приз"
    )
    assert _matches_full_label(ACCOUNT_STATUS_RE, real_status)
    assert _matches_success_label(real_status)
    assert _success(_Page([real_status])) is True


def test_promo_ancestor_with_success_words_is_not_preclick_proof() -> None:
    real_status = (
        "Отлично! Теперь ты участвуешь в розыгрыше. "
        "Жди завершения таймера, чтобы забрать приз"
    )
    promo_ancestor = "ПОДГОН ОТ DEKO\nПравила акции\n" + real_status
    assert not _matches_success_label(promo_ancestor)
    assert _success(_Page([promo_ancestor])) is False
    # Embedded text is still allowed after our own click, where the click itself
    # supplies the missing causal evidence.
    assert _matches_success_label(promo_ancestor, allow_embedded=True)
    assert _success(_Page([promo_ancestor]), allow_embedded=True) is True


def test_success_confirmation_phrases_are_exact() -> None:
    assert _matches_full_label(SUCCESS_LABEL_RE, "Участие подтверждено")
    assert _matches_full_label(SUCCESS_LABEL_RE, "Вы в розыгрыше")
    assert not _matches_full_label(
        SUCCESS_LABEL_RE,
        "Правила объясняют, как вы участвуете в розыгрыше",
    )


def test_referral_ineligible_requires_self_contained_betboom_refusal() -> None:
    assert _matches_full_label(
        REFERRAL_INELIGIBLE_LABEL_RE,
        "Ваш аккаунт не является рефералом.",
    )
    assert _visible_referral_ineligible(
        _Page(["Ваш аккаунт не является рефералом."])
    ).endswith("Ваш аккаунт не является рефералом.")
    assert not _matches_full_label(
        REFERRAL_INELIGIBLE_LABEL_RE,
        "Колесико для рефов",
    )
    assert _visible_referral_ineligible(_Page(["Колесико для рефов"])) == ""


def test_authorization_screen_is_terminal_session_outcome(monkeypatch) -> None:
    monkeypatch.setattr(browser, "_save_diagnostics", lambda *_args, **_kwargs: "")
    result = browser._authorization_failure(
        _Page(["Войти"]),
        "https://betboom.ru/freestream/test",
        "страница показывает вход/авторизацию",
    )
    assert result.success is False
    assert result.status == "authorization_required"
    assert "авторизац" in result.detail.casefold()
