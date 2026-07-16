from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import source_registry
from bbvg.bot.foundation import (
    MINIAPP_RELEASE,
    MINIAPP_URL,
    PanelFoundationMixin,
)
from bbvg.bot.users import UserManagementRuntime

SOURCE_REGISTRY_PATH = "source_registry.json"
_EMPTY_REGISTRY = {"version": 2, "summary": {}, "sources": []}


class SourceRegistryRuntime(UserManagementRuntime):
    """Approved source modes and merged source registry for the panel.

    This is the single owner of the source-registry contract formerly spread
    across the v22 panel layer and later source views. It keeps both supported
    representations:

    * the public list-shaped ``source_registry.json`` used by status screens;
    * the mode mapping used by source-management screens.
    """

    miniapp_url_for_chat = PanelFoundationMixin.miniapp_url_for_chat
    show_app_entry = PanelFoundationMixin.show_app_entry

    @staticmethod
    def source_mode_name(mode: str) -> str:
        return {
            "primary": "Основная проверка",
            "reserve": "Ночное наблюдение",
            "paused": "Временно приостановлены",
            "quiet": "Давно без колёс",
            "fast": "Основная проверка",
            "nightly": "Ночное наблюдение",
        }.get(mode, mode)

    def load_source_registry(self) -> dict[str, Any]:
        """Load the generated list-shaped source registry safely."""

        try:
            value = self.get_json_file(SOURCE_REGISTRY_PATH, dict(_EMPTY_REGISTRY))
        except Exception:
            value = dict(_EMPTY_REGISTRY)
        if not isinstance(value, dict):
            return dict(_EMPTY_REGISTRY)
        summary = value.get("summary")
        sources = value.get("sources")
        return {
            "version": max(2, int(value.get("version", 2) or 2)),
            "summary": summary if isinstance(summary, dict) else {},
            "sources": sources if isinstance(sources, list) else [],
        }

    def source_registry_fallback(self) -> dict[str, Any]:
        """Build the status registry from the current source lists and health."""

        snap = self.snapshot(force=True)
        configured: dict[str, tuple[str, str]] = {}
        for tier, values in (("primary", snap.fast), ("nightly", snap.nightly)):
            for value in values:
                username = str(value or "").strip().lstrip("@")
                if username:
                    configured.setdefault(username.casefold(), (username, tier))

        health_sources = (
            snap.health.get("sources", {}) if isinstance(snap.health, dict) else {}
        )
        health_by_name = {
            str(key).casefold(): candidate
            for key, candidate in health_sources.items()
            if isinstance(candidate, dict)
        } if isinstance(health_sources, dict) else {}

        rows: list[dict[str, Any]] = []
        for username, tier in configured.values():
            health = health_by_name.get(username.casefold(), {})
            checked = bool(
                health.get("last_checked_at") or int(health.get("checks", 0) or 0)
            )
            available = str(health.get("status") or "").casefold() == "ok"
            status = "available" if available else ("unavailable" if checked else "pending")
            rows.append(
                {
                    "username": username,
                    "tier": tier,
                    "status": status,
                    "checked": checked,
                    "available": available,
                    "reason": str(
                        health.get("failure_reason")
                        or health.get("last_error")
                        or ("источник доступен" if available else "ожидает первой проверки")
                    ),
                    "last_checked_at": health.get("last_checked_at"),
                }
            )

        return {
            "version": 2,
            "summary": {
                "total": len(rows),
                "primary": sum(row["tier"] == "primary" for row in rows),
                "nightly": sum(row["tier"] == "nightly" for row in rows),
                "checked": sum(bool(row["checked"]) for row in rows),
                "available": sum(bool(row["available"]) for row in rows),
                "unavailable": sum(row["status"] == "unavailable" for row in rows),
                "pending": sum(row["status"] == "pending" for row in rows),
            },
            "sources": rows,
        }

    def source_registry(self, snap: Any) -> dict[str, dict[str, Any]]:
        """Return the editable mode mapping used by source-management pages."""

        fallback = {
            "sources": {
                source: {
                    "mode": (
                        "primary"
                        if source.casefold() in {value.casefold() for value in snap.fast}
                        else "reserve"
                    ),
                    "manual_override": False,
                }
                for source in [*snap.fast, *snap.nightly]
            }
        }
        try:
            registry = self.get_json_file(SOURCE_REGISTRY_PATH, fallback)
        except Exception:
            registry = fallback
        raw = registry.get("sources") if isinstance(registry, dict) else {}
        data = raw if isinstance(raw, dict) else {}
        names = sorted(
            {
                *[str(value) for value in snap.fast],
                *[str(value) for value in snap.nightly],
                *[str(value) for value in data],
            },
            key=str.casefold,
        )
        result: dict[str, dict[str, Any]] = {}
        fast_names = {value.casefold() for value in snap.fast}
        for source in names:
            row = data.get(source)
            row = dict(row) if isinstance(row, dict) else {}
            if not row.get("mode"):
                row["mode"] = "primary" if source.casefold() in fast_names else "reserve"
            result[source] = row
        return result


def self_test() -> None:
    assert SourceRegistryRuntime.source_mode_name("primary") == "Основная проверка"
    assert SourceRegistryRuntime.source_mode_name("reserve") == "Ночное наблюдение"
    assert SourceRegistryRuntime.miniapp_url_for_chat is PanelFoundationMixin.miniapp_url_for_chat
    assert MINIAPP_RELEASE == "5.11.0"
    assert MINIAPP_URL.startswith("https://")

    panel = object.__new__(SourceRegistryRuntime)
    snap = SimpleNamespace(
        fast=["Primary"],
        nightly=["Nightly"],
        health={
            "sources": {
                "Primary": {"status": "ok", "last_checked_at": "2026-07-16T00:00:00Z"},
                "Nightly": {"status": "error", "checks": 1, "last_error": "test"},
            }
        },
    )
    panel.get_json_file = lambda path, fallback: {  # type: ignore[method-assign]
        "sources": {
            "Primary": {"mode": "paused", "manual_override": True},
            "Extra": {"mode": "quiet"},
        }
    }
    registry = panel.source_registry(snap)
    assert registry["Primary"]["mode"] == "paused"
    assert registry["Nightly"]["mode"] == "reserve"
    assert registry["Extra"]["mode"] == "quiet"

    panel.snapshot = lambda force=False: snap  # type: ignore[method-assign]
    panel.get_json_file = lambda path, fallback: fallback  # type: ignore[method-assign]
    fallback = panel.source_registry_fallback()
    assert fallback["summary"] == {
        "total": 2,
        "primary": 1,
        "nightly": 1,
        "checked": 2,
        "available": 1,
        "unavailable": 1,
        "pending": 0,
    }
    assert panel.load_source_registry() == _EMPTY_REGISTRY
    assert SOURCE_REGISTRY_PATH == "source_registry.json"
    assert callable(source_registry.build_registry)
    assert callable(source_registry.write_registry)
    print("BB V.G. source registry subsystem self-test passed")


if __name__ == "__main__":
    self_test()
