from __future__ import annotations

from betboom_participation_browser import _click_candidates, _diagnostic_labels, _success


class Candidate:
    def __init__(self, text: str) -> None:
        self.text = text
        self.clicked = False

    def is_visible(self) -> bool:
        return True

    def inner_text(self, timeout: int = 0) -> str:
        return self.text

    def click(self, timeout: int = 0, force: bool = False) -> None:
        self.clicked = True


class Locator:
    def __init__(self, candidates: list[Candidate]) -> None:
        self.candidates = candidates
        self.first = candidates[0] if candidates else Candidate("")

    def count(self) -> int:
        return len(self.candidates)

    def nth(self, index: int) -> Candidate:
        return self.candidates[index]

    def filter(self, **_kwargs):
        return self


class Root:
    def __init__(self, texts: list[str], *, url: str = "") -> None:
        self.candidates = [Candidate(text) for text in texts]
        self.url = url

    def get_by_text(self, _pattern, **_kwargs):
        return Locator(self.candidates)

    def get_by_role(self, _role, **_kwargs):
        return Locator(self.candidates)

    def locator(self, _selector: str):
        return Locator(self.candidates)

    def evaluate(self, _script: str):
        return ""


class Page(Root):
    def __init__(self, main_texts: list[str], child_frames: list[Root]) -> None:
        super().__init__(main_texts, url="https://betboom.ru/freestream/test")
        self.main_frame = Root([], url=self.url)
        self.frames = [self.main_frame, *child_frames]


def test_success_confirmation_is_found_inside_child_frame() -> None:
    page = Page(["Об акции"], [Root(["Вы уже участвуете"], url="https://wheel.example/embed")])
    assert _success(page) is True


def test_participation_button_is_clicked_inside_child_frame() -> None:
    child = Root(["Участвовать"], url="https://wheel.example/embed")
    page = Page(["Об акции"], [child])

    clicked, location = _click_candidates(page, 1000)

    assert clicked is True
    assert child.candidates[0].clicked is True
    assert location.startswith("frame:wheel.example:")


def test_frame_diagnostics_include_location_and_label() -> None:
    page = Page(["Об акции"], [Root(["Участвовать"], url="https://wheel.example/embed")])
    labels = _diagnostic_labels(page)
    assert "main:Об акции" in labels
    assert "frame:wheel.example:Участвовать" in labels
