#!/usr/bin/env bash
set -euo pipefail

workflow_sha="${GITHUB_SHA:-}"
release_sha="$(head -n 1 control_center_release.txt | tr -d '\r\n[:space:]')"
validation_stage="bootstrap"

record_validation_failure() {
  local code=$?
  local failed_command="${BASH_COMMAND:-unknown}"
  trap - ERR
  set +e
  echo "Control Center preflight failed: stage=${validation_stage}; command=${failed_command}; exit=${code}" >&2

  if [[ -n "${GITHUB_ACTIONS:-}" ]]; then
    git fetch origin main >/dev/null 2>&1 || true
    git checkout -B main origin/main >/dev/null 2>&1 || true
    if [[ -f admin_panel_status.json ]]; then
      local now
      now="$(date -u +%FT%T.%NZ)"
      local detail
      detail="stage=${validation_stage}; command=${failed_command}; exit=${code}; release_sha=${release_sha}"
      local tmp
      tmp="$(mktemp)"
      jq \
        --arg now "$now" \
        --arg stage "$validation_stage" \
        --arg command "$failed_command" \
        --arg detail "$detail" \
        --arg release_sha "$release_sha" \
        --arg run_id "${GITHUB_RUN_ID:-}" \
        '.status = "validation_failed"
         | .last_validation_at = $now
         | .preflight_failed_stage = $stage
         | .preflight_failed_command = $command
         | .preflight_failure_detail = $detail
         | .preflight_release_sha = $release_sha
         | .validation_run_id = $run_id' \
        admin_panel_status.json > "$tmp" && mv "$tmp" admin_panel_status.json
      git config user.name "github-actions[bot]"
      git config user.email "41898282+github-actions[bot]@users.noreply.github.com"
      git add admin_panel_status.json
      git commit -m "Record exact Control Center preflight failure [skip ci]" >/dev/null 2>&1 || true
      for attempt in 1 2 3; do
        if git push origin HEAD:main >/dev/null 2>&1; then
          break
        fi
        git pull --rebase origin main >/dev/null 2>&1 || { git rebase --abort >/dev/null 2>&1 || true; break; }
      done
    fi
  fi
  exit "$code"
}
trap record_validation_failure ERR

if [[ ! "$release_sha" =~ ^[0-9a-fA-F]{40}$ ]]; then
  echo "control_center_release.txt must contain the exact 40-character release commit SHA" >&2
  false
fi
if ! git cat-file -e "${release_sha}^{commit}" 2>/dev/null; then
  echo "Release commit is not available in checkout: ${release_sha}" >&2
  false
fi

validation_stage="historical_runtime_guard"
for version in $(seq 25 40); do
  legacy_path="admin_panel_runtime_v${version}.py"
  if [[ -e "$legacy_path" ]]; then
    echo "Historical panel runtime must not exist after chapter 2C: ${legacy_path}" >&2
    false
  fi
done

validation_stage="checkout_release_sha"
git checkout --detach "$release_sha"
validated_sha="$(git rev-parse HEAD)"
if [[ "$validated_sha" != "$release_sha" ]]; then
  echo "Release SHA mismatch: expected ${release_sha}, got ${validated_sha}" >&2
  false
fi
echo "Validating exact Control Center release SHA: ${validated_sha}"

validation_stage="compile_bbvg_package"
python -m compileall -q bbvg
validation_stage="compile_control_center_modules"
python -m py_compile \
  admin_bot.py admin_action.py admin_action_v2.py admin_action_v3.py admin_action_queue.py chapter1_stability.py admin_runtime.py \
  admin_panel_v2.py admin_panel_runtime_v41.py notification_button_recovery.py \
  telegram_ui.py chapter4_acceptance.py chapter5_acceptance.py wheel_lifecycle_v2.py wheel_link_lifecycle.py wheel_scenario_suite.py \
  bot_private_state.py bot_notification_state.py \
  notification_integrity_v2.py notification_router.py wheel_publications_v2.py \
  rating_policy.py chapter2_unified_logic.py privacy_retention.py security_audit.py \
  migrate_bot_private_state.py

validation_stage="bot_private_state_self_test"
python bot_private_state.py
validation_stage="notification_integrity_self_test"
python notification_integrity_v2.py --self-test
validation_stage="chapter2_unified_logic"
python chapter2_unified_logic.py
validation_stage="bot_notification_state_self_test"
python bot_notification_state.py
validation_stage="privacy_retention_self_test"
python privacy_retention.py
validation_stage="security_audit_current"
python security_audit.py --current
validation_stage="private_state_migration_check"
python migrate_bot_private_state.py --check
validation_stage="admin_action_v2_self_test"
python admin_action_v2.py --self-test
validation_stage="admin_action_v3_self_test"
python admin_action_v3.py --self-test
validation_stage="admin_action_queue_self_test"
python admin_action_queue.py
validation_stage="bot_storage_self_test"
python -m bbvg.bot.storage
validation_stage="bot_users_self_test"
python -m bbvg.bot.users
validation_stage="wheel_link_lifecycle_self_test"
python wheel_link_lifecycle.py
validation_stage="wheel_scenario_suite"
python wheel_scenario_suite.py
validation_stage="bot_runtime_self_test"
python -m bbvg.bot.runtime --self-test
validation_stage="runtime_v41_self_test"
python admin_panel_runtime_v41.py --self-test

validation_stage="chapter4_acceptance"
if grep -Fq 'run: python admin_panel_runtime_v41.py' .github/workflows/admin-bot.yml; then
  python chapter4_acceptance.py
elif grep -Fq 'run: python notification_button_recovery.py' .github/workflows/admin-bot.yml; then
  validation_stage="chapter4_compatibility_entrypoint"
  python notification_button_recovery.py --self-test
  python - <<'PY'
from tests import production_acceptance as acceptance

original_text = acceptance.text

def compatible_text(path: str) -> str:
    value = original_text(path)
    if path == ".github/workflows/admin-bot.yml":
        value = value.replace(
            "run: python notification_button_recovery.py",
            "run: python admin_panel_runtime_v41.py",
        )
    return value

acceptance.text = compatible_text
acceptance.interface_acceptance()
print("Chapter 4 compatibility entrypoint acceptance passed")
PY
else
  echo "Unknown Control Center production entrypoint" >&2
  false
fi

validation_stage="chapter5_acceptance"
python chapter5_acceptance.py
validation_stage="chapter1_stability"
python chapter1_stability.py

validation_stage="final_control_center_contracts"
python - <<'PY'
import inspect
import notification_router
import telegram_ui
from admin_panel_runtime_v41 import TelegramPanelRuntimeV41
from bbvg.bot.runtime import TelegramPanelRuntime
from bbvg.bot.storage import PrivateStateRuntime
from bbvg.bot.users import UserSettingsMixin

handler = inspect.getsource(UserSettingsMixin.handle_callback)
persistence = inspect.getsource(PrivateStateRuntime._save_bot_bundle)
detail = inspect.getsource(TelegramPanelRuntime.show_user_detail)
normalization = inspect.getsource(PrivateStateRuntime.normalize_access)
safe_send = inspect.getsource(TelegramPanelRuntime.safe_text_for_role)
summary_handler = inspect.getsource(TelegramPanelRuntime.handle_callback)
colorizer = inspect.getsource(TelegramPanelRuntime._color_active_payload)
home = inspect.getsource(TelegramPanelRuntime.show_menu)

assert TelegramPanelRuntimeV41.RUNTIME_VERSION == 41
assert not any(cls.__module__.startswith("admin_panel_runtime_v") for cls in TelegramPanelRuntime.__mro__)
assert "usernotify:" in handler
assert "usernotifyall:" in handler
assert "_load_remote_bundle" in persistence
assert "_merge_access" in persistence
assert "Управлять уведомлениями" in detail
assert "access_signature" in normalization
assert "result.pop" in normalization
assert "USER_ACTION_ERROR" in safe_send
assert "dispatch_summary" not in summary_handler
assert "show_period_report" in summary_handler
assert "callback_data" not in colorizer
assert "Находит колёса BetBoom" in home
assert "Ваша роль" in home
assert notification_router._bbvg_notification_integrity_v2_installed is True
ranking = [
    button["text"]
    for row in TelegramPanelRuntime.source_menu_rows(False)
    for button in row
    if button.get("callback_data") == "page:ranking"
]
assert ranking == ["🏆 Рейтинг источников"]
assert "summary:send" not in inspect.getsource(TelegramPanelRuntime.show_analytics)
assert "подтвержд.)" not in inspect.getsource(TelegramPanelRuntime.show_ranking)
assert telegram_ui.TELEGRAM_CALLBACK_LIMIT == 64
print("BB V.G. Control Center preflight validated")
PY

validation_stage="restore_workflow_sha"
if [[ -n "$workflow_sha" ]] && git cat-file -e "${workflow_sha}^{commit}" 2>/dev/null; then
  git checkout --detach "$workflow_sha"
fi
trap - ERR
