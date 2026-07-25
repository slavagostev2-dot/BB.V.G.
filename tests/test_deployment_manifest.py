from bbvg.deployment_manifest import build_manifest


def test_manifest_requires_one_exact_component_sha():
    sha = "a" * 40
    manifest = build_manifest(
        {"head_sha": sha, "run_started_at": "2026-07-26T00:00:00+00:00"},
        {"head_sha": sha, "started_at": "2026-07-26T00:01:00+00:00"},
    )
    assert manifest["compatible"] is True
    assert {
        component["sha"]
        for component in manifest["components"].values()
    } == {sha}

    mismatch = build_manifest(
        {"head_sha": sha},
        {"head_sha": "b" * 40},
    )
    assert mismatch["compatible"] is False
