name: BB V.G. auto participation

on:
  workflow_dispatch:
    inputs:
      probe:
        description: "Run isolated zonertg11 button probe instead of normal auto participation"
        required: false
        type: boolean
        default: false
      event_payload:
        description: "Canonical durable event JSON from the monitor outbox"
        required: false
        type: string
        default: ""

permissions:
  contents: read

concurrency:
  group: bb-vg-auto-participation
  cancel-in-progress: false

jobs:
  participate:
    permissions:
      contents: write
    runs-on: ubuntu-latest
    timeout-minutes: 20
    steps:
      - name: Checkout repository
        uses: actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683 # v4.2.2
        with:
          ref: main
          fetch-depth: 0

      - name: Refresh latest monitor state
        run: |
          sleep 2
          git fetch origin main
          git reset --hard origin/main

      - name: Detect isolated probe request
        id: mode
        shell: bash
        run: |
          if [[ "${{ inputs.probe }}" == "true" || -f auto_participation_probe.trigger ]]; then
            echo "probe=true" >> "$GITHUB_OUTPUT"
          else
            echo "probe=false" >> "$GITHUB_OUTPUT"
          fi

      - name: Set up Python
        uses: actions/setup-python@a26af69be951a213d495a4c3e4e4022e16d87065 # v5.6.0
        with:
          python-version: "3.12"
          cache: pip

      - name: Install dependencies
        run: python -m pip install -r requirements.txt

      - name: Run isolated participation probe
        if: ${{ steps.mode.outputs.probe == 'true' }}
        env:
          BETBOOM_PROBE_URL: "https://betboom.ru/freestream/zonertg11"
          BETBOOM_STORAGE_STATE_JSON_PART1: ${{ secrets.BETBOOM_STORAGE_STATE_JSON_PART1 }}
          BETBOOM_STORAGE_STATE_JSON_PART2: ${{ secrets.BETBOOM_STORAGE_STATE_JSON_PART2 }}
          BETBOOM_BROWSER_CHANNEL: chrome
        run: python auto_participation_probe.py

      - name: Persist isolated probe result
        if: ${{ always() && steps.mode.outputs.probe == 'true' }}
        run: |
          test -f auto_participation_probe_result.json || echo '{"status":"result_missing"}' > auto_participation_probe_result.json
          git config user.name "github-actions[bot]"
          git config user.email "41898282+github-actions[bot]@users.noreply.github.com"
          git add auto_participation_probe_result.json
          git rm -f auto_participation_probe.trigger || true
          git commit -m "Record isolated auto participation probe [skip ci]" || exit 0
          git pull --rebase origin main
          git push origin HEAD:main

      - name: Validate auto participation
        if: ${{ steps.mode.outputs.probe != 'true' }}
        env:
          BETBOOM_AUTO_PARTICIPATE: "true"
          BETBOOM_STORAGE_STATE_JSON_PART1: ${{ secrets.BETBOOM_STORAGE_STATE_JSON_PART1 }}
          BETBOOM_STORAGE_STATE_JSON_PART2: ${{ secrets.BETBOOM_STORAGE_STATE_JSON_PART2 }}
          BETBOOM_STORAGE_STATE_JSON_PART3: ${{ secrets.BETBOOM_STORAGE_STATE_JSON_PART3 }}
          BETBOOM_STORAGE_STATE_JSON_PART4: ${{ secrets.BETBOOM_STORAGE_STATE_JSON_PART4 }}
          BETBOOM_STORAGE_STATE_JSON_PART5: ${{ secrets.BETBOOM_STORAGE_STATE_JSON_PART5 }}
          BETBOOM_STORAGE_STATE_JSON_PART6: ${{ secrets.BETBOOM_STORAGE_STATE_JSON_PART6 }}
          BETBOOM_ACCOUNT2_LABEL: "Аккаунт 2"
          BETBOOM_ACCOUNT2_TELEGRAM_USER: "Вячеслав"
          BETBOOM_ACCOUNT3_LABEL: "xFLARXx"
          BETBOOM_ACCOUNT3_TELEGRAM_USER: "xFLARXx"
        run: |
          python - <<'PY'
          import betboom_auto_participation as auto
          import auto_participation_recovery
          import auto_participation_bot_sync
          import auto_participation_owner_sync
          import betboom_account_participation
          import betboom_participation_browser
          import xflarxx_account_participation
          required = ("_event_token", "_eligible_for_event_attempt", "process_new_wheel_events")
          missing = [name for name in required if not hasattr(auto, name)]
          if missing:
              raise SystemExit("Missing auto participation functions: " + ", ".join(missing))
          if not auto.configured():
              raise SystemExit("Primary BetBoom auto participation session is not configured")
          if not betboom_account_…231621 tokens truncated…eel_event_runtime_installed = True


def self_test() -> None:
    published = datetime(2026, 7, 15, 12, 17, tzinfo=UTC)

    def parser(text: str, at: datetime):
        return at + timedelta(hours=2), "относительное время"

    available_at, _ = infer_availability(
        "Через 2 часа запущу колесо с фрибетами", published, parser
    )
    assert available_at == published + timedelta(hours=2)
    assert infer_availability("Итоги через 2 часа", published, parser)[0] is None

    reused = {
        "active_wheels": {"same": {"action_id": 100}},
        "participating_wheels": {"same": {"marked_at": published.isoformat()}},
        "wheel_publications": {"same": [{"source": "old"}]},
    }
    removed = reset_changed_action_state(reused, "same", 101)
    assert "active_wheels" in removed
    assert "same" not in reused["active_wheels"]
    assert "same" not in reused["participating_wheels"]
    assert "same" not in reused["wheel_publications"]

    state = {
        "inactive_wheels": {
            "risen": {"marked_at": "2026-07-14T12:00:00+00:00"}
        },
        "manual_deadlines": {
            "risen": {"updated_at": "2026-07-14T12:01:00+00:00"}
        },
        "recently_completed_wheels": {
            "risen": {"removed_at": "2026-07-14T14:00:00+00:00"}
        },
    }
    removed = reset_stale_event_state(state, "risen", published)
    assert {"inactive_wheels", "manual_deadlines", "recently_completed_wheels"} <= set(removed)

    observations: dict[str, Any] = {}
    record_generation_observation(
        observations,
        "same",
        100,
        published,
        current=published,
        status="active",
    )
    record_generation_observation(
        observations,
        "same",
        100,
        published + timedelta(hours=6),
        current=published + timedelta(hours=6),
        status="active",
    )
    report = generation_observation_report(
        observations, current=published + timedelta(hours=6)
    )
    assert report["same_action_id_multiple_starts"] == [
        {
            "wheel_key": "same",
            "action_id": 100,
            "server_start_at": [
                published.isoformat(),
                (published + timedelta(hours=6)).isoformat(),
            ],
        }
    ]
    print("recurring wheel event and availability self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="BB V.G. recurring wheel identity diagnostics"
    )
    parser.add_argument(
        "--observation-report",
        type=Path,
        metavar="STATE_JSON",
        help="print a JSON report from the bounded generation history",
    )
    args = parser.parse_args()
    if args.observation_report is None:
        self_test()
        return 0
    state = json.loads(args.observation_report.read_text(encoding="utf-8"))
    print(json.dumps(generation_observation_report(state), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
