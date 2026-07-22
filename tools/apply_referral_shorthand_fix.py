from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    file = Path(path)
    text = file.read_text(encoding="utf-8")
    if new in text:
        return
    if text.count(old) != 1:
        raise SystemExit(f"Unexpected contract in {path}")
    file.write_text(text.replace(old, new, 1), encoding="utf-8")


replace_once(
    "wheel_publications_v2.py",
    '    re.compile(r"\\b(?:для|моим?|нашим?)\\s+реферал\\w*\\b", re.IGNORECASE),\n',
    '    re.compile(\n        r"\\b(?:для|моим?|нашим?)\\s+реф(?:ерал\\w*|ов|ам)\\b",\n        re.IGNORECASE,\n    ),\n',
)

replace_once(
    "tests/test_chapter5_lifecycle.py",
    '        self.assertTrue(wheel_publications_v2.is_referral_restricted(restricted))\n',
    '        self.assertTrue(wheel_publications_v2.is_referral_restricted(restricted))\n'
    '        self.assertTrue(\n'
    '            wheel_publications_v2.is_referral_restricted(\n'
    '                "Колесо для рефов на BetBoom "\n'
    '                "https://betboom.ru/freestream/CTOM13"\n'
    '            )\n'
    '        )\n',
)

replace_once(
    "AGENTS.md",
    '- Монитор колёс: `bbvg_monitor_main.py`, `monitor.py` и тематические модули.\n',
    '- Монитор колёс: `bbvg_monitor_main.py`, `monitor.py` и тематические модули.\n'
    '- Явные ограничения «только для рефералов», «для рефералов» и сокращённое «для рефов» считаются реферальным колесом. Такое колесо маркируется `referral_restricted`; предупреждение должно сохраняться в первичном уведомлении и списке активных колёс.\n',
)

changelog = Path("docs/PROJECT_CHANGELOG_RU.md")
text = changelog.read_text(encoding="utf-8")
title = "## 2026-07-22 — Формулировка «для рефов» распознаётся как ограничение"
if title not in text:
    marker = "---\n\n"
    entry = (
        title + "\n\n"
        "Инцидент `CTOM13` не был общим отказом авторизации: оба браузера нажали элемент участия, но BetBoom не подтвердил результат. Исходный пост содержал прямое условие «Колесо для рефов», которое не попадало под прежний шаблон «для рефералов».\n\n"
        "Детерминированный классификатор теперь распознаёт сокращения «для рефов» и «рефам». Такие публикации получают `referral_restricted` и явное предупреждение о необходимости регистрации по реферальной ссылке либо промокоду автора. Регрессионный тест использует точную формулировку инцидента `CTOM13`.\n\n"
    )
    if marker not in text:
        raise SystemExit("Changelog marker not found")
    changelog.write_text(text.replace(marker, marker + entry, 1), encoding="utf-8")
