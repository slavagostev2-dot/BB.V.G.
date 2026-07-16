from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import source_registry
from bbvg.bot.foundation import PanelFoundationMixin
from bbvg.bot.users import UserManagementRuntime

SOURCE_REGISTRY_PATH = "source_registry.json"


class SourceRegistryRuntime(UserManagementRuntime):
    """Approved source modes and merged source registry for the panel."""

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

    def source_registry(self, snap: Any) -> dict[str, dict[str, Any]]:
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
        for source in names:
            row = data.get(source)
            row = dict(row) if isinstance(row, dict) else {}
            if not row.get("mode"):
                row["mode"] = (
                    "primary"
                    if source.casefold() in {value.casefold() for value in snap.fast}
                    else "reserve"
                )
            result[source] = row
        return result


def self_test() -> None:
    assert SourceRegistryRuntime.source_mode_name("primary") == "Основная проверка"
    assert SourceRegistryRuntime.source_mode_name("reserve") == "Ночное наблюдение"
    assert SourceRegistryRuntime.miniapp_url_for_chat is PanelFoundationMixin.miniapp_url_for_chat

    panel = object.__new__(SourceRegistryRuntime)
    snap = SimpleNamespace(fast=["Primary"], nightly=["Nightly"])
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
    assert SOURCE_REGISTRY_PATH == "source_registry.json"
    assert hasattr(source_registry, "load_registry")
    print("BB V.G. source registry subsystem self-test passed")


if __name__ == "__main__":
    self_test()
