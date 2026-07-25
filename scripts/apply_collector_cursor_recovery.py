from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def replace_once(path: Path, old: str, new: str, label: str) -> None:
    text = path.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{path}: {label}; expected one match, got {count}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


module = ROOT / "telegram_post_links_v2.py"
replace_once(
    module,
    "from typing import Any\n",
    "from typing import Any, Callable\n",
    "extend typing imports",
)
replace_once(
    module,
    '''PARTICIPATION_EVIDENCE_RE = re.compile(
    r"\\b(?:участв\\w*|ссылк\\w*|розыгрыш\\w*|приз\\w*)\\b",
    re.IGNORECASE,
)


''',
    '''PARTICIPATION_EVIDENCE_RE = re.compile(
    r"\\b(?:участв\\w*|ссылк\\w*|розыгрыш\\w*|приз\\w*)\\b",
    re.IGNORECASE,
)
COLLECTOR_CURSOR_STATE_KEY = "telegram_collector_cursors"
COLLECTOR_DIRECT_PROBE_LIMIT = max(
    3, int(os.getenv("COLLECTOR_DIRECT_PROBE_LIMIT", "8"))
)
COLLECTOR_GAP_SCAN_LIMIT = max(
    COLLECTOR_DIRECT_PROBE_LIMIT,
    int(os.getenv("COLLECTOR_GAP_SCAN_LIMIT", "80")),
)

_ACTIVE_MONITOR_STATE: dict[str, Any] | None = None
_COLLECTOR_CURSOR_DIRTY = False


''',
    "add collector recovery constants",
)
helpers = r'''
def _collector_sources(monitor_module: Any) -> set[str]:
    try:
        payload = json.loads(
            monitor_module.IDENTIFIER_SOURCES_PATH.read_text(encoding="utf-8")
        )
    except (OSError, json.JSONDecodeError, TypeError, AttributeError):
        payload = {}
    return {
        str(value or "").strip().lstrip("@").casefold()
        for value in (payload.get("collectors", []) if isinstance(payload, dict) else [])
        if str(value or "").strip().lstrip("@")
    }


def _message_identity(message: Any) -> tuple[str, int]:
    source = str(getattr(message, "source", "") or "").strip().lstrip("@").casefold()
    try:
        message_id = int(getattr(message, "message_id", 0) or 0)
    except (TypeError, ValueError):
        message_id = 0
    return source, message_id


def _merge_messages(*pages: list[Any]) -> list[Any]:
    merged: dict[tuple[str, int], Any] = {}
    for page in pages:
        for message in page:
            identity = _message_identity(message)
            if identity[1] > 0:
                merged[identity] = message
    return sorted(
        merged.values(),
        key=lambda message: (
            getattr(message, "date", None),
            _message_identity(message)[0],
            _message_identity(message)[1],
        ),
    )


def fetch_direct_public_post(
    monitor_module: Any,
    source: str,
    message_id: int,
):
    """Fetch one immutable Telegram post independently from the moving preview."""

    base = monitor_module.telegram_transport.public_message_url(source, message_id)
    slot = int(monitor_module.now_utc().timestamp() // 30)
    response = monitor_module.request_with_retries(
        "GET",
        f"{base}?embed=1&mode=tme&bbvg_fresh={slot}-{message_id}",
        attempts=1,
        timeout=min(8, int(monitor_module.REQUEST_TIMEOUT)),
        headers={
            "User-Agent": monitor_module.USER_AGENT,
            "Cache-Control": "no-cache, no-store, max-age=0",
            "Pragma": "no-cache",
        },
        allow_redirects=True,
    )
    response.raise_for_status()
    for message in parse_public_channel_html(monitor_module, source, response.text):
        if _message_identity(message)[1] == int(message_id):
            return message
    return None


def recover_collector_message_gaps(
    monitor_module: Any,
    state: dict[str, Any],
    results: dict[str, list[Any]],
    errors: dict[str, str],
    empty: list[str],
    sources: list[str],
    *,
    collector_sources: set[str] | None = None,
    direct_fetcher: Callable[[str, int], Any | None] | None = None,
) -> dict[str, Any]:
    """Recover collector posts by monotonic Telegram message ID.

    Telegram's public ``/s/channel`` preview is a moving, edge-dependent window.
    A non-empty HTTP 200 response does not prove that its highest message ID is
    current. Collector channels are therefore reconciled against a persisted
    cursor and the immutable direct embed endpoint before classification.
    """

    configured = collector_sources if collector_sources is not None else _collector_sources(
        monitor_module
    )
    configured = {str(value).casefold() for value in configured}
    cursor_rows = state.setdefault(COLLECTOR_CURSOR_STATE_KEY, {})
    summary: dict[str, Any] = {
        "checked_collectors": 0,
        "recovered_messages": 0,
        "recovered_sources": {},
        "listing_failures_recovered": 0,
        "changed": False,
    }
    fetch_one = direct_fetcher or (
        lambda source, message_id: fetch_direct_public_post(
            monitor_module, source, message_id
        )
    )

    for source in sources:
        normalized = str(source or "").strip().lstrip("@").casefold()
        if normalized not in configured:
            continue
        summary["checked_collectors"] = int(summary["checked_collectors"]) + 1
        listed = list(results.get(source, []))
        listed_ids = {
            _message_identity(message)[1]
            for message in listed
            if _message_identity(message)[1] > 0
        }
        page_max = max(listed_ids, default=0)
        record = cursor_rows.get(normalized)
        record = dict(record) if isinstance(record, dict) else {}
        try:
            stored_cursor = int(record.get("last_message_id", 0) or 0)
        except (TypeError, ValueError):
            stored_cursor = 0

        probe_ids: list[int] = []
        if stored_cursor > 0 and page_max > stored_cursor:
            for candidate in range(stored_cursor + 1, page_max + 1):
                if candidate not in listed_ids:
                    probe_ids.append(candidate)
                    if len(probe_ids) >= COLLECTOR_GAP_SCAN_LIMIT:
                        break
        future_base = max(stored_cursor, page_max)
        if future_base > 0:
            probe_ids.extend(
                range(
                    future_base + 1,
                    future_base + COLLECTOR_DIRECT_PROBE_LIMIT + 1,
                )
            )
        probe_ids = list(dict.fromkeys(probe_ids))

        recovered: list[Any] = []
        for candidate in probe_ids:
            try:
                message = fetch_one(source, candidate)
            except Exception as exc:
                print(
                    "WARNING collector direct cursor probe failed: "
                    f"@{source}/{candidate} {type(exc).__name__}: {exc}"
                )
                break
            if message is not None:
                recovered.append(message)

        merged = _merge_messages(listed, recovered)
        merged_ids = {
            _message_identity(message)[1]
            for message in merged
            if _message_identity(message)[1] > 0
        }
        if merged:
            results[source] = merged
            if source in empty:
                empty.remove(source)
        if recovered:
            if source in errors:
                errors.pop(source, None)
                summary["listing_failures_recovered"] = int(
                    summary["listing_failures_recovered"]
                ) + 1
            recovered_ids = sorted(
                {
                    _message_identity(message)[1]
                    for message in recovered
                    if _message_identity(message)[1] > 0
                }
            )
            summary["recovered_messages"] = int(summary["recovered_messages"]) + len(
                recovered_ids
            )
            summary["recovered_sources"][source] = recovered_ids
            print(
                "Recovered Telegram collector gap: "
                f"@{source} message_ids={','.join(str(value) for value in recovered_ids)}"
            )
            record["last_direct_recovered_at"] = monitor_module.now_utc().isoformat()
            record["last_direct_recovered_ids"] = recovered_ids
            record["direct_recovered_total"] = int(
                record.get("direct_recovered_total", 0) or 0
            ) + len(recovered_ids)

        highest = max([stored_cursor, *merged_ids], default=stored_cursor)
        before = dict(record)
        if highest > 0:
            record["last_message_id"] = highest
        if page_max > 0:
            record["last_page_message_id"] = page_max
        if page_max and stored_cursor and page_max < stored_cursor:
            record["last_stale_listing_at"] = monitor_module.now_utc().isoformat()
        if recovered:
            record["last_gap_recovered_from"] = min(
                _message_identity(message)[1] for message in recovered
            )
            record["last_gap_recovered_to"] = max(
                _message_identity(message)[1] for message in recovered
            )
        if record != before or normalized not in cursor_rows:
            cursor_rows[normalized] = record
            summary["changed"] = True

    state["last_collector_cursor_recovery"] = {
        "checked_collectors": summary["checked_collectors"],
        "recovered_messages": summary["recovered_messages"],
        "recovered_sources": summary["recovered_sources"],
        "listing_failures_recovered": summary["listing_failures_recovered"],
    }
    return summary


def _install_collector_cursor_recovery(monitor_module: Any) -> None:
    if getattr(monitor_module, "_bbvg_collector_cursor_recovery_installed", False):
        return
    original_load_state: Callable = monitor_module.load_state
    original_fetch_all: Callable = monitor_module.fetch_all_sources
    original_save_health: Callable = monitor_module.data_store.save_health

    def load_state_with_collector_cursor():
        global _ACTIVE_MONITOR_STATE
        state = original_load_state()
        if isinstance(state, dict):
            state.setdefault(COLLECTOR_CURSOR_STATE_KEY, {})
            _ACTIVE_MONITOR_STATE = state
        return state

    def fetch_all_with_collector_cursor(sources: list[str]):
        global _COLLECTOR_CURSOR_DIRTY
        results, errors, empty = original_fetch_all(sources)
        state = _ACTIVE_MONITOR_STATE
        if isinstance(state, dict):
            summary = recover_collector_message_gaps(
                monitor_module,
                state,
                results,
                errors,
                empty,
                sources,
            )
            _COLLECTOR_CURSOR_DIRTY = bool(summary.get("changed")) or _COLLECTOR_CURSOR_DIRTY
        return results, errors, empty

    def save_health_with_collector_cursor(value: dict[str, Any]) -> None:
        global _COLLECTOR_CURSOR_DIRTY
        if _COLLECTOR_CURSOR_DIRTY and isinstance(_ACTIVE_MONITOR_STATE, dict):
            monitor_module.save_state(_ACTIVE_MONITOR_STATE)
            _COLLECTOR_CURSOR_DIRTY = False
        original_save_health(value)

    fetch_all_with_collector_cursor.__module__ = "telegram_transport"
    monitor_module.load_state = load_state_with_collector_cursor
    monitor_module.fetch_all_sources = fetch_all_with_collector_cursor
    monitor_module.data_store.save_health = save_health_with_collector_cursor
    monitor_module._bbvg_collector_cursor_recovery_installed = True


'''
replace_once(
    module,
    "def _ai_wheel_evidence_cap(text: str, classification: str = \"\") -> float:\n",
    helpers + "def _ai_wheel_evidence_cap(text: str, classification: str = \"\") -> float:\n",
    "insert collector cursor recovery",
)
replace_once(
    module,
    '''    except Exception as exc:
        print(
            "WARNING wheel detection reliability integration failed: "
            f"{type(exc).__name__}: {exc}"
        )


''',
    '''    except Exception as exc:
        print(
            "WARNING wheel detection reliability integration failed: "
            f"{type(exc).__name__}: {exc}"
        )

    _install_collector_cursor_recovery(monitor_module)


''',
    "install collector cursor recovery last",
)
replace_once(
    module,
    '''    fresh = fresh_public_source_url(monitor, "jestercast")
    assert "bbvg_fresh=" in fresh
    before = fresh_public_source_url(monitor, "jestercast", before=100)
    assert "before=100" in before and "&bbvg_fresh=" in before
    install(monitor)
''',
    '''    fresh = fresh_public_source_url(monitor, "jestercast")
    assert "bbvg_fresh=" in fresh
    before = fresh_public_source_url(monitor, "jestercast", before=100)
    assert "before=100" in before and "&bbvg_fresh=" in before

    listed = [
        monitor.Message(
            source="kolesaBB",
            message_id=249,
            date=datetime.fromisoformat("2026-07-25T11:38:08+00:00"),
            text="https://betboom.ru/freestream/CTOM19",
            message_url="https://telegram.me/kolesaBB/249",
        )
    ]
    recovered_rows = {
        250: monitor.Message(
            source="kolesaBB",
            message_id=250,
            date=datetime.fromisoformat("2026-07-25T13:39:30+00:00"),
            text="https://betboom.ru/freestream/pomidor1",
            message_url="https://telegram.me/kolesaBB/250",
        ),
        251: monitor.Message(
            source="kolesaBB",
            message_id=251,
            date=datetime.fromisoformat("2026-07-25T13:48:17+00:00"),
            text="https://betboom.ru/freestream/CTOM22",
            message_url="https://telegram.me/kolesaBB/251",
        ),
    }
    cursor_state: dict[str, Any] = {}
    cursor_results = {"kolesaBB": listed}
    cursor_errors: dict[str, str] = {}
    cursor_empty: list[str] = []
    summary = recover_collector_message_gaps(
        monitor,
        cursor_state,
        cursor_results,
        cursor_errors,
        cursor_empty,
        ["kolesaBB"],
        collector_sources={"kolesabb"},
        direct_fetcher=lambda _source, message_id: recovered_rows.get(message_id),
    )
    assert [message.message_id for message in cursor_results["kolesaBB"]] == [
        249,
        250,
        251,
    ]
    assert summary["recovered_messages"] == 2
    assert cursor_state[COLLECTOR_CURSOR_STATE_KEY]["kolesabb"]["last_message_id"] == 251

    error_results: dict[str, list[Any]] = {}
    error_rows = {252: monitor.Message(
        source="kolesaBB",
        message_id=252,
        date=datetime.fromisoformat("2026-07-25T14:00:00+00:00"),
        text="обычный новый пост",
        message_url="https://telegram.me/kolesaBB/252",
    )}
    error_summary = recover_collector_message_gaps(
        monitor,
        cursor_state,
        error_results,
        {"kolesaBB": "simulated stale listing failure"},
        [],
        ["kolesaBB"],
        collector_sources={"kolesabb"},
        direct_fetcher=lambda _source, message_id: error_rows.get(message_id),
    )
    assert [message.message_id for message in error_results["kolesaBB"]] == [252]
    assert error_summary["listing_failures_recovered"] == 1
    assert cursor_state[COLLECTOR_CURSOR_STATE_KEY]["kolesabb"]["last_message_id"] == 252

    install(monitor)
''',
    "add collector cursor self-test",
)

contracts = ROOT / "tests" / "test_current_contracts.py"
replace_once(
    contracts,
    "import system_checks_v3\n",
    "import system_checks_v3\nimport telegram_post_links_v2\n",
    "import collector recovery contract",
)
replace_once(
    contracts,
    '''        source_registry.self_test()
        source_intelligence_alerts.self_test()
''',
    '''        source_registry.self_test()
        source_intelligence_alerts.self_test()
        telegram_post_links_v2.self_test()
''',
    "run collector recovery contract",
)

changelog = ROOT / "docs" / "PROJECT_CHANGELOG_RU.md"
entry = '''## 2026-07-25 — прямой cursor-recovery для Telegram collector-каналов

Устранён класс пропусков, при котором публичная страница `/s/channel` отвечала
`200 OK` и содержала сообщения, но её скользящее окно на отдельном GitHub Runner
отставало от реального канала. Ранее health ошибочно считал такой источник
полностью исправным, поэтому новые публикации могли не попасть даже в `seen`.

Для collector-каналов из `identifier_sources.json` добавлен монотонный курсор
Telegram message ID. После обычной загрузки монитор независимо проверяет следующие
ID через прямые immutable embed-страницы, восстанавливает пробелы и только затем
передаёт объединённый поток в классификацию, уведомления и автоучастие. Механизм
работает и при ошибке основной страницы, не помечает будущие отсутствующие ID как
прочитанные и сохраняет курсор вместе с основным `state.json` после обработки.

Regression-тест воспроизводит инцидент `kolesaBB`: обычное окно заканчивается на
ID 249, а прямой проход обязан восстановить ID 250 (`pomidor1`) и 251 (`CTOM22`).
Также проверено восстановление следующего ID при полном отказе listing-запроса.

**Backup перед изменением:**
`backup/before-collector-cursor-recovery-20260725`.

'''
text = changelog.read_text(encoding="utf-8")
if entry.splitlines()[0] not in text:
    marker = "---\n\n"
    if marker not in text:
        raise RuntimeError("changelog insertion marker not found")
    changelog.write_text(text.replace(marker, marker + entry, 1), encoding="utf-8")

print("collector cursor recovery patch applied")
