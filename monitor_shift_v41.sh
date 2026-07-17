#!/usr/bin/env bash
set -uo pipefail

git config user.name "github-actions[bot]"
git config user.email "41898282+github-actions[bot]@users.noreply.github.com"

# Public source ranking starts from zero on 2026-07-17 (Asia/Barnaul).
# Keep the epoch exported for every monitor iteration so monitor_data does not
# restore the previous 2026-07-14 epoch in memory.
export SOURCE_RATING_EPOCH_DAY="${SOURCE_RATING_EPOCH_DAY:-2026-07-17}"

python - <<'PY'
from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

path = Path("source_stats.json")
reset_day = str(os.environ.get("SOURCE_RATING_EPOCH_DAY") or "2026-07-17")
reset_version = 2
rating_fields = {
    "wheel_posts",
    "admin_confirmed_wheels",
    "admin_rejected_wheels",
    "quality_score",
    "quality_decisions",
    "activation_sent",
    "personal_vote_points",
    "personal_vote_score",
    "personal_votes",
    "user_votes",
    "admin_votes",
    "last_vote_at",
}

try:
    data = json.loads(path.read_text(encoding="utf-8"))
except (FileNotFoundError, json.JSONDecodeError, OSError):
    data = {"version": 1, "sources": {}, "daily": {}}

if not isinstance(data, dict):
    data = {"version": 1, "sources": {}, "daily": {}}

already_reset = (
    data.get("source_rating_epoch_day") == reset_day
    and int(data.get("source_rating_reset_version", 0) or 0) >= reset_version
)

if not already_reset:
    data.pop("admin_wheel_decisions", None)
    data.pop("personal_wheel_votes", None)

    sources = data.setdefault("sources", {})
    if isinstance(sources, dict):
        for entry in sources.values():
            if not isinstance(entry, dict):
                continue
            for field in rating_fields:
                entry.pop(field, None)

    daily = data.setdefault("daily", {})
    if isinstance(daily, dict):
        for daily_entry in daily.values():
            if not isinstance(daily_entry, dict):
                continue
            totals = daily_entry.setdefault("totals", {})
            if isinstance(totals, dict):
                for field in rating_fields:
                    totals.pop(field, None)
            source_rows = daily_entry.setdefault("sources", {})
            if isinstance(source_rows, dict):
                for entry in source_rows.values():
                    if not isinstance(entry, dict):
                        continue
                    for field in rating_fields:
                        entry.pop(field, None)

    data["source_rating_policy"] = "personal_votes_v1"
    data["source_rating_epoch_day"] = reset_day
    data["source_rating_reset_version"] = reset_version
    data["source_rating_reset_at"] = datetime.now(
        ZoneInfo("Asia/Barnaul")
    ).isoformat()
    data["source_rating_counting_from"] = f"{reset_day}T00:00:00+07:00"

    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(
        json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temp.replace(path)
    print(f"Source rating reset completed for epoch {reset_day}.")
else:
    print(f"Source rating epoch {reset_day} is already active.")
PY

runtime_files=(
  state.json source_health.json source_stats.json
  unknown_timer_samples.json monitor_status.json
  notification_delivery_state.json
)

push_runtime() {
  if git diff --quiet -- "${runtime_files[@]}"; then
    return 0
  fi

  git add "${runtime_files[@]}"
  git commit -m "Update BB V.G. runtime data [skip ci]" || true

  for attempt in 1 2 3; do
    if git push origin "HEAD:${GITHUB_REF_NAME:-main}"; then
      return 0
    fi
    echo "Runtime push attempt ${attempt} failed; rebasing before retry."
    if ! git pull --rebase origin "${GITHUB_REF_NAME:-main}"; then
      git rebase --abort || true
      echo "Runtime rebase failed; preserving local data for the next retry."
      return 1
    fi
  done
  return 1
}

python monitor_health.py start --run-id "${GITHUB_RUN_ID:-}"

shift_end=$(( $(date +%s) + 19800 ))
last_commit_at=0
iteration=0

while true; do
  iteration=$((iteration + 1))
  started_at=$(date +%s)
  admin_action_before=$(python - <<'PY'
import json
try:
    value = json.load(open("state.json", encoding="utf-8"))
    print(str(value.get("last_admin_action_applied_at") or ""))
except Exception:
    print("")
PY
  )
  echo "=== BB V.G. check $iteration at $(date -u +%FT%TZ) ==="

  timeout --signal=TERM --kill-after=30s 600s python bbvg_monitor_main.py 2>&1 | tee monitor-run.log
  iteration_exit=${PIPESTATUS[0]}
  duration=$(( $(date +%s) - started_at ))

  python monitor_health.py record \
    --run-id "${GITHUB_RUN_ID:-}" \
    --iteration "$iteration" \
    --exit-code "$iteration_exit" \
    --duration-seconds "$duration"

  if (( iteration_exit != 0 )); then
    echo "BB V.G. iteration failed with exit code ${iteration_exit}; status was saved."
  fi

  restart_required=$(python - <<'PY'
import json
try:
    value = json.load(open("monitor_status.json", encoding="utf-8"))
except Exception:
    value = {}
print("true" if value.get("restart_recommended") else "false")
PY
  )
  if [[ "$restart_required" == "true" ]]; then
    echo "Repeated failures or missing source progress detected; ending this shift cleanly."
    push_runtime || true
    break
  fi

  now_ts=$(date +%s)
  delivery_changed=false
  if ! git diff --quiet -- notification_delivery_state.json; then
    delivery_changed=true
  fi
  admin_action_after=$(python - <<'PY'
import json
try:
    value = json.load(open("state.json", encoding="utf-8"))
    print(str(value.get("last_admin_action_applied_at") or ""))
except Exception:
    print("")
PY
  )
  admin_action_applied=false
  if [[ -n "$admin_action_after" && "$admin_action_after" != "$admin_action_before" ]]; then
    admin_action_applied=true
  fi

  if [[ "$delivery_changed" == "true" || "$admin_action_applied" == "true" || "${CONTINUOUS:-false}" != "true" ]] || (( last_commit_at == 0 || now_ts - last_commit_at >= 900 || now_ts >= shift_end )); then
    push_runtime || echo "Runtime push is deferred; watchdog will continue checking repository freshness."
    last_commit_at=$now_ts
  fi

  if [[ "${CONTINUOUS:-false}" != "true" ]] || (( now_ts >= shift_end )); then
    break
  fi

  interval_seconds=$(python - <<'PY'
import bot_notification_state
try:
    data, _ = bot_notification_state.load_config()
    value = int(data.get("settings", {}).get("monitor_interval_minutes", 5))
except Exception:
    value = 5
value = value if value in {1, 3, 5, 10, 15, 30} else 5
print(value * 60)
PY
  )

  sleep_for=$(( interval_seconds - duration ))
  if (( sleep_for > 0 )); then
    echo "Previous check finished in ${duration}s. Next check in ${sleep_for}s."
    sleep "$sleep_for"
  else
    echo "Previous check took ${duration}s; configured interval already elapsed."
  fi
done

push_runtime || true
