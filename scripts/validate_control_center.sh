#!/usr/bin/env bash
set -euo pipefail

workflow_sha="${GITHUB_SHA:-}"
release_sha="$(head -n 1 control_center_release.txt | tr -d '\r\n[:space:]')"
if [[ ! "$release_sha" =~ ^[0-9a-fA-F]{40}$ ]]; then
  echo "control_center_release.txt must contain the exact 40-character release commit SHA" >&2
  exit 1
fi
if ! git cat-file -e "${release_sha}^{commit}" 2>/dev/null; then
  echo "Release commit is not available in checkout: ${release_sha}" >&2
  exit 1
fi
git checkout --detach "$release_sha"
validated_sha="$(git rev-parse HEAD)"
if [[ "$validated_sha" != "$release_sha" ]]; then
  echo "Release SHA mismatch: expected ${release_sha}, got ${validated_sha}" >&2
  exit 1
fi
echo "Validating exact Control Center release SHA: ${validated_sha}"

python -m compileall -q bbvg
python -m py_compile \
  admin_bot.py admin_action.py admin_action_v2.py admin_action_v3.py admin_action_queue.py chapter1_stability.py admin_runtime.py \
  admin_panel_v2.py admin_panel_runtime_v41.py \
  telegram_ui.py chapter4_acceptance.py chapter5_acceptance.py wheel_lifecycle_v2.py wheel_link_lifecycle.py wheel_scenario_suite.py \
  bot_private_state.py bot_notification_state.py \
  notification_integrity_v2.py notification_router.py wheel_publications_v2.py \
  rating_policy.py chapter2_unified_logic.py privacy_retention.py security_audit.py \
  migrate_bot_private_state.py

python bot_private_state.py
python notification_integrity_v2.py --self-test
python chapter2_unified_logic.py
python bot_notification_state.py
python privacy_retention.py
python security_audit.py --current
python migrate_bot_private_state.py --check
python admin_action_v2.py --self-test
python admin_action_v3.py --self-test
python admin_action_queue.py
python -m bbvg.bot.storage
python -m bbvg.bot.users
python wheel_link_lifecycle.py
python wheel_scenario_suite.py
python -m bbvg.bot.runtime --self-test
python admin_panel_runtime_v41.py --self-test
python chapter4_acceptance.py
python chapter5_acceptance.py
python chapter1_stability.py

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
assert TelegramPanelRuntimeV41.__module__ == 'bbvg.bot.control_center'
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

if [[ -n "$workflow_sha" ]] && git cat-file -e "${workflow_sha}^{commit}" 2>/dev/null; then
  git checkout --detach "$workflow_sha"
fi
