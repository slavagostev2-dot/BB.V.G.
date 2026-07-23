#!/usr/bin/env bash
set -euo pipefail

release_sha="${CONTROL_CENTER_RELEASE_SHA:-}"
if [[ -z "$release_sha" ]]; then
  release_sha="$(head -n 1 control_center_release.txt | tr -d '\r\n[:space:]')"
fi
validation_stage="bootstrap"

record_validation_failure() {
  local code=$?
  local failed_command="${BASH_COMMAND:-unknown}"
  trap - ERR
  echo "Control Center preflight failed: stage=${validation_stage}; command=${failed_command}; exit=${code}" >&2
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
for version in $(seq 2 40); do
  legacy_path="admin_panel_runtime_v${version}.py"
  if [[ -e "$legacy_path" ]]; then
    echo "Historical panel runtime must not exist: ${legacy_path}" >&2
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
  admin_bot.py admin_action.py admin_action_v2.py admin_action_v3.py admin_action_queue.py admin_runtime.py \
  admin_panel_v2.py admin_panel_runtime_v41.py notification_button_recovery.py \
  telegram_ui.py wheel_lifecycle_v2.py wheel_link_lifecycle.py wheel_scenario_suite.py \
  bot_private_state.py bot_notification_state.py \
  notification_integrity_v2.py notification_router.py wheel_publications_v2.py \
  rating_policy.py privacy_retention.py security_audit.py \
  migrate_bot_private_state.py

validation_stage="bot_private_state_self_test"
python bot_private_state.py
validation_stage="notification_integrity_self_test"
python notification_integrity_v2.py --self-test
validation_stage="production_acceptance_unified"
python tests/production_acceptance.py --section unified
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

validation_stage="production_entrypoint"
if ! grep -Fq 'run: python notification_button_recovery.py' .github/workflows/admin-bot.yml; then
  echo "Unknown Control Center production entrypoint" >&2
  false
fi
python notification_button_recovery.py --self-test
validation_stage="production_acceptance_interface"
python tests/production_acceptance.py --section interface

validation_stage="production_acceptance_lifecycle"
python tests/production_acceptance.py --section lifecycle
validation_stage="production_acceptance_stability"
python tests/production_acceptance.py --section stability

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
trap - ERR
