from __future__ import annotations

import json

import auto_participation_probe as probe
import betboom_auto_participation as auto


def test_result_payload_preserves_browser_outcome() -> None:
    result = auto.ParticipationResult(
        True,
        "already_participating",
        "confirmed",
        "runtime/browser_diagnostics/example",
    )
    payload = probe._result_payload(
        "https://betboom.ru/freestream/example",
        result,
    )
    assert payload == {
        "success": True,
        "status": "already_participating",
        "detail": "confirmed",
        "artifact_url": "runtime/browser_diagnostics/example",
        "url": "https://betboom.ru/freestream/example",
    }


def test_invalid_probe_url_writes_diagnostic_without_browser(
    monkeypatch, tmp_path
) -> None:
    result_path = tmp_path / "probe.json"
    monkeypatch.setattr(probe, "RESULT_PATH", result_path)
    monkeypatch.setenv("BETBOOM_PROBE_URL", "https://example.com/not-a-wheel")

    assert probe.main() == 2
    payload = json.loads(result_path.read_text(encoding="utf-8"))
    assert payload["status"] == "invalid_probe_url"
    assert payload["success"] is False
