from __future__ import annotations

import betboom_participation_browser as browser


def test_promo_details_is_not_clicked_as_preparation() -> None:
    assert browser.PROMO_DETAILS_RE not in browser._preparation_patterns()
    assert browser.COOKIE_RE in browser._preparation_patterns()


def test_post_click_promo_layout_confirms_participation() -> None:
    assert browser._accepted_post_click_layout(
        participation_visible=False,
        promo_details_visible=True,
    )


def test_promo_layout_is_not_success_while_participation_button_remains() -> None:
    assert not browser._accepted_post_click_layout(
        participation_visible=True,
        promo_details_visible=True,
    )
