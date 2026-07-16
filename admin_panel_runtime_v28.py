from __future__ import annotations

import argparse
from typing import Any

import admin_action_v2
from admin_panel_runtime_v26 import TelegramPanelRuntimeV26


class TelegramPanelRuntimeV28(TelegramPanelRuntimeV26):
    """Reliable administrator wheel actions through GitHub Contents API."""

    def _apply_admin_action_direct(self, action: str, value: str) -> dict[str, Any]:
        last_error: Exception | None = None
        for attempt in range(1, 4):
            state = self.get_json_file("state.json", {})
            health = self.get_json_file(
                "source_health.json",
                {"version": 1, "sources": {}},
            )
            stats = self.get_json_file(
                "source_stats.json",
                {"version": 1, "sources": {}, "daily": {}},
            )
            result = admin_action_v2.legacy.apply_action(
                state,
                health,
                stats,
                action,
                value,
            )
            changed: list[tuple[str, dict[str, Any]]] = []
            if result.get("state_changed"):
                changed.append(("state.json", state))
            if result.get("health_changed"):
                changed.append(("source_health.json", health))
            if result.get("stats_changed"):
                changed.append(("source_stats.json", stats))
            if not changed:
                return result

            try:
                for path, payload in changed:
                    self.update_file(
                        path,
                        self._serialize_json(payload),
                        f"Apply BB V.G. administrator action: {action} [skip ci]",
                    )
            except RuntimeError as exc:
                last_error = exc
                text = str(exc)
                if attempt < 3 and any(code in text for code in (" 409 ", " 422 ")):
                    continue
                raise

            with self.snapshot_lock:
                self.snapshot_value = None
                self.snapshot_updated_at = 0.0
            self.refresh_requested.set()
            return result

        raise RuntimeError(
            "Не удалось сохранить действие после трёх попыток"
        ) from last_error


def self_test() -> None:
    panel = TelegramPanelRuntimeV28()
    assert panel._json_text('{"ok": true}', {}) == {"ok": True}
    assert panel._serialize_json({"ok": True}).strip() == '{\n  "ok": true\n}'
    print("admin_panel_runtime_v28 contents-api action self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return 0
    return TelegramPanelRuntimeV28().run()


if __name__ == "__main__":
    raise SystemExit(main())
