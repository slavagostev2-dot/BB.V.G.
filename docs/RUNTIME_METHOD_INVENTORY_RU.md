# BB V.G. — карта методов исторического runtime панели

Файл генерируется автоматически из AST и фактической MRO текущего runtime.
Исходный коммит аудита: `378adc8fd979a1287a41f5b19df9b7161ce38d1f`.

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

Метод учитывается у первого класса MRO, который его определяет.

### `bbvg.bot.runtime.TelegramPanelRuntime` — 5 методов

`_color_active_payload()`, `_simplify_active_payload()`, `handle_callback()`, `show_active()`, `show_menu()`

### `admin_panel_runtime_v38.TelegramPanelRuntimeV38` — 25 методов

`_display_user()`, `_normalize_page()`, `_page_family()`, `_period_buttons()`, `_quick_time_callback()`, `_resolve_wheel_token()`, `_set_quick_time()`, `_wheel_callback()`, `_wheel_digest()`, `_wheel_token()`, `compact_menu_rows()`, `open_page()`, `period_overview()`, `render_page()`, `request_manual_time()`, `send()`, `show_access()`, `show_analytics()`, `show_inactive_report()`, `show_period_report()`, `show_ranking()`, `show_recipients()`, `show_reports()`, `show_stats()`, `show_status()`

### `admin_panel_runtime_v37.TelegramPanelRuntimeV37` — 18 методов

`_apply_notification_policy_once()`, `_monitor_status()`, `_notification_options_for_role()`, `_remove_summary_preferences()`, `dispatch_admin_action()`, `handle_message()`, `notification_preferences()`, `parse_manual_deadline()`, `record_runtime_heartbeat()`, `register_user()`, `set_admin()`, `setup_bot()`, `show_discovery()`, `show_intelligence()`, `show_notifications()`, `show_sources()`, `toggle_notification()`, `transfer_owner()`

### `admin_panel_runtime_v36.TelegramPanelRuntimeV36` — 2 методов

`safe_text_for_role()`, `source_menu_rows()`

### `bbvg.bot.users.UserSettingsMixin` — 7 методов

`_save_user_preferences()`, `delete_current_user_data()`, `set_all_user_notifications()`, `set_user_notification()`, `show_settings()`, `show_user_detail()`, `show_user_notifications()`

### `admin_panel_runtime_v32.TelegramPanelRuntimeV32` — 2 методов

`_collect_current_wheels()`, `_sources_for_item()`

### `admin_panel_runtime_v31.TelegramPanelRuntimeV31` — 7 методов

`analytics_menu_rows()`, `control_menu_rows()`, `dispatch_summary()`, `period_title()`, `show_control()`, `show_send_summary_menu()`, `summary_send_rows()`

### `admin_panel_runtime_v30.TelegramPanelRuntimeV30` — 2 методов

`begin_source_request()`, `ranked_sources()`

### `admin_panel_runtime_v29.TelegramPanelRuntimeV29` — 1 методов

`show_source_request_help()`

### `admin_panel_runtime_v28.TelegramPanelRuntimeV28` — 1 методов

`_apply_admin_action_direct()`

### `admin_panel_runtime_v26.TelegramPanelRuntimeV26` — 3 методов

`_prepare_callback_user()`, `_read_json_at()`, `_serialize_json()`

### `bbvg.bot.storage.PrivateStateRuntime` — 12 методов

`_bootstrap_access()`, `_load_bot_bundle()`, `_load_remote_bundle()`, `_merge_access()`, `_normalize_bundle()`, `_save_bot_bundle()`, `_write_remote_bundle()`, `load_access()`, `load_source_requests()`, `normalize_access()`, `save_access()`, `save_source_requests()`

### `bbvg.bot.sources.SourceRegistryRuntime` — 6 методов

`load_source_registry()`, `miniapp_url_for_chat()`, `show_app_entry()`, `source_mode_name()`, `source_registry()`, `source_registry_fallback()`

### `bbvg.bot.users.UserManagementRuntime` — 3 методов

`_sync_recipient()`, `handle_update()`, `notify_owner_about_new_user()`

### `bbvg.bot.wheels.WheelInteractionRuntime` — 6 методов

`_delete_callback_message()`, `_hidden_wheels()`, `_joined_wheel_keys()`, `_personal_participating_wheels()`, `hide_wheel_for_current_user()`, `mark_personal_participation()`

### `bbvg.bot.source_requests.SourceRequestRuntime` — 9 методов

`bot_username()`, `can_moderate_source_requests()`, `decide_source_request()`, `inspect_source()`, `moderator_chat_ids()`, `notify_moderators()`, `request_id()`, `requester_name()`, `submit_source_request()`

### `bbvg.bot.interface.PanelInterfaceRuntime` — 11 методов

`_hide_reply_keyboard()`, `_telegram_error_text()`, `_write_source_list()`, `bulk_intelligence_rows()`, `bulk_set_intelligence_mode()`, `pending_reason()`, `pending_rows()`, `show_intelligence_list()`, `show_more()`, `show_pending()`, `show_source_detail()`

### `bbvg.bot.foundation.PanelFoundationMixin` — 6 методов

`_callback_page()`, `intelligence_launch_text()`, `miniapp_deployment()`, `nav_rows()`, `show_candidate_list()`, `with_nav()`

### `admin_panel_runtime_v6.TelegramPanelRuntimeV6` — 5 методов

`filtered_intelligence_rows()`, `intelligence_label()`, `intelligence_rows()`, `intelligence_state()`, `show_intelligence_detail()`

### `admin_panel_runtime_v5.TelegramPanelRuntimeV5` — 12 методов

`_candidate_filter()`, `_recent_candidate_wheels()`, `candidate_rows()`, `candidate_score()`, `ignore_candidate()`, `load_moderation()`, `recommendation()`, `restore_candidate()`, `save_moderation()`, `score_label()`, `set_candidate_mode()`, `show_candidate_detail()`

### `admin_panel_runtime_v4.TelegramPanelRuntimeV4` — 3 методов

`_entry_key()`, `_inspect_entry()`, `_restore_telegram_deadline()`

### `admin_panel_runtime_v3.TelegramPanelRuntimeV3` — 5 методов

`_security_payload()`, `_signature()`, `_trusted_owner()`, `set_interval()`, `show_interval()`

### `admin_panel_v2.TelegramPanelV2` — 27 методов

`_direct_get_file()`, `_json_text()`, `active_rows()`, `back()`, `bool_mark()`, `can_view()`, `diagnose_input()`, `is_admin()`, `is_owner()`, `private_chat()`, `refresh_loop()`, `refresh_snapshot()`, `remaining()`, `request_add_source()`, `role_for()`, `role_name()`, `run()`, `set_context()`, `show_diagnostic()`, `show_errors_report()`, `show_source_list()`, `snapshot()`, `source_sets()`, `source_status_name()`, `stack()`, `toggle_recipient()`, `toggle_setting()`

### `admin_runtime.RuntimeAdminBot` — 2 методов

`set_source_mode()`, `verify_public_source()`

### `admin_bot.AdminBot` — 21 методов

`age_text()`, `answer()`, `append_to_list_text()`, `authorized()`, `counter()`, `dispatch()`, `fmt_dt()`, `get_file()`, `get_json_file()`, `gh_headers()`, `gh_request()`, `merged_source_stats()`, `monitor_state_text()`, `parse_dt()`, `parse_list()`, `period_totals()`, `remove_from_list_text()`, `safe_source()`, `telegram_api()`, `update_file()`, `workflow_run()`

## Файлы и определённые методы

### `admin_panel_runtime_v2.py` — 140 строк, в рабочей цепочке

Прямые импорты: `admin_bot`, `admin_panel_v2`

Класс `TelegramPanelRuntimeV2`; база: `admin_panel_v2.TelegramPanelV2`.

- `register_user()` — 72 строк

### `admin_panel_runtime_v25.py` — 271 строк, в рабочей цепочке

Прямые импорты: `bbvg.bot.source_requests`, `bbvg.bot.storage`, `bbvg.bot.users`, `bot_private_state`

Класс `TelegramPanelRuntimeV25`; база: `PrivateStateRuntime`.

- `compact_menu_rows()` — 23 строк
- `show_sources()` — 42 строк
- `show_ranking()` — 36 строк
- `show_active()` — 58 строк
- `handle_callback()` — 12 строк
- `render_page()` — 21 строк

### `admin_panel_runtime_v26.py` — 321 строк, в рабочей цепочке

Прямые импорты: `admin_action_v2`, `admin_bot`, `admin_panel_runtime_v25`, `bbvg.bot.source_requests`, `bbvg.bot.sources`, `bot_private_state`

Класс `TelegramPanelRuntimeV26`; база: `TelegramPanelRuntimeV25`.

- `compact_menu_rows()` — 11 строк
- `_read_json_at()` — 18 строк
- `_serialize_json()` — 3 строк
- `_apply_admin_action_direct()` — 103 строк
- `dispatch_admin_action()` — 13 строк
- `_prepare_callback_user()` — 13 строк
- `handle_callback()` — 53 строк

### `admin_panel_runtime_v28.py` — 85 строк, в рабочей цепочке

Прямые импорты: `admin_action_v2`, `admin_panel_runtime_v26`

Класс `TelegramPanelRuntimeV28`; база: `TelegramPanelRuntimeV26`.

- `_apply_admin_action_direct()` — 52 строк

### `admin_panel_runtime_v29.py` — 275 строк, в рабочей цепочке

Прямые импорты: `admin_panel_runtime_v28`

Класс `TelegramPanelRuntimeV29`; база: `TelegramPanelRuntimeV28`.

- `compact_menu_rows()` — 16 строк
- `source_menu_rows()` — 28 строк
- `control_menu_rows()` — 7 строк
- `show_analytics()` — 14 строк
- `show_stats()` — 23 строк
- `show_reports()` — 14 строк
- `show_sources()` — 35 строк
- `show_control()` — 10 строк
- `show_source_request_help()` — 8 строк
- `render_page()` — 20 строк
- `handle_callback()` — 8 строк

### `admin_panel_runtime_v3.py` — 364 строк, в рабочей цепочке

Прямые импорты: `admin_bot`, `admin_panel_runtime_v2`, `admin_panel_v2`

Класс `TelegramPanelRuntimeV3`; база: `TelegramPanelRuntimeV2`.

- `_security_payload()` — 11 строк
- `_signature()` — 5 строк
- `_trusted_owner()` — 2 строк
- `normalize_access()` — 26 строк
- `show_active()` — 83 строк
- `show_sources()` — 31 строк
- `show_reports()` — 14 строк
- `show_discovery()` — 49 строк
- `show_settings()` — 22 строк
- `show_interval()` — 17 строк
- `set_interval()` — 7 строк
- `render_page()` — 5 строк
- `handle_callback()` — 24 строк

### `admin_panel_runtime_v30.py` — 313 строк, в рабочей цепочке

Прямые импорты: `admin_panel_runtime_v29`, `bbvg.bot.source_requests`, `bbvg.bot.users`, `bot_private_state`

Класс `TelegramPanelRuntimeV30`; база: `TelegramPanelRuntimeV29`.

- `analytics_menu_rows()` — 5 строк
- `source_menu_rows()` — 28 строк
- `ranked_sources()` — 14 строк
- `notification_preferences()` — 9 строк
- `register_user()` — 38 строк
- `show_analytics()` — 9 строк
- `show_reports()` — 5 строк
- `show_ranking()` — 24 строк
- `show_notifications()` — 26 строк
- `toggle_notification()` — 4 строк
- `begin_source_request()` — 10 строк
- `render_page()` — 5 строк
- `handle_message()` — 20 строк
- `handle_callback()` — 11 строк

### `admin_panel_runtime_v31.py` — 408 строк, в рабочей цепочке

Прямые импорты: `admin_bot`, `admin_panel_runtime_v30`

Класс `TelegramPanelRuntimeV31`; база: `TelegramPanelRuntimeV30`.

- `setup_bot()` — 7 строк
- `analytics_menu_rows()` — 8 строк
- `control_menu_rows()` — 6 строк
- `summary_send_rows()` — 6 строк
- `period_title()` — 8 строк
- `period_overview()` — 59 строк
- `show_analytics()` — 7 строк
- `show_stats()` — 37 строк
- `show_reports()` — 17 строк
- `show_send_summary_menu()` — 8 строк
- `dispatch_summary()` — 5 строк
- `show_period_report()` — 41 строк
- `show_inactive_report()` — 22 строк
- `show_control()` — 10 строк
- `notification_preferences()` — 6 строк
- `render_page()` — 22 строк
- `handle_callback()` — 21 строк

### `admin_panel_runtime_v32.py` — 330 строк, в рабочей цепочке

Прямые импорты: `admin_action_v3`, `admin_bot`, `admin_panel_runtime_v31`

Класс `TelegramPanelRuntimeV32`; база: `TelegramPanelRuntimeV31`.

- `setup_bot()` — 7 строк
- `compact_menu_rows()` — 14 строк
- `_collect_current_wheels()` — 10 строк
- `_sources_for_item()` — 24 строк
- `show_active()` — 76 строк
- `show_analytics()` — 47 строк
- `show_stats()` — 2 строк
- `show_reports()` — 2 строк
- `show_period_report()` — 2 строк
- `render_page()` — 30 строк
- `handle_callback()` — 45 строк

### `admin_panel_runtime_v36.py` — 153 строк, в рабочей цепочке

Прямые импорты: `admin_bot`, `admin_panel_runtime_v32`, `bbvg.bot.users`, `bot_notification_state`

Класс `TelegramPanelRuntimeV36`; база: `UserSettingsMixin`, `TelegramPanelRuntimeV32`.

- `safe_text_for_role()` — 5 строк
- `send()` — 9 строк
- `setup_bot()` — 9 строк
- `source_menu_rows()` — 7 строк
- `show_ranking()` — 25 строк

### `admin_panel_runtime_v37.py` — 873 строк, в рабочей цепочке

Прямые импорты: `admin_action_queue`, `admin_bot`, `admin_panel_runtime_v36`, `bbvg.bot.users`

Класс `TelegramPanelRuntimeV37`; база: `TelegramPanelRuntimeV36`.

- `__init__()` — 5 строк
- `record_runtime_heartbeat()` — 44 строк
- `notification_preferences()` — 13 строк
- `_notification_options_for_role()` — 7 строк
- `_apply_notification_policy_once()` — 26 строк
- `setup_bot()` — 21 строк
- `register_user()` — 28 строк
- `show_notifications()` — 36 строк
- `toggle_notification()` — 4 строк
- `_remove_summary_preferences()` — 17 строк
- `set_admin()` — 3 строк
- `transfer_owner()` — 4 строк
- `show_menu()` — 19 строк
- `handle_message()` — 9 строк
- `dispatch_admin_action()` — 13 строк
- `_monitor_status()` — 5 строк
- `show_active()` — 67 строк
- `parse_manual_deadline()` — 14 строк
- `request_manual_time()` — 41 строк
- `_set_quick_time()` — 9 строк
- `show_analytics()` — 49 строк
- `show_stats()` — 2 строк
- `show_reports()` — 2 строк
- `show_period_report()` — 2 строк
- `show_status()` — 34 строк
- `show_sources()` — 39 строк
- `show_intelligence()` — 43 строк
- `show_discovery()` — 47 строк
- `render_page()` — 8 строк
- `handle_callback()` — 31 строк

### `admin_panel_runtime_v38.py` — 801 строк, в рабочей цепочке

Прямые импорты: `admin_bot`, `admin_panel_runtime_v37`, `telegram_ui`

Класс `TelegramPanelRuntimeV38`; база: `TelegramPanelRuntimeV37`.

- `send()` — 12 строк
- `compact_menu_rows()` — 18 строк
- `_normalize_page()` — 18 строк
- `_page_family()` — 12 строк
- `open_page()` — 9 строк
- `_wheel_digest()` — 3 строк
- `_wheel_token()` — 9 строк
- `_wheel_callback()` — 6 строк
- `_quick_time_callback()` — 9 строк
- `_resolve_wheel_token()` — 10 строк
- `show_active()` — 126 строк
- `request_manual_time()` — 45 строк
- `_set_quick_time()` — 5 строк
- `period_overview()` — 15 строк
- `_period_buttons()` — 9 строк
- `show_analytics()` — 49 строк
- `show_stats()` — 2 строк
- `show_reports()` — 2 строк
- `show_period_report()` — 2 строк
- `show_ranking()` — 21 строк
- `show_inactive_report()` — 59 строк
- `show_status()` — 33 строк
- `_display_user()` — 11 строк
- `show_access()` — 74 строк
- `show_recipients()` — 57 строк
- `render_page()` — 29 строк
- `handle_callback()` — 18 строк

### `admin_panel_runtime_v4.py` — 228 строк, в рабочей цепочке

Прямые импорты: `admin_panel_runtime_v3`, `monitor`

Класс `TelegramPanelRuntimeV4`; база: `TelegramPanelRuntimeV3`.

- `show_menu()` — 12 строк
- `_entry_key()` — 2 строк
- `_restore_telegram_deadline()` — 13 строк
- `_inspect_entry()` — 9 строк
- `_collect_current_wheels()` — 63 строк
- `show_active()` — 56 строк

### `admin_panel_runtime_v41.py` — 8 строк, в рабочей цепочке

Прямые импорты: `bbvg.bot.runtime`

Классы не определены.

### `admin_panel_runtime_v5.py` — 421 строк, в рабочей цепочке

Прямые импорты: `admin_panel_runtime_v4`

Класс `TelegramPanelRuntimeV5`; база: `TelegramPanelRuntimeV4`.

- `load_moderation()` — 18 строк
- `save_moderation()` — 7 строк
- `candidate_score()` — 18 строк
- `score_label()` — 6 строк
- `recommendation()` — 6 строк
- `candidate_rows()` — 36 строк
- `_candidate_filter()` — 5 строк
- `show_discovery()` — 46 строк
- `show_candidate_list()` — 42 строк
- `_recent_candidate_wheels()` — 11 строк
- `show_candidate_detail()` — 50 строк
- `set_candidate_mode()` — 17 строк
- `ignore_candidate()` — 13 строк
- `restore_candidate()` — 12 строк
- `render_page()` — 9 строк
- `handle_callback()` — 65 строк

### `admin_panel_runtime_v6.py` — 343 строк, в рабочей цепочке

Прямые импорты: `admin_panel_runtime_v5`

Класс `TelegramPanelRuntimeV6`; база: `TelegramPanelRuntimeV5`.

- `show_menu()` — 13 строк
- `intelligence_state()` — 8 строк
- `intelligence_rows()` — 28 строк
- `intelligence_label()` — 6 строк
- `show_intelligence()` — 45 строк
- `filtered_intelligence_rows()` — 9 строк
- `show_intelligence_list()` — 36 строк
- `show_intelligence_detail()` — 39 строк
- `render_page()` — 12 строк
- `handle_message()` — 10 строк
- `handle_callback()` — 63 строк

### `admin_panel_runtime_v7.py` — 211 строк, в рабочей цепочке

Прямые импорты: `admin_panel_runtime_v6`, `monitor`

Класс `TelegramPanelRuntimeV7`; база: `TelegramPanelRuntimeV6`.

- `show_menu()` — 12 строк
- `_collect_current_wheels()` — 83 строк
- `handle_message()` — 39 строк

### `admin_panel_runtime_v8.py` — 182 строк, в рабочей цепочке

Прямые импорты: `admin_panel_runtime_v7`

Класс `TelegramPanelRuntimeV8`; база: `TelegramPanelRuntimeV7`.

- `show_sources()` — 21 строк
- `show_reports()` — 14 строк
- `handle_callback()` — 37 строк

Класс `_TestPanel`; база: `TelegramPanelRuntimeV8`.

- `__init__()` — 9 строк
- `role_for()` — 2 строк
- `can_view()` — 2 строк
- `is_admin()` — 2 строк
- `set_context()` — 4 строк
- `send()` — 3 строк
- `answer()` — 2 строк
- `open_page()` — 2 строк
- `dispatch_admin_action()` — 3 строк

### `admin_panel_runtime_v9.py` — 169 строк, в рабочей цепочке

Прямые импорты: `admin_panel_runtime_v6`, `admin_panel_runtime_v7`, `admin_panel_runtime_v8`

Класс `TelegramPanelRuntimeV9`; база: `TelegramPanelRuntimeV8`.

- `show_menu()` — 12 строк
- `show_app_entry()` — 9 строк
- `show_settings()` — 2 строк
- `show_active()` — 49 строк
- `handle_message()` — 19 строк
- `render_page()` — 5 строк

## Методы, определённые в нескольких слоях

Такие методы требуют осторожного объединения.

- `__init__()` — `admin_panel_runtime_v37`, `admin_panel_runtime_v8`
- `_apply_admin_action_direct()` — `admin_panel_runtime_v26`, `admin_panel_runtime_v28`
- `_collect_current_wheels()` — `admin_panel_runtime_v32`, `admin_panel_runtime_v4`, `admin_panel_runtime_v7`
- `_set_quick_time()` — `admin_panel_runtime_v37`, `admin_panel_runtime_v38`
- `analytics_menu_rows()` — `admin_panel_runtime_v30`, `admin_panel_runtime_v31`
- `compact_menu_rows()` — `admin_panel_runtime_v25`, `admin_panel_runtime_v26`, `admin_panel_runtime_v29`, `admin_panel_runtime_v32`, `admin_panel_runtime_v38`
- `control_menu_rows()` — `admin_panel_runtime_v29`, `admin_panel_runtime_v31`
- `dispatch_admin_action()` — `admin_panel_runtime_v26`, `admin_panel_runtime_v37`, `admin_panel_runtime_v8`
- `handle_callback()` — `admin_panel_runtime_v25`, `admin_panel_runtime_v26`, `admin_panel_runtime_v29`, `admin_panel_runtime_v3`, `admin_panel_runtime_v30`, `admin_panel_runtime_v31`, `admin_panel_runtime_v32`, `admin_panel_runtime_v37`, `admin_panel_runtime_v38`, `admin_panel_runtime_v5`, `admin_panel_runtime_v6`, `admin_panel_runtime_v8`
- `handle_message()` — `admin_panel_runtime_v30`, `admin_panel_runtime_v37`, `admin_panel_runtime_v6`, `admin_panel_runtime_v7`, `admin_panel_runtime_v9`
- `notification_preferences()` — `admin_panel_runtime_v30`, `admin_panel_runtime_v31`, `admin_panel_runtime_v37`
- `open_page()` — `admin_panel_runtime_v38`, `admin_panel_runtime_v8`
- `period_overview()` — `admin_panel_runtime_v31`, `admin_panel_runtime_v38`
- `register_user()` — `admin_panel_runtime_v2`, `admin_panel_runtime_v30`, `admin_panel_runtime_v37`
- `render_page()` — `admin_panel_runtime_v25`, `admin_panel_runtime_v29`, `admin_panel_runtime_v3`, `admin_panel_runtime_v30`, `admin_panel_runtime_v31`, `admin_panel_runtime_v32`, `admin_panel_runtime_v37`, `admin_panel_runtime_v38`, `admin_panel_runtime_v5`, `admin_panel_runtime_v6`, `admin_panel_runtime_v9`
- `request_manual_time()` — `admin_panel_runtime_v37`, `admin_panel_runtime_v38`
- `send()` — `admin_panel_runtime_v36`, `admin_panel_runtime_v38`, `admin_panel_runtime_v8`
- `setup_bot()` — `admin_panel_runtime_v31`, `admin_panel_runtime_v32`, `admin_panel_runtime_v36`, `admin_panel_runtime_v37`
- `show_active()` — `admin_panel_runtime_v25`, `admin_panel_runtime_v3`, `admin_panel_runtime_v32`, `admin_panel_runtime_v37`, `admin_panel_runtime_v38`, `admin_panel_runtime_v4`, `admin_panel_runtime_v9`
- `show_analytics()` — `admin_panel_runtime_v29`, `admin_panel_runtime_v30`, `admin_panel_runtime_v31`, `admin_panel_runtime_v32`, `admin_panel_runtime_v37`, `admin_panel_runtime_v38`
- `show_control()` — `admin_panel_runtime_v29`, `admin_panel_runtime_v31`
- `show_discovery()` — `admin_panel_runtime_v3`, `admin_panel_runtime_v37`, `admin_panel_runtime_v5`
- `show_inactive_report()` — `admin_panel_runtime_v31`, `admin_panel_runtime_v38`
- `show_intelligence()` — `admin_panel_runtime_v37`, `admin_panel_runtime_v6`
- `show_menu()` — `admin_panel_runtime_v37`, `admin_panel_runtime_v4`, `admin_panel_runtime_v6`, `admin_panel_runtime_v7`, `admin_panel_runtime_v9`
- `show_notifications()` — `admin_panel_runtime_v30`, `admin_panel_runtime_v37`
- `show_period_report()` — `admin_panel_runtime_v31`, `admin_panel_runtime_v32`, `admin_panel_runtime_v37`, `admin_panel_runtime_v38`
- `show_ranking()` — `admin_panel_runtime_v25`, `admin_panel_runtime_v30`, `admin_panel_runtime_v36`, `admin_panel_runtime_v38`
- `show_reports()` — `admin_panel_runtime_v29`, `admin_panel_runtime_v3`, `admin_panel_runtime_v30`, `admin_panel_runtime_v31`, `admin_panel_runtime_v32`, `admin_panel_runtime_v37`, `admin_panel_runtime_v38`, `admin_panel_runtime_v8`
- `show_settings()` — `admin_panel_runtime_v3`, `admin_panel_runtime_v9`
- `show_sources()` — `admin_panel_runtime_v25`, `admin_panel_runtime_v29`, `admin_panel_runtime_v3`, `admin_panel_runtime_v37`, `admin_panel_runtime_v8`
- `show_stats()` — `admin_panel_runtime_v29`, `admin_panel_runtime_v31`, `admin_panel_runtime_v32`, `admin_panel_runtime_v37`, `admin_panel_runtime_v38`
- `show_status()` — `admin_panel_runtime_v37`, `admin_panel_runtime_v38`
- `source_menu_rows()` — `admin_panel_runtime_v29`, `admin_panel_runtime_v30`, `admin_panel_runtime_v36`
- `toggle_notification()` — `admin_panel_runtime_v30`, `admin_panel_runtime_v37`
