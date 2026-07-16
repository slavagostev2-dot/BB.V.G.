# BB V.G. — карта методов исторического runtime панели

Файл генерируется автоматически из AST и фактической MRO текущего runtime.
Исходный коммит аудита: `73be9a0d8be86bf63643b47b89c2b81aeb9608c6`.

- Runtime-файлов: **19**
- В текущей цепочке: **19**
- Суммарно строк: **5896**
- Уникальных имён методов: **100**

## Фактическая MRO текущей панели

1. `bbvg.bot.runtime.TelegramPanelRuntime`
2. `admin_panel_runtime_v38.TelegramPanelRuntimeV38`
3. `admin_panel_runtime_v37.TelegramPanelRuntimeV37`
4. `admin_panel_runtime_v36.TelegramPanelRuntimeV36`
5. `bbvg.bot.users.UserSettingsMixin`
6. `admin_panel_runtime_v32.TelegramPanelRuntimeV32`
7. `admin_panel_runtime_v31.TelegramPanelRuntimeV31`
8. `admin_panel_runtime_v30.TelegramPanelRuntimeV30`
9. `admin_panel_runtime_v29.TelegramPanelRuntimeV29`
10. `admin_panel_runtime_v28.TelegramPanelRuntimeV28`
11. `admin_panel_runtime_v26.TelegramPanelRuntimeV26`
12. `admin_panel_runtime_v25.TelegramPanelRuntimeV25`
13. `bbvg.bot.storage.PrivateStateRuntime`
14. `bbvg.bot.sources.SourceRegistryRuntime`
15. `bbvg.bot.users.UserManagementRuntime`
16. `bbvg.bot.wheels.WheelInteractionRuntime`
17. `bbvg.bot.source_requests.SourceRequestRuntime`
18. `bbvg.bot.interface.PanelInterfaceRuntime`
19. `bbvg.bot.foundation.PanelFoundationMixin`
20. `admin_panel_runtime_v9.TelegramPanelRuntimeV9`
21. `admin_panel_runtime_v8.TelegramPanelRuntimeV8`
22. `admin_panel_runtime_v7.TelegramPanelRuntimeV7`
23. `admin_panel_runtime_v6.TelegramPanelRuntimeV6`
24. `admin_panel_runtime_v5.TelegramPanelRuntimeV5`
25. `admin_panel_runtime_v4.TelegramPanelRuntimeV4`
26. `admin_panel_runtime_v3.TelegramPanelRuntimeV3`
27. `admin_panel_runtime_v2.TelegramPanelRuntimeV2`
28. `admin_panel_v2.TelegramPanelV2`
29. `admin_runtime.RuntimeAdminBot`
30. `admin_bot.AdminBot`

## Владельцы фактически действующих методов

### `bbvg.bot.runtime.TelegramPanelRuntime` — 3 методов

`handle_callback()`, `show_active()`, `show_menu()`

### `admin_panel_runtime_v38.TelegramPanelRuntimeV38` — 24 методов

`_display_user()`, `_normalize_page()`, `_page_family()`, `_period_buttons()`, `_resolve_wheel_token()`, `_set_quick_time()`, `_wheel_digest()`, `compact_menu_rows()`, `handle_callback()`, `open_page()`, `period_overview()`, `render_page()`, `request_manual_time()`, `send()`, `show_access()`, `show_active()`, `show_analytics()`, `show_inactive_report()`, `show_period_report()`, `show_ranking()`, `show_recipients()`, `show_reports()`, `show_stats()`, `show_status()`

### `admin_panel_runtime_v37.TelegramPanelRuntimeV37` — 29 методов

`_apply_notification_policy_once()`, `_monitor_status()`, `_notification_options_for_role()`, `_remove_summary_preferences()`, `_set_quick_time()`, `dispatch_admin_action()`, `handle_callback()`, `handle_message()`, `notification_preferences()`, `parse_manual_deadline()`, `record_runtime_heartbeat()`, `register_user()`, `render_page()`, `request_manual_time()`, `set_admin()`, `setup_bot()`, `show_active()`, `show_analytics()`, `show_discovery()`, `show_intelligence()`, `show_menu()`, `show_notifications()`, `show_period_report()`, `show_reports()`, `show_sources()`, `show_stats()`, `show_status()`, `toggle_notification()`, `transfer_owner()`

### `admin_panel_runtime_v36.TelegramPanelRuntimeV36` — 5 методов

`safe_text_for_role()`, `send()`, `setup_bot()`, `show_ranking()`, `source_menu_rows()`

### `bbvg.bot.users.UserSettingsMixin` — 14 методов

`_notification_options_for_role()`, `_save_user_preferences()`, `delete_current_user_data()`, `handle_callback()`, `notification_preferences()`, `register_user()`, `render_page()`, `set_all_user_notifications()`, `set_user_notification()`, `show_notifications()`, `show_settings()`, `show_user_detail()`, `show_user_notifications()`, `toggle_notification()`

### `admin_panel_runtime_v32.TelegramPanelRuntimeV32` — 11 методов

`_collect_current_wheels()`, `_sources_for_item()`, `compact_menu_rows()`, `handle_callback()`, `render_page()`, `setup_bot()`, `show_active()`, `show_analytics()`, `show_period_report()`, `show_reports()`, `show_stats()`

### `admin_panel_runtime_v31.TelegramPanelRuntimeV31` — 17 методов

`analytics_menu_rows()`, `control_menu_rows()`, `dispatch_summary()`, `handle_callback()`, `notification_preferences()`, `period_overview()`, `period_title()`, `render_page()`, `setup_bot()`, `show_analytics()`, `show_control()`, `show_inactive_report()`, `show_period_report()`, `show_reports()`, `show_send_summary_menu()`, `show_stats()`, `summary_send_rows()`

### `admin_panel_runtime_v30.TelegramPanelRuntimeV30` — 14 методов

`analytics_menu_rows()`, `begin_source_request()`, `handle_callback()`, `handle_message()`, `notification_preferences()`, `ranked_sources()`, `register_user()`, `render_page()`, `show_analytics()`, `show_notifications()`, `show_ranking()`, `show_reports()`, `source_menu_rows()`, `toggle_notification()`

### `admin_panel_runtime_v29.TelegramPanelRuntimeV29` — 11 методов

`compact_menu_rows()`, `control_menu_rows()`, `handle_callback()`, `render_page()`, `show_analytics()`, `show_control()`, `show_reports()`, `show_source_request_help()`, `show_sources()`, `show_stats()`, `source_menu_rows()`

### `admin_panel_runtime_v28.TelegramPanelRuntimeV28` — 1 методов

`_apply_admin_action_direct()`

### `admin_panel_runtime_v26.TelegramPanelRuntimeV26` — 7 методов

`_apply_admin_action_direct()`, `_prepare_callback_user()`, `_read_json_at()`, `_serialize_json()`, `compact_menu_rows()`, `dispatch_admin_action()`, `handle_callback()`

### `admin_panel_runtime_v25.TelegramPanelRuntimeV25` — 6 методов

`compact_menu_rows()`, `handle_callback()`, `render_page()`, `show_active()`, `show_ranking()`, `show_sources()`

### `bbvg.bot.storage.PrivateStateRuntime` — 12 методов

`_bootstrap_access()`, `_load_bot_bundle()`, `_load_remote_bundle()`, `_merge_access()`, `_normalize_bundle()`, `_save_bot_bundle()`, `_write_remote_bundle()`, `load_access()`, `load_source_requests()`, `normalize_access()`, `save_access()`, `save_source_requests()`

### `bbvg.bot.sources.SourceRegistryRuntime` — 6 методов

`load_source_registry()`, `miniapp_url_for_chat()`, `show_app_entry()`, `source_mode_name()`, `source_registry()`, `source_registry_fallback()`

### `bbvg.bot.users.UserManagementRuntime` — 18 методов

`_sync_recipient()`, `compact_menu_rows()`, `handle_callback()`, `handle_message()`, `handle_update()`, `miniapp_url_for_chat()`, `notification_preferences()`, `notify_owner_about_new_user()`, `register_user()`, `render_page()`, `set_admin()`, `show_access()`, `show_notifications()`, `show_recipients()`, `show_settings()`, `show_user_detail()`, `toggle_notification()`, `transfer_owner()`

### `bbvg.bot.wheels.WheelInteractionRuntime` — 14 методов

`_collect_current_wheels()`, `_delete_callback_message()`, `_hidden_wheels()`, `_joined_wheel_keys()`, `_personal_participating_wheels()`, `handle_callback()`, `handle_message()`, `hide_wheel_for_current_user()`, `mark_personal_participation()`, `parse_manual_deadline()`, `render_page()`, `request_manual_time()`, `show_active()`, `show_stats()`

### `bbvg.bot.source_requests.SourceRequestRuntime` — 15 методов

`bot_username()`, `can_moderate_source_requests()`, `decide_source_request()`, `handle_callback()`, `handle_message()`, `inspect_source()`, `load_source_requests()`, `miniapp_url_for_chat()`, `moderator_chat_ids()`, `notify_moderators()`, `request_id()`, `requester_name()`, `save_source_requests()`, `show_app_entry()`, `submit_source_request()`

### `bbvg.bot.interface.PanelInterfaceRuntime` — 20 методов

`_hide_reply_keyboard()`, `_telegram_error_text()`, `_write_source_list()`, `bulk_intelligence_rows()`, `bulk_set_intelligence_mode()`, `compact_menu_rows()`, `handle_callback()`, `handle_message()`, `pending_reason()`, `pending_rows()`, `render_page()`, `send()`, `show_active()`, `show_intelligence_list()`, `show_menu()`, `show_more()`, `show_pending()`, `show_source_detail()`, `show_stats()`, `source_mode_name()`

### `bbvg.bot.foundation.PanelFoundationMixin` — 13 методов

`_callback_page()`, `bot_username()`, `handle_callback()`, `handle_message()`, `intelligence_launch_text()`, `miniapp_deployment()`, `miniapp_url_for_chat()`, `nav_rows()`, `setup_bot()`, `show_app_entry()`, `show_candidate_list()`, `show_discovery()`, `with_nav()`

### `admin_panel_runtime_v9.TelegramPanelRuntimeV9` — 6 методов

`handle_message()`, `render_page()`, `show_active()`, `show_app_entry()`, `show_menu()`, `show_settings()`

### `admin_panel_runtime_v8.TelegramPanelRuntimeV8` — 3 методов

`handle_callback()`, `show_reports()`, `show_sources()`

### `admin_panel_runtime_v7.TelegramPanelRuntimeV7` — 3 методов

`_collect_current_wheels()`, `handle_message()`, `show_menu()`

### `admin_panel_runtime_v6.TelegramPanelRuntimeV6` — 11 методов

`filtered_intelligence_rows()`, `handle_callback()`, `handle_message()`, `intelligence_label()`, `intelligence_rows()`, `intelligence_state()`, `render_page()`, `show_intelligence()`, `show_intelligence_detail()`, `show_intelligence_list()`, `show_menu()`

### `admin_panel_runtime_v5.TelegramPanelRuntimeV5` — 16 методов

`_candidate_filter()`, `_recent_candidate_wheels()`, `candidate_rows()`, `candidate_score()`, `handle_callback()`, `ignore_candidate()`, `load_moderation()`, `recommendation()`, `render_page()`, `restore_candidate()`, `save_moderation()`, `score_label()`, `set_candidate_mode()`, `show_candidate_detail()`, `show_candidate_list()`, `show_discovery()`

### `admin_panel_runtime_v4.TelegramPanelRuntimeV4` — 6 методов

`_collect_current_wheels()`, `_entry_key()`, `_inspect_entry()`, `_restore_telegram_deadline()`, `show_active()`, `show_menu()`

### `admin_panel_runtime_v3.TelegramPanelRuntimeV3` — 12 методов

`_security_payload()`, `_trusted_owner()`, `handle_callback()`, `normalize_access()`, `render_page()`, `set_interval()`, `show_active()`, `show_discovery()`, `show_interval()`, `show_reports()`, `show_settings()`, `show_sources()`

### `admin_panel_runtime_v2.TelegramPanelRuntimeV2` — 1 методов

`register_user()`

### `admin_panel_v2.TelegramPanelV2` — 59 методов

`_direct_get_file()`, `_json_text()`, `active_rows()`, `back()`, `bool_mark()`, `can_view()`, `diagnose_input()`, `handle_callback()`, `handle_message()`, `is_admin()`, `is_owner()`, `load_access()`, `nav_rows()`, `normalize_access()`, `open_page()`, `private_chat()`, `record_runtime_heartbeat()`, `refresh_loop()`, `refresh_snapshot()`, `register_user()`, `remaining()`, `render_page()`, `request_add_source()`, `role_for()`, `role_name()`, `run()`, `save_access()`, `send()`, `set_admin()`, `set_context()`, `setup_bot()`, `show_access()`, `show_active()`, `show_control()`, `show_diagnostic()`, `show_discovery()`, `show_errors_report()`, `show_inactive_report()`, `show_menu()`, `show_period_report()`, `show_ranking()`, `show_recipients()`, `show_reports()`, `show_settings()`, `show_source_detail()`, `show_source_list()`, `show_sources()`, `show_stats()`, `show_status()`, `show_user_detail()`, `snapshot()`, `source_mode_name()`, `source_sets()`, `source_status_name()`, `stack()`, `toggle_recipient()`, `toggle_setting()`, `transfer_owner()`, `with_nav()`

### `admin_runtime.RuntimeAdminBot` — 3 методов

`handle_callback()`, `set_source_mode()`, `verify_public_source()`

### `admin_bot.AdminBot` — 47 методов

`active_rows()`, `answer()`, `append_to_list_text()`, `authorized()`, `counter()`, `dispatch()`, `dispatch_admin_action()`, `get_file()`, `get_json_file()`, `gh_headers()`, `gh_request()`, `handle_callback()`, `handle_message()`, `handle_update()`, `merged_source_stats()`, `monitor_state_text()`, `parse_dt()`, `parse_list()`, `period_totals()`, `remaining()`, `remove_from_list_text()`, `run()`, `safe_source()`, `send()`, `set_source_mode()`, `setup_bot()`, `show_active()`, `show_control()`, `show_discovery()`, `show_errors_report()`, `show_inactive_report()`, `show_menu()`, `show_period_report()`, `show_ranking()`, `show_reports()`, `show_settings()`, `show_source_detail()`, `show_source_list()`, `show_sources()`, `show_stats()`, `show_status()`, `snapshot()`, `source_sets()`, `telegram_api()`, `update_file()`, `verify_public_source()`, `workflow_run()`
