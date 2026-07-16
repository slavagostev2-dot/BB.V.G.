# BB V.G. — карта методов исторического runtime панели

Файл генерируется автоматически из AST. Он описывает текущую ветку рефакторинга и не заменяет ручной анализ поведения.

- Runtime-файлов: **34**
- В текущей цепочке: **31**
- Суммарно строк: **9273**
- Уникальных имён методов: **137**

## Файлы и определённые методы

### `admin_panel_runtime_v13.py` — 33 строк, в рабочей цепочке

Прямые импорты: `admin_panel_runtime_v9`, `bbvg.bot.foundation`

Класс `TelegramPanelRuntimeV13`; база: `PanelFoundationMixin`, `TelegramPanelRuntimeV9`.

- собственных методов нет

### `admin_panel_runtime_v16.py` — 29 строк, в рабочей цепочке

Прямые импорты: `bbvg.bot.interface`

Класс `TelegramPanelRuntimeV16`; база: `PanelInterfaceRuntime`.

- собственных методов нет

### `admin_panel_runtime_v17.py` — 403 строк, в рабочей цепочке

Прямые импорты: `admin_bot`, `admin_panel_runtime_v16`, `admin_panel_v2`, `telegram_transport`

Класс `TelegramPanelRuntimeV17`; база: `TelegramPanelRuntimeV16`.

- `__init__()` — 3 строк
- `bot_username()` — 11 строк
- `miniapp_url_for_chat()` — 11 строк
- `show_app_entry()` — 10 строк
- `load_source_requests()` — 11 строк
- `save_source_requests()` — 3 строк
- `moderator_chat_ids()` — 17 строк
- `can_moderate_source_requests()` — 2 строк
- `requester_name()` — 9 строк
- `inspect_source()` — 45 строк
- `request_id()` — 3 строк
- `notify_moderators()` — 34 строк
- `submit_source_request()` — 66 строк
- `decide_source_request()` — 44 строк
- `handle_message()` — 30 строк
- `handle_callback()` — 34 строк

### `admin_panel_runtime_v18.py` — 52 строк, в рабочей цепочке

Прямые импорты: `admin_panel_runtime_v17`

Класс `TelegramPanelRuntimeV18`; база: `TelegramPanelRuntimeV17`.

- `miniapp_url_for_chat()` — 16 строк

### `admin_panel_runtime_v19.py` — 67 строк, в рабочей цепочке

Прямые импорты: `admin_panel_runtime_v18`

Класс `TelegramPanelRuntimeV19`; база: `TelegramPanelRuntimeV18`.

- `miniapp_url_for_chat()` — 6 строк
- `show_menu()` — 12 строк
- `show_app_entry()` — 10 строк

### `admin_panel_runtime_v2.py` — 140 строк, в рабочей цепочке

Прямые импорты: `admin_bot`, `admin_panel_v2`

Класс `TelegramPanelRuntimeV2`; база: `admin_panel_v2.TelegramPanelV2`.

- `register_user()` — 72 строк

### `admin_panel_runtime_v20.py` — 468 строк, в рабочей цепочке

Прямые импорты: `admin_bot`, `admin_panel_runtime_v19`

Класс `TelegramPanelRuntimeV20`; база: `TelegramPanelRuntimeV19`.

- `_hidden_wheels()` — 23 строк
- `hide_wheel_for_current_user()` — 21 строк
- `_personal_participating_wheels()` — 11 строк
- `mark_personal_participation()` — 20 строк
- `_joined_wheel_keys()` — 8 строк
- `_collect_current_wheels()` — 38 строк
- `show_active()` — 53 строк
- `show_stats()` — 32 строк
- `parse_manual_deadline()` — 55 строк
- `request_manual_time()` — 17 строк
- `_delete_callback_message()` — 13 строк
- `handle_message()` — 34 строк
- `handle_callback()` — 80 строк
- `render_page()` — 5 строк

### `admin_panel_runtime_v21.py` — 439 строк, в рабочей цепочке

Прямые импорты: `admin_panel_runtime_v20`

Класс `TelegramPanelRuntimeV21`; база: `TelegramPanelRuntimeV20`.

- `register_user()` — 17 строк
- `handle_update()` — 18 строк
- `notify_owner_about_new_user()` — 36 строк
- `show_access()` — 3 строк
- `show_user_detail()` — 3 строк
- `show_recipients()` — 3 строк
- `miniapp_url_for_chat()` — 14 строк
- `compact_menu_rows()` — 7 строк
- `notification_preferences()` — 41 строк
- `show_settings()` — 18 строк
- `show_notifications()` — 40 строк
- `toggle_notification()` — 33 строк
- `set_admin()` — 24 строк
- `transfer_owner()` — 24 строк
- `render_page()` — 5 строк
- `handle_message()` — 20 строк
- `handle_callback()` — 21 строк

### `admin_panel_runtime_v22.py` — 327 строк, в рабочей цепочке

Прямые импорты: `admin_bot`, `admin_panel_runtime_v21`

Класс `TelegramPanelRuntimeV22`; база: `TelegramPanelRuntimeV21`.

- `compact_menu_rows()` — 16 строк
- `miniapp_url_for_chat()` — 16 строк
- `load_source_registry()` — 9 строк
- `source_registry_fallback()` — 48 строк
- `show_sources()` — 39 строк
- `show_stats()` — 33 строк
- `show_ranking()` — 34 строк
- `show_active()` — 59 строк
- `render_page()` — 11 строк

### `admin_panel_runtime_v23.py` — 138 строк, вне рабочей цепочки

Прямые импорты: `admin_bot`, `admin_panel_runtime_v17`, `admin_panel_runtime_v22`, `admin_panel_v2`, `private_state`

Класс `TelegramPanelRuntimeV23`; база: `TelegramPanelRuntimeV22`.

- `__init__()` — 3 строк
- `temporary_access()` — 21 строк
- `load_access()` — 11 строк
- `save_access()` — 8 строк
- `load_source_requests()` — 10 строк
- `save_source_requests()` — 6 строк

### `admin_panel_runtime_v25.py` — 393 строк, в рабочей цепочке

Прямые импорты: `admin_bot`, `admin_panel_runtime_v17`, `admin_panel_runtime_v21`, `admin_panel_runtime_v22`, `admin_panel_v2`, `bot_private_state`

Класс `TelegramPanelRuntimeV25`; база: `TelegramPanelRuntimeV22`.

- `__init__()` — 4 строк
- `_bootstrap_access()` — 43 строк
- `_load_bot_bundle()` — 18 строк
- `_save_bot_bundle()` — 18 строк
- `load_access()` — 8 строк
- `save_access()` — 8 строк
- `load_source_requests()` — 7 строк
- `save_source_requests()` — 8 строк
- `compact_menu_rows()` — 23 строк
- `show_sources()` — 42 строк
- `show_ranking()` — 36 строк
- `show_active()` — 58 строк
- `handle_callback()` — 12 строк
- `render_page()` — 21 строк

### `admin_panel_runtime_v26.py` — 320 строк, в рабочей цепочке

Прямые импорты: `admin_action_v2`, `admin_bot`, `admin_panel_runtime_v17`, `admin_panel_runtime_v22`, `admin_panel_runtime_v25`, `bot_private_state`

Класс `TelegramPanelRuntimeV26`; база: `TelegramPanelRuntimeV25`.

- `compact_menu_rows()` — 11 строк
- `_read_json_at()` — 18 строк
- `_serialize_json()` — 3 строк
- `_apply_admin_action_direct()` — 103 строк
- `dispatch_admin_action()` — 13 строк
- `_prepare_callback_user()` — 13 строк
- `handle_callback()` — 52 строк

### `admin_panel_runtime_v27.py` — 106 строк, в рабочей цепочке

Прямые импорты: `admin_panel_runtime_v17`, `admin_panel_runtime_v25`, `admin_panel_runtime_v26`, `bot_private_state`

Класс `TelegramPanelRuntimeV27`; база: `TelegramPanelRuntimeV26`.

- собственных методов нет

### `admin_panel_runtime_v28.py` — 85 строк, в рабочей цепочке

Прямые импорты: `admin_action_v2`, `admin_panel_runtime_v27`

Класс `TelegramPanelRuntimeV28`; база: `TelegramPanelRuntimeV27`.

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

Прямые импорты: `admin_panel_runtime_v17`, `admin_panel_runtime_v21`, `admin_panel_runtime_v29`, `bot_private_state`

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

### `admin_panel_runtime_v33.py` — 289 строк, в рабочей цепочке

Прямые импорты: `admin_panel_runtime_v17`, `admin_panel_runtime_v21`, `admin_panel_runtime_v32`, `bot_private_state`, `privacy_retention`

Класс `TelegramPanelRuntimeV33`; база: `TelegramPanelRuntimeV32`.

- `notification_preferences()` — 14 строк
- `register_user()` — 23 строк
- `show_settings()` — 28 строк
- `show_notifications()` — 38 строк
- `toggle_notification()` — 37 строк
- `delete_current_user_data()` — 15 строк
- `handle_callback()` — 45 строк

### `admin_panel_runtime_v34.py` — 695 строк, в рабочей цепочке

Прямые импорты: `admin_bot`, `admin_panel_runtime_v17`, `admin_panel_runtime_v21`, `admin_panel_runtime_v33`, `bot_private_state`, `privacy_retention`

Класс `TelegramPanelRuntimeV34`; база: `TelegramPanelRuntimeV33`.

- `__init__()` — 3 строк
- `_normalize_bundle()` — 12 строк
- `_load_remote_bundle()` — 8 строк
- `_load_bot_bundle()` — 11 строк
- `_merge_access()` — 68 строк
- `_write_remote_bundle()` — 20 строк
- `_save_bot_bundle()` — 39 строк
- `show_notifications()` — 58 строк
- `toggle_notification()` — 38 строк
- `_notification_options_for_role()` — 8 строк
- `show_user_detail()` — 61 строк
- `show_user_notifications()` — 66 строк
- `set_user_notification()` — 34 строк
- `set_all_user_notifications()` — 28 строк
- `render_page()` — 5 строк
- `handle_callback()` — 38 строк

### `admin_panel_runtime_v35.py` — 196 строк, в рабочей цепочке

Прямые импорты: `admin_panel_runtime_v3`, `admin_panel_runtime_v34`, `admin_panel_v2`

Класс `TelegramPanelRuntimeV35`; база: `TelegramPanelRuntimeV34`.

- `normalize_access()` — 43 строк
- `_merge_access()` — 53 строк
- `set_user_notification()` — 8 строк
- `set_all_user_notifications()` — 3 строк

### `admin_panel_runtime_v36.py` — 151 строк, в рабочей цепочке

Прямые импорты: `admin_bot`, `admin_panel_runtime_v35`, `bot_notification_state`

Класс `TelegramPanelRuntimeV36`; база: `TelegramPanelRuntimeV35`.

- `safe_text_for_role()` — 5 строк
- `send()` — 9 строк
- `setup_bot()` — 9 строк
- `source_menu_rows()` — 7 строк
- `show_ranking()` — 25 строк

### `admin_panel_runtime_v37.py` — 873 строк, в рабочей цепочке

Прямые импорты: `admin_action_queue`, `admin_bot`, `admin_panel_runtime_v21`, `admin_panel_runtime_v33`, `admin_panel_runtime_v36`

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

### `admin_panel_runtime_v39.py` — 8 строк, вне рабочей цепочки

Прямые импорты: `bbvg.bot.runtime`

Классы не определены.

### `admin_panel_runtime_v4.py` — 228 строк, в рабочей цепочке

Прямые импорты: `admin_panel_runtime_v3`, `monitor`

Класс `TelegramPanelRuntimeV4`; база: `TelegramPanelRuntimeV3`.

- `show_menu()` — 12 строк
- `_entry_key()` — 2 строк
- `_restore_telegram_deadline()` — 13 строк
- `_inspect_entry()` — 9 строк
- `_collect_current_wheels()` — 63 строк
- `show_active()` — 56 строк

### `admin_panel_runtime_v40.py` — 8 строк, вне рабочей цепочки

Прямые импорты: `bbvg.bot.runtime`

Классы не определены.

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

Такие методы требуют особенно осторожного объединения: более поздний слой может сознательно заменять прежнее поведение.

- `__init__()` — `admin_panel_runtime_v17`, `admin_panel_runtime_v23`, `admin_panel_runtime_v25`, `admin_panel_runtime_v34`, `admin_panel_runtime_v37`, `admin_panel_runtime_v8`
- `_apply_admin_action_direct()` — `admin_panel_runtime_v26`, `admin_panel_runtime_v28`
- `_collect_current_wheels()` — `admin_panel_runtime_v20`, `admin_panel_runtime_v32`, `admin_panel_runtime_v4`, `admin_panel_runtime_v7`
- `_load_bot_bundle()` — `admin_panel_runtime_v25`, `admin_panel_runtime_v34`
- `_merge_access()` — `admin_panel_runtime_v34`, `admin_panel_runtime_v35`
- `_notification_options_for_role()` — `admin_panel_runtime_v34`, `admin_panel_runtime_v37`
- `_save_bot_bundle()` — `admin_panel_runtime_v25`, `admin_panel_runtime_v34`
- `_set_quick_time()` — `admin_panel_runtime_v37`, `admin_panel_runtime_v38`
- `analytics_menu_rows()` — `admin_panel_runtime_v30`, `admin_panel_runtime_v31`
- `compact_menu_rows()` — `admin_panel_runtime_v21`, `admin_panel_runtime_v22`, `admin_panel_runtime_v25`, `admin_panel_runtime_v26`, `admin_panel_runtime_v29`, `admin_panel_runtime_v32`, `admin_panel_runtime_v38`
- `control_menu_rows()` — `admin_panel_runtime_v29`, `admin_panel_runtime_v31`
- `dispatch_admin_action()` — `admin_panel_runtime_v26`, `admin_panel_runtime_v37`, `admin_panel_runtime_v8`
- `handle_callback()` — `admin_panel_runtime_v17`, `admin_panel_runtime_v20`, `admin_panel_runtime_v21`, `admin_panel_runtime_v25`, `admin_panel_runtime_v26`, `admin_panel_runtime_v29`, `admin_panel_runtime_v3`, `admin_panel_runtime_v30`, `admin_panel_runtime_v31`, `admin_panel_runtime_v32`, `admin_panel_runtime_v33`, `admin_panel_runtime_v34`, `admin_panel_runtime_v37`, `admin_panel_runtime_v38`, `admin_panel_runtime_v5`, `admin_panel_runtime_v6`, `admin_panel_runtime_v8`
- `handle_message()` — `admin_panel_runtime_v17`, `admin_panel_runtime_v20`, `admin_panel_runtime_v21`, `admin_panel_runtime_v30`, `admin_panel_runtime_v37`, `admin_panel_runtime_v6`, `admin_panel_runtime_v7`, `admin_panel_runtime_v9`
- `load_access()` — `admin_panel_runtime_v23`, `admin_panel_runtime_v25`
- `load_source_requests()` — `admin_panel_runtime_v17`, `admin_panel_runtime_v23`, `admin_panel_runtime_v25`
- `miniapp_url_for_chat()` — `admin_panel_runtime_v17`, `admin_panel_runtime_v18`, `admin_panel_runtime_v19`, `admin_panel_runtime_v21`, `admin_panel_runtime_v22`
- `normalize_access()` — `admin_panel_runtime_v3`, `admin_panel_runtime_v35`
- `notification_preferences()` — `admin_panel_runtime_v21`, `admin_panel_runtime_v30`, `admin_panel_runtime_v31`, `admin_panel_runtime_v33`, `admin_panel_runtime_v37`
- `open_page()` — `admin_panel_runtime_v38`, `admin_panel_runtime_v8`
- `parse_manual_deadline()` — `admin_panel_runtime_v20`, `admin_panel_runtime_v37`
- `period_overview()` — `admin_panel_runtime_v31`, `admin_panel_runtime_v38`
- `register_user()` — `admin_panel_runtime_v2`, `admin_panel_runtime_v21`, `admin_panel_runtime_v30`, `admin_panel_runtime_v33`, `admin_panel_runtime_v37`
- `render_page()` — `admin_panel_runtime_v20`, `admin_panel_runtime_v21`, `admin_panel_runtime_v22`, `admin_panel_runtime_v25`, `admin_panel_runtime_v29`, `admin_panel_runtime_v3`, `admin_panel_runtime_v30`, `admin_panel_runtime_v31`, `admin_panel_runtime_v32`, `admin_panel_runtime_v34`, `admin_panel_runtime_v37`, `admin_panel_runtime_v38`, `admin_panel_runtime_v5`, `admin_panel_runtime_v6`, `admin_panel_runtime_v9`
- `request_manual_time()` — `admin_panel_runtime_v20`, `admin_panel_runtime_v37`, `admin_panel_runtime_v38`
- `save_access()` — `admin_panel_runtime_v23`, `admin_panel_runtime_v25`
- `save_source_requests()` — `admin_panel_runtime_v17`, `admin_panel_runtime_v23`, `admin_panel_runtime_v25`
- `send()` — `admin_panel_runtime_v36`, `admin_panel_runtime_v38`, `admin_panel_runtime_v8`
- `set_admin()` — `admin_panel_runtime_v21`, `admin_panel_runtime_v37`
- `set_all_user_notifications()` — `admin_panel_runtime_v34`, `admin_panel_runtime_v35`
- `set_user_notification()` — `admin_panel_runtime_v34`, `admin_panel_runtime_v35`
- `setup_bot()` — `admin_panel_runtime_v31`, `admin_panel_runtime_v32`, `admin_panel_runtime_v36`, `admin_panel_runtime_v37`
- `show_access()` — `admin_panel_runtime_v21`, `admin_panel_runtime_v38`
- `show_active()` — `admin_panel_runtime_v20`, `admin_panel_runtime_v22`, `admin_panel_runtime_v25`, `admin_panel_runtime_v3`, `admin_panel_runtime_v32`, `admin_panel_runtime_v37`, `admin_panel_runtime_v38`, `admin_panel_runtime_v4`, `admin_panel_runtime_v9`
- `show_analytics()` — `admin_panel_runtime_v29`, `admin_panel_runtime_v30`, `admin_panel_runtime_v31`, `admin_panel_runtime_v32`, `admin_panel_runtime_v37`, `admin_panel_runtime_v38`
- `show_app_entry()` — `admin_panel_runtime_v17`, `admin_panel_runtime_v19`, `admin_panel_runtime_v9`
- `show_control()` — `admin_panel_runtime_v29`, `admin_panel_runtime_v31`
- `show_discovery()` — `admin_panel_runtime_v3`, `admin_panel_runtime_v37`, `admin_panel_runtime_v5`
- `show_inactive_report()` — `admin_panel_runtime_v31`, `admin_panel_runtime_v38`
- `show_intelligence()` — `admin_panel_runtime_v37`, `admin_panel_runtime_v6`
- `show_menu()` — `admin_panel_runtime_v19`, `admin_panel_runtime_v37`, `admin_panel_runtime_v4`, `admin_panel_runtime_v6`, `admin_panel_runtime_v7`, `admin_panel_runtime_v9`
- `show_notifications()` — `admin_panel_runtime_v21`, `admin_panel_runtime_v30`, `admin_panel_runtime_v33`, `admin_panel_runtime_v34`, `admin_panel_runtime_v37`
- `show_period_report()` — `admin_panel_runtime_v31`, `admin_panel_runtime_v32`, `admin_panel_runtime_v37`, `admin_panel_runtime_v38`
- `show_ranking()` — `admin_panel_runtime_v22`, `admin_panel_runtime_v25`, `admin_panel_runtime_v30`, `admin_panel_runtime_v36`, `admin_panel_runtime_v38`
- `show_recipients()` — `admin_panel_runtime_v21`, `admin_panel_runtime_v38`
- `show_reports()` — `admin_panel_runtime_v29`, `admin_panel_runtime_v3`, `admin_panel_runtime_v30`, `admin_panel_runtime_v31`, `admin_panel_runtime_v32`, `admin_panel_runtime_v37`, `admin_panel_runtime_v38`, `admin_panel_runtime_v8`
- `show_settings()` — `admin_panel_runtime_v21`, `admin_panel_runtime_v3`, `admin_panel_runtime_v33`, `admin_panel_runtime_v9`
- `show_sources()` — `admin_panel_runtime_v22`, `admin_panel_runtime_v25`, `admin_panel_runtime_v29`, `admin_panel_runtime_v3`, `admin_panel_runtime_v37`, `admin_panel_runtime_v8`
- `show_stats()` — `admin_panel_runtime_v20`, `admin_panel_runtime_v22`, `admin_panel_runtime_v29`, `admin_panel_runtime_v31`, `admin_panel_runtime_v32`, `admin_panel_runtime_v37`, `admin_panel_runtime_v38`
- `show_status()` — `admin_panel_runtime_v37`, `admin_panel_runtime_v38`
- `show_user_detail()` — `admin_panel_runtime_v21`, `admin_panel_runtime_v34`
- `source_menu_rows()` — `admin_panel_runtime_v29`, `admin_panel_runtime_v30`, `admin_panel_runtime_v36`
- `toggle_notification()` — `admin_panel_runtime_v21`, `admin_panel_runtime_v30`, `admin_panel_runtime_v33`, `admin_panel_runtime_v34`, `admin_panel_runtime_v37`
- `transfer_owner()` — `admin_panel_runtime_v21`, `admin_panel_runtime_v37`
