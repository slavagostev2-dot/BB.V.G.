from __future__ import annotations

import base64
import json
from pathlib import Path

import pytest

import runtime_state_publisher


def _event(key: str = "wheel") -> dict[str, object]:
    return {
        "wheel_key": key,
        "identifier": key,
        "action_id": 42,
        "server_start_at": "2026-07-26T12:00:00+00:00",
        "url": f"https://betboom.ru/freestream/{key}",
    }


def test_monitor_merge_preserves_browser_success_without_resurrecting_closed_wheel() -> None:
    local = {
        "source_cursor": {"kolesaBB": 260},
        "active_wheels": {
            "wheel": {
                **_event(),
                "last_checked_at": "2026-07-26T12:02:00+00:00",
                "verification_status": "confirmed",
            }
        },
        "auto_participation_events": {
            "evt:wheel": {
                "wheel_key": "wheel",
                "status": "queued",
                "recorded_at": "2026-07-26T12:00:10+00:00",
            }
        },
    }
    remote = {
        "source_cursor": {"kolesaBB": 250},
        "active_wheels": {
            "wheel": {
                **_event(),
                "last_checked_at": "2026-07-26T11:59:00+00:00",
                "participating": True,
                "auto_participation_status": "participated",
                "auto_participation_confirmed_at": "2026-07-26T12:01:00+00:00",
            },
            "closed": {
                **_event("closed"),
                "participating": True,
                "auto_participation_status": "participated",
            },
        },
        "auto_participation_events": {
            "evt:wheel": {
                "wheel_key": "wheel",
                "status": "participated",
                "attempted_at": "2026-07-26T12:01:00+00:00",
                "bot_success_pending_at": "2026-07-26T12:01:01+00:00",
            }
        },
    }

    merged = runtime_state_publisher.merge_monitor_runtime_state(local, remote)

    assert merged["source_cursor"] == {"kolesaBB": 260}
    assert set(merged["active_wheels"]) == {"wheel"}
    item = merged["active_wheels"]["wheel"]
    assert item["last_checked_at"] == "2026-07-26T12:02:00+00:00"
    assert item["verification_status"] == "confirmed"
    assert item["participating"] is True
    assert item["auto_participation_status"] == "participated"
    result = merged["auto_participation_events"]["evt:wheel"]
    assert result["status"] == "participated"
    assert result["bot_success_pending_at"]


def test_monitor_publish_retries_cas_and_writes_latest_merge_back(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    local_path = tmp_path / "state.json"
    local_path.write_text(
        json.dumps(
            {
                "monitor_field": "fresh",
                "active_wheels": {"wheel": _event()},
                "auto_participation_events": {},
            }
        ),
        encoding="utf-8",
    )
    remotes = [
        {
            "active_wheels": {
                "wheel": {
                    **_event(),
                    "participating": True,
                    "auto_participation_status": "participated",
                }
            },
            "auto_participation_events": {
                "evt:first": {
                    "status": "participated",
                    "attempted_at": "2026-07-26T12:01:00+00:00",
                }
            },
        },
        {
            "active_wheels": {
                "wheel": {
                    **_event(),
                    "participating": True,
                    "auto_participation_status": "participated",
                    "auto_participation_confirmed_at": "2026-07-26T12:02:00+00:00",
                }
            },
            "auto_participation_events": {
                "evt:first": {
                    "status": "participated",
                    "attempted_at": "2026-07-26T12:01:00+00:00",
                },
                "evt:second": {
                    "status": "participated",
                    "attempted_at": "2026-07-26T12:02:00+00:00",
                },
            },
        },
    ]

    class Response:
        def __init__(self, status_code: int, payload: dict, text: str = ""):
            self.status_code = status_code
            self._payload = payload
            self.text = text

        def json(self):
            return self._payload

    reads = 0
    writes: list[dict] = []

    def get(*args, **kwargs):
        nonlocal reads
        payload = remotes[min(reads, len(remotes) - 1)]
        reads += 1
        return Response(
            200,
            {
                "sha": f"remote-{reads}",
                "content": base64.b64encode(
                    json.dumps(payload).encode("utf-8")
                ).decode("ascii"),
            },
        )

    def put(*args, **kwargs):
        writes.append(kwargs["json"])
        if len(writes) == 1:
            return Response(409, {}, "conflict")
        return Response(200, {"commit": {"sha": "published"}})

    monkeypatch.setattr(runtime_state_publisher.requests, "get", get)
    monkeypatch.setattr(runtime_state_publisher.requests, "put", put)
    monkeypatch.setattr(runtime_state_publisher.time, "sleep", lambda _seconds: None)

    result = runtime_state_publisher.publish_monitor_runtime(
        local_path,
        token="token",
        repository="owner/repo",
    )

    assert result == {
        "file": "state.json",
        "branch": "runtime-state",
        "attempt": 2,
        "changed": True,
        "sha": "published",
    }
    assert reads == 2
    assert writes[-1]["sha"] == "remote-2"
    published = json.loads(base64.b64decode(writes[-1]["content"]))
    assert published["monitor_field"] == "fresh"
    assert "evt:first" in published["auto_participation_events"]
    assert "evt:second" in published["auto_participation_events"]
    assert published["active_wheels"]["wheel"]["participating"] is True
    persisted = json.loads(local_path.read_text(encoding="utf-8"))
    assert persisted == published


def test_monitor_publish_reads_large_state_via_git_blob(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    local_path = tmp_path / "state.json"
    local_path.write_text(
        json.dumps({"monitor_field": "fresh", "active_wheels": {}}),
        encoding="utf-8",
    )
    remote = {"auto_participation_events": {"evt:kept": {"status": "participated"}}}

    class Response:
        def __init__(self, status_code: int, payload: dict, text: str = ""):
            self.status_code = status_code
            self._payload = payload
            self.text = text

        def json(self):
            return self._payload

    reads: list[str] = []
    writes: list[dict] = []

    def get(url, **kwargs):
        reads.append(url)
        if "/contents/state.json" in url:
            return Response(
                200,
                {
                    "sha": "large-state-blob",
                    "size": 1_049_887,
                    "encoding": "none",
                    "content": "",
                },
            )
        assert url.endswith("/git/blobs/large-state-blob")
        return Response(
            200,
            {
                "encoding": "base64",
                "content": base64.b64encode(json.dumps(remote).encode()).decode(),
            },
        )

    def put(*args, **kwargs):
        writes.append(kwargs["json"])
        return Response(200, {"commit": {"sha": "published"}})

    monkeypatch.setattr(runtime_state_publisher.requests, "get", get)
    monkeypatch.setattr(runtime_state_publisher.requests, "put", put)

    result = runtime_state_publisher.publish_monitor_runtime(
        local_path,
        token="token",
        repository="owner/repo",
    )

    assert result["changed"] is True
    assert reads == [
        "https://api.github.com/repos/owner/repo/contents/state.json",
        "https://api.github.com/repos/owner/repo/git/blobs/large-state-blob",
    ]
    published = json.loads(base64.b64decode(writes[-1]["content"]))
    assert published["monitor_field"] == "fresh"
    assert published["auto_participation_events"]["evt:kept"]["status"] == (
        "participated"
    )


def test_monitor_workflow_uses_one_publisher_and_does_not_block_later_files() -> None:
    workflow = Path(".github/workflows/monitor.yml").read_text(encoding="utf-8")
    assert '"runtime_state_publisher.py"' in workflow
    assert "python runtime_state_publisher.py --self-test" in workflow
    block = workflow.split("          push_runtime() {", 1)[1].split(
        "          BBVG_HEAD_SHA=", 1
    )[0]
    assert '--publish-monitor-runtime "$file"' in block
    assert "publish_failed=true" in block
    assert "continue" in block
    publish_loop = block.split("            publish_failed=false", 1)[1]
    assert publish_loop.index("publish_failed=true") < publish_loop.index("done")
    assert publish_loop.index("done") < publish_loop.rindex("return 1")
    assert '"/repos/${GITHUB_REPOSITORY}/git/blobs/${blob_sha}"' in workflow
    assert 'python - "${file}.runtime"' in workflow
    assert 'mv "${file}.runtime" "$file"' in workflow
    assert workflow.index('python - "${file}.runtime"') < workflow.index(
        'mv "${file}.runtime" "$file"'
    )
