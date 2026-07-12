name: Nightly Telegram source discovery

on:
  workflow_dispatch:
  schedule:
    - cron: "27 20 * * *"

permissions:
  contents: write

concurrency:
  group: betboom-wheel-nightly-discovery
  cancel-in-progress: false

jobs:
  discovery:
    runs-on: ubuntu-latest
    timeout-minutes: 35

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
          python -m py_compile monitor.py monitor_data.py nightly_discovery.py daily_report.py
          python self_test.py

      - name: Scan nightly source catalog
        env:
          BOT_TOKEN: ${{ secrets.BOT_TOKEN }}
          BOT_CHAT_ID: ${{ secrets.BOT_CHAT_ID }}
          DISPLAY_TIMEZONE: Asia/Barnaul
          REQUEST_TIMEOUT_SECONDS: "15"
          DISCOVERY_LOOKBACK_HOURS: "48"
          DISCOVERY_PAGES: "4"
          MAX_NEW_POST_AGE_MINUTES: "360"
          FRESH_UNKNOWN_POST_MINUTES: "0"
          QUARANTINE_FAILURE_THRESHOLD: "3"
          QUARANTINE_EMPTY_THRESHOLD: "4"
          QUARANTINE_RECHECK_HOURS: "6"
          MANUAL_RUN: ${{ github.event_name == 'workflow_dispatch' }}
        run: python nightly_discovery.py

      - name: Save discovery, health and statistics
        shell: bash
        run: |
          files=(public_sources.txt source_catalog.txt discovery_state.json)
          if git diff --quiet -- "${files[@]}"; then
            echo "Discovery data did not change."
            exit 0
          fi

          git config user.name "github-actions[bot]"
          git config user.email "41898282+github-actions[bot]@users.noreply.github.com"
          git add "${files[@]}"
          git commit -m "Update Telegram source discovery [skip ci]"
          git pull --rebase origin "${GITHUB_REF_NAME}"
          git push origin "HEAD:${GITHUB_REF_NAME}"
