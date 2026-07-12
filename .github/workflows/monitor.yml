name: Monitor BetBoom wheels
run-name: BetBoom monitor — ${{ github.event_name }}

on:
  push:
    branches: [main]
    paths:
      - "monitor.py"
      - "telegram_monitor.py"
      - "public_sources.txt"
      - "identifier_sources.json"
      - "requirements.txt"
      - "self_test.py"
      - "preflight.py"
      - "nightly_discovery.py"
      - ".github/workflows/monitor.yml"
  workflow_dispatch:
    inputs:
      continuous:
        description: "Непрерывный режим с проверкой каждые 5 минут"
        required: false
        type: boolean
        default: false
  schedule:
    # Резервный перезапуск. Основной цикл сам запускает следующую смену.
    # Минута 23 выбрана не в начале часа, когда GitHub чаще задерживает cron.
    - cron: "23 */4 * * *"

permissions:
  contents: write
  actions: write

concurrency:
  group: betboom-wheel-monitor-continuous
  cancel-in-progress: true

jobs:
  monitor:
    runs-on: ubuntu-latest
    timeout-minutes: 350

    steps:
      - name: Checkout repository
        uses: actions/checkout@v4
        with:
          ref: main
          fetch-depth: 0

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: "3.12"
          cache: pip

      - name: Verify repository layout
        run: python preflight.py

      - name: Install dependencies
        run: python -m pip install -r requirements.txt

      - name: Check project files
        run: |
          python -m py_compile monitor.py telegram_monitor.py nightly_discovery.py
          python -m json.tool identifier_sources.json >/dev/null
          python self_test.py

      - name: Run monitor loop
        env:
          BOT_TOKEN: ${{ secrets.BOT_TOKEN }}
          BOT_CHAT_ID: ${{ secrets.BOT_CHAT_ID }}
          DISPLAY_TIMEZONE: Asia/Barnaul
          REQUEST_TIMEOUT_SECONDS: "15"
          MAX_WORKERS: "12"
          UNKNOWN_DEDUP_HOURS: "24"
          DEADLINE_GRACE_MINUTES: "30"
          HEARTBEAT_HOURS: "6"
          HEALTH_ALERT_COOLDOWN_HOURS: "6"
          STATUS_REPORT_HOURS: "12"
          MANUAL_RUN: ${{ github.event_name == 'workflow_dispatch' && inputs.continuous != true }}
          AUTO_RUN: ${{ github.event_name != 'workflow_dispatch' || inputs.continuous == true }}
          CONTINUOUS: ${{ github.event_name != 'workflow_dispatch' || inputs.continuous == true }}
        shell: bash
        run: |
          set -uo pipefail

          git config user.name "github-actions[bot]"
          git config user.email "41898282+github-actions[bot]@users.noreply.github.com"

          if [[ "$CONTINUOUS" == "true" ]]; then
            iterations=68
          else
            iterations=1
          fi

          for ((iteration=1; iteration<=iterations; iteration++)); do
            started_at=$(date +%s)
            echo "=== Check $iteration/$iterations at $(date -u +%FT%TZ) ==="

            # Pick up sources promoted by the nightly workflow while this runner is alive.
            git pull --rebase origin "${GITHUB_REF_NAME:-main}" || true

            python monitor.py || echo "Monitor iteration failed; the loop will retry."

            if ! git diff --quiet -- state.json; then
              git add state.json
              git commit -m "Update monitor state [skip ci]" || true
              git pull --rebase origin "${GITHUB_REF_NAME:-main}" || true
              git push origin "HEAD:${GITHUB_REF_NAME:-main}" || echo "State push failed; will retry next iteration."
            fi

            if (( iteration < iterations )); then
              elapsed=$(( $(date +%s) - started_at ))
              sleep_for=$(( 300 - elapsed ))
              if (( sleep_for < 5 )); then sleep_for=5; fi
              echo "Next check in ${sleep_for}s."
              sleep "$sleep_for"
            fi
          done

      - name: Start next continuous shift
        if: ${{ success() && (github.event_name != 'workflow_dispatch' || inputs.continuous == true) }}
        env:
          GH_TOKEN: ${{ github.token }}
        run: gh workflow run monitor.yml --ref main -f continuous=true
