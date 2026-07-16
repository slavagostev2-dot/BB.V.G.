#!/usr/bin/env bash
set -uo pipefail

git config user.name "github-actions[bot]"
git config user.email "41898282+github-actions[bot]@users.noreply.github.com"

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
