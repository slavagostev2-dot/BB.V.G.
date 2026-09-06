from __future__ import annotations

from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    target = Path(path)
    text = target.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{path}: expected exactly one match, got {count}")
    target.write_text(text.replace(old, new, 1), encoding="utf-8")


def replace_first(path: str, old: str, new: str) -> None:
    target = Path(path)
    text = target.read_text(encoding="utf-8")
    if old not in text:
        raise SystemExit(f"{path}: expected at least one match")
    target.write_text(text.replace(old, new, 1), encoding="utf-8")


# Harden the newly added network module: malformed/non-JSON bodies are never
# persisted, because they cannot be safely field-filtered.
replace_once(
    "betboom_network_diagnostics.py",
    "    except json.JSONDecodeError:\n        sanitized = _TOKENISH_RE.sub(\"<redacted>\", raw)\n        return sanitized[:MAX_BODY_CHARS]\n",
    "    except json.JSONDecodeError:\n        return None\n",
)

# Browser participation: observation only. No click/retry behavior changes.
replace_once(
    "betboom_participation_browser.py",
    "import betboom_auto_participation as auto\n",
    "import betboom_auto_participation as auto\nimport betboom_network_diagnostics as network_diag\n",
)
replace_once(
    "betboom_participation_browser.py",
    "    page: Any = None\n    try:\n",
    "    page: Any = None\n    network_trace: list[dict[str, Any]] = []\n    try:\n",
)
replace_once(
    "betboom_participation_browser.py",
    "            clicked, location, preparations, preexisting = _find_and_click(\n                page, timeout_ms\n            )\n",
    "            network_trace = network_diag.attach(page)\n            clicked, location, preparations, preexisting = _find_and_click(\n                page, timeout_ms\n            )\n",
)
participated_old = """                    artifact = _finish_click_proof(
                        page,
                        proof_target,
                        url=url,
                        click_location=location,
                        confirmation=confirmation,
                        status="participated",
                    )
                    browser.close()
"""
participated_new = """                    artifact = _finish_click_proof(
                        page,
                        proof_target,
                        url=url,
                        click_location=location,
                        confirmation=confirmation,
                        status="participated",
                    )
                    network_diag.write_trace(artifact, network_trace)
                    browser.close()
"""
replace_first("betboom_participation_browser.py", participated_old, participated_new)
replace_once("betboom_participation_browser.py", participated_old, participated_new)
replace_once(
    "betboom_participation_browser.py",
    """                    result = _authorization_failure(
                        page,
                        url,
                        "страница показывает вход/авторизацию после клика участия",
                    )
                    browser.close()
""",
    """                    result = _authorization_failure(
                        page,
                        url,
                        "страница показывает вход/авторизацию после клика участия",
                    )
                    network_diag.write_trace(result.artifact_url, network_trace)
                    browser.close()
""",
)
replace_once(
    "betboom_participation_browser.py",
    """                    artifact = _save_diagnostics(
                        page, url, "referral_ineligible", detail
                    )
                    browser.close()
                    return auto.ParticipationResult(
                        False, "referral_ineligible", detail[:300], artifact
                    )

            try:
""",
    """                    artifact = _save_diagnostics(
                        page, url, "referral_ineligible", detail
                    )
                    network_diag.write_trace(artifact, network_trace)
                    browser.close()
                    return auto.ParticipationResult(
                        False, "referral_ineligible", detail[:300], artifact
                    )

            try:
""",
)
replace_once(
    "betboom_participation_browser.py",
    """                    result = _authorization_failure(
                        page,
                        url,
                        "контрольная перезагрузка показывает вход/авторизацию; участие не подтверждено",
                    )
                    browser.close()
""",
    """                    result = _authorization_failure(
                        page,
                        url,
                        "контрольная перезагрузка показывает вход/авторизацию; участие не подтверждено",
                    )
                    network_diag.write_trace(result.artifact_url, network_trace)
                    browser.close()
""",
)
replace_once(
    "betboom_participation_browser.py",
    """            artifact = _finish_click_proof(
                page,
                proof_target,
                url=url,
                click_location=location,
                confirmation="not_found",
                status="unconfirmed",
            )
            browser.close()
""",
    """            artifact = _finish_click_proof(
                page,
                proof_target,
                url=url,
                click_location=location,
                confirmation="not_found",
                status="unconfirmed",
            )
            network_diag.write_trace(artifact, network_trace)
            browser.close()
""",
)
replace_once(
    "betboom_participation_browser.py",
    "        artifact = _save_diagnostics(page, url, \"technical_error\", detail)\n        return auto.ParticipationResult(\n",
    "        artifact = _save_diagnostics(page, url, \"technical_error\", detail)\n        network_diag.write_trace(artifact, network_trace)\n        return auto.ParticipationResult(\n",
)
replace_once(
    "betboom_participation_browser.py",
    "    assert PROMO_DETAILS_RE not in _preparation_patterns()\n",
    "    assert PROMO_DETAILS_RE not in _preparation_patterns()\n    network_diag.self_test()\n",
)

# Recovery: merge every source publication seen in the scan into the existing
# publication ledger, while keeping the newest publication as the API candidate.
restore_signature = """def _restore_runtime_state(
    state: dict[str, Any],
    active: list[dict[str, Any]],
    attempts: list[dict[str, Any]],
    scanned_at: Any,
) -> None:
"""
helper_and_signature = """def _merge_discovered_publications(
    state: dict[str, Any],
    key: str,
    entry: dict[str, Any],
    incoming: Any,
) -> list[str]:
    collection = state.setdefault("wheel_publications", {})
    merged = wheel_publications_v2.merge_publications(
        collection.get(key, []),
        incoming,
        reset_event=False,
    )
    if merged:
        collection[key] = merged
    else:
        collection.pop(key, None)
    sources = wheel_publications_v2.publication_sources(state, key, entry)
    if sources:
        entry["sources"] = sources
    return sources


def _restore_runtime_state(
    state: dict[str, Any],
    active: list[dict[str, Any]],
    attempts: list[dict[str, Any]],
    scanned_at: Any,
    discovered_publications: dict[str, list[dict[str, Any]]] | None = None,
) -> None:
"""
replace_once("auto_participation_recovery.py", restore_signature, helper_and_signature)
replace_once(
    "auto_participation_recovery.py",
    "        _ensure_button_context(state, entry, item)\n\n        attempt = attempts_by_key.get(key)\n",
    """        publication_rows = (discovered_publications or {}).get(key, [])
        if publication_rows:
            _merge_discovered_publications(state, key, entry, publication_rows)
        _ensure_button_context(state, entry, item)

        attempt = attempts_by_key.get(key)
""",
)
replace_once(
    "auto_participation_recovery.py",
    "    candidates = _persisted_active_candidates(persisted, now)\n    for source, messages in results.items():\n",
    "    candidates = _persisted_active_candidates(persisted, now)\n    discovered_publications: dict[str, list[dict[str, Any]]] = {}\n    for source, messages in results.items():\n",
)
replace_once(
    "auto_participation_recovery.py",
    """                record = {
                    "wheel_key": key,
                    "url": monitor.normalize_url(link),
                    "source": source,
                    "message_id": message.message_id,
                    "message_date": published.isoformat(),
                    "message_url": message.message_url,
                    "message_text": str(message.text or "")[:4000],
                }
                if current is None or record["message_date"] > current["message_date"]:
""",
    """                record = {
                    "wheel_key": key,
                    "url": monitor.normalize_url(link),
                    "source": source,
                    "message_id": message.message_id,
                    "message_date": published.isoformat(),
                    "message_url": message.message_url,
                    "message_text": str(message.text or "")[:4000],
                }
                discovered_publications.setdefault(key, []).append(
                    {
                        "source": source,
                        "message_id": message.message_id,
                        "message_date": published.isoformat(),
                        "message_url": message.message_url,
                    }
                )
                if current is None or record["message_date"] > current["message_date"]:
""",
)
replace_once(
    "auto_participation_recovery.py",
    "    _restore_runtime_state(persisted, active, attempts, now)\n",
    "    _restore_runtime_state(\n        persisted, active, attempts, now, discovered_publications\n    )\n",
)
replace_once(
    "auto_participation_recovery.py",
    """    assert _notification_already_recorded(
        notification_state,
        "old",
        {"message_date": "2026-07-21T10:01:00+00:00"},
    )
    print("auto participation recovery authoritative-outcome self-test passed")
""",
    """    assert _notification_already_recorded(
        notification_state,
        "old",
        {"message_date": "2026-07-21T10:01:00+00:00"},
    )
    source_state = {
        "wheel_publications": {
            "kekw2": [
                {"source": "shadowkek", "message_id": 1}
            ]
        }
    }
    source_entry = {"source": "shadowkek"}
    sources = _merge_discovered_publications(
        source_state,
        "kekw2",
        source_entry,
        [
            {"source": "burdakekw", "message_id": 5911},
            {"source": "private_2445382077", "message_id": 7805},
        ],
    )
    assert set(sources) == {
        "shadowkek",
        "burdakekw",
        "private_2445382077",
    }
    assert set(source_entry["sources"]) == set(sources)
    print("auto participation recovery authoritative-outcome self-test passed")
""",
)

# Active wheel UI: consume the existing multi-publication ledger.
replace_once(
    "bbvg/bot/wheels.py",
    "    @staticmethod\n    def _wheel_digest(key: str) -> str:\n",
    """    @staticmethod
    def _source_text(sources: list[str], primary_source: str) -> str:
        if primary_source == _MANUAL_SUBMISSION_SOURCE and not sources:
            return "📡 Добавлено через бот BB V.G."
        if not sources:
            return "📡 Источник неизвестен"
        labels: list[str] = []
        private_added = False
        for source in sources:
            if source.casefold().startswith("private_"):
                if not private_added:
                    labels.append("закрытый Telegram-канал")
                    private_added = True
                continue
            labels.append(f"@{html.escape(source)}")
        if not labels:
            return "📡 Источник неизвестен"
        prefix = "Источник" if len(labels) == 1 else "Источники"
        return f"📡 {prefix}: " + ", ".join(labels)

    @staticmethod
    def _wheel_digest(key: str) -> str:
""",
)
replace_once(
    "bbvg/bot/wheels.py",
    """            source = str(item.get("source") or "неизвестно")
            source_text = (
                "📡 Добавлено через бот BB V.G."
                if source == _MANUAL_SUBMISSION_SOURCE
                else f"📡 @{html.escape(source)}"
            )
""",
    """            primary_source = str(item.get("source") or "")
            sources = self._sources_for_item(snap, key, item)
            source_text = self._source_text(sources, primary_source)
""",
)
replace_once(
    "bbvg/bot/wheels.py",
    """    assert "wheel:part:" in WheelInteractionRuntime.handle_callback.__code__.co_consts
    print("BB V.G. wheel interaction subsystem self-test passed")
""",
    """    assert "wheel:part:" in WheelInteractionRuntime.handle_callback.__code__.co_consts
    source_line = panel._source_text(
        ["shadowkek", "burdakekw", "private_2445382077"],
        "shadowkek",
    )
    assert "@shadowkek" in source_line
    assert "@burdakekw" in source_line
    assert "закрытый Telegram-канал" in source_line
    assert "private_2445382077" not in source_line
    print("BB V.G. wheel interaction subsystem self-test passed")
""",
)

changelog = Path("docs/PROJECT_CHANGELOG_RU.md")
text = changelog.read_text(encoding="utf-8")
note = """## 2026-09-06 — диагностика автоучастия и несколько источников одного колеса

- `betboom_participation_browser.py` сохраняет безопасный XHR/fetch trace BetBoom для фактического клика участия. Заголовки, cookies, request body, query-параметры и секреты не сохраняются; диагностический JSON ограничен endpoint/status и разрешёнными полями результата/ошибки. Механика клика и retry не изменена.
- `auto_participation_recovery.py` сохраняет все свежие публикации одного wheel key, даже если для API-проверки используется только самая новая публикация.
- `bbvg/bot/wheels.py` показывает объединённые источники активного колеса; внутренний `private_*` идентификатор отображается как `закрытый Telegram-канал`.

"""
if note not in text:
    changelog.write_text(note + text, encoding="utf-8")
