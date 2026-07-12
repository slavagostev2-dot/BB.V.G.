name: Nightly Telegram source discovery

on:
  push:
    branches: [main]
    paths:
      - "nightly_discovery.py"
      - "source_catalog.txt"
      - "public_sources.txt"
      - ".github/workflows/nightly-discovery.yml"
      - "preflight.py"
      - "self_test.py"
      - "requirements.txt"
  workflow_dispatch:
  schedule:
    # 20:27 UTC = 03:27 в Барнауле. Не начало часа.
    - cron: "27 20 * * *"

permissions:
  contents: write
  actions: write

concurrency:
  group: betboom-wheel-nightly-discovery
  cancel-in-progress: false

jobs:
  discovery:
    runs-on: ubuntu-latest
    timeout-minutes: 30

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
          python -m py_compile monitor.py nightly_discovery.py
          python self_test.py

      - name: Scan nightly source catalog
        env:
          BOT_TOKEN: ${{ secrets.BOT_TOKEN }}
          BOT_CHAT_ID: ${{ secrets.BOT_CHAT_ID }}
          DISPLAY_TIMEZONE: Asia/Barnaul
          REQUEST_TIMEOUT_SECONDS: "15"
          DISCOVERY_LOOKBACK_HOURS: "48"
          DISCOVERY_PAGES: "4"
          MANUAL_RUN: ${{ github.event_name == 'workflow_dispatch' }}
        run: python nightly_discovery.py

      - name: Save discovery and promotions
        shell: bash
        run: |
          if git diff --quiet -- public_sources.txt source_catalog.txt discovery_state.json; then
            echo "Discovery state did not change."
            exit 0
          fi

          git config user.name "github-actions[bot]"
          git config user.email "41898282+github-actions[bot]@users.noreply.github.com"
          git add public_sources.txt source_catalog.txt discovery_state.json
          git commit -m "Update Telegram source discovery [skip ci]"
          git pull --rebase origin "${GITHUB_REF_NAME}"
          git push origin "HEAD:${GITHUB_REF_NAME}"

      - name: Wake or restart fast monitor
        if: ${{ success() && github.event_name != 'push' }}
        env:
          GH_TOKEN: ${{ github.token }}
        run: gh workflow run monitor.yml --ref main -f continuous=true
