from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import telegram_private_sources as private

UTC = timezone.utc


def test_parse_private_sources_normalizes_channel_ids() -> None:
    rows = private.parse_sources(
        json.dumps(
            {
                "closed_wheels": {"peer_id": -1001234567890},
                "second": "987654321",
            }
        )
    )
    assert [(row.alias, row.peer_id) for row in rows] == [
        ("closed_wheels", 1234567890),
        ("second", 987654321),
    ]


def test_private_message_keeps_hidden_button_url() -> None:
    monitor = SimpleNamespace(
        now_utc=lambda: datetime(2026, 9, 6, tzinfo=UTC),
        Message=lambda **kwargs: SimpleNamespace(**kwargs),
    )
    source = private.PrivateSource("closed_wheels", 1234567890)
    entity = SimpleNamespace(id=1234567890, username=None)
    button = SimpleNamespace(url="https://betboom.ru/freestream/private-wheel")
    message = SimpleNamespace(
        id=77,
        date=datetime(2026, 9, 6, tzinfo=UTC),
        raw_text="Колесо",
        entities=[],
        buttons=[[button]],
    )
    converted = private._to_monitor_message(monitor, source, entity, message)
    assert converted.source == "closed_wheels"
    assert "https://betboom.ru/freestream/private-wheel" in converted.text
    assert converted.message_url == "https://telegram.me/c/1234567890/77"


def test_install_routes_private_alias_through_authenticated_transport(
    monkeypatch,
    tmp_path: Path,
) -> None:
    sources_path = tmp_path / "public_sources.txt"
    sources_path.write_text("public_one\n", encoding="utf-8")
    public_calls: list[list[str]] = []

    def read_list(path: Path):
        return [
            line.strip()
            for line in Path(path).read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]

    def fetch_all(sources: list[str]):
        public_calls.append(list(sources))
        return ({source: [f"public:{source}"] for source in sources}, {}, [])

    monitor = SimpleNamespace(
        SOURCES_PATH=sources_path,
        read_list=read_list,
        fetch_all_sources=fetch_all,
    )
    monkeypatch.setenv(
        "TELEGRAM_PRIVATE_SOURCES_JSON",
        json.dumps({"closed_wheels": {"peer_id": 1234567890}}),
    )

    def fake_private_fetch(_monitor, requested):
        assert requested == [private.PrivateSource("closed_wheels", 1234567890)]
        return ({"closed_wheels": ["private:closed_wheels"]}, {}, [])

    monkeypatch.setattr(private, "fetch_private_sources", fake_private_fetch)
    private.install(monitor)

    configured = monitor.read_list(sources_path)
    assert configured == ["public_one", "closed_wheels"]
    results, errors, empty = monitor.fetch_all_sources(configured)
    assert public_calls == [["public_one"]]
    assert results == {
        "public_one": ["public:public_one"],
        "closed_wheels": ["private:closed_wheels"],
    }
    assert errors == {}
    assert empty == []
    assert monitor.fetch_all_sources.__module__ == "telegram_transport"


def test_private_alias_is_not_added_to_unrelated_lists(monkeypatch, tmp_path: Path) -> None:
    sources_path = tmp_path / "public_sources.txt"
    other_path = tmp_path / "other.txt"
    sources_path.write_text("public_one\n", encoding="utf-8")
    other_path.write_text("other\n", encoding="utf-8")

    monitor = SimpleNamespace(
        SOURCES_PATH=sources_path,
        read_list=lambda path: Path(path).read_text(encoding="utf-8").splitlines(),
        fetch_all_sources=lambda sources: ({}, {}, []),
    )
    monkeypatch.setenv(
        "TELEGRAM_PRIVATE_SOURCES_JSON",
        json.dumps({"closed_wheels": {"peer_id": 1234567890}}),
    )
    private.install(monitor)
    assert monitor.read_list(other_path) == ["other"]
