# BB V.G. — владельцы методов Control Center

Актуально на 26 июля 2026 года. Карта нужна для безопасного рефакторинга:
изменение выполняется в предметном владельце, а не новым versioned-слоем.

## Production MRO

```text
notification_button_recovery.TelegramPanelRuntimeButtonRecovery
  admin_panel_runtime_v41.TelegramPanelRuntimeV41
  bbvg.bot.runtime.TelegramPanelRuntime
  personal_wheel_voting.PersonalWheelVotingMixin
  bbvg.bot.users.UserSettingsMixin
  bbvg.bot.storage.PrivateStateRuntime
  bbvg.bot.sources.SourceRegistryRuntime
  bbvg.bot.users.UserManagementRuntime
  bbvg.bot.wheels.WheelInteractionRuntime
  bbvg.bot.source_requests.SourceRequestRuntime
  bbvg.bot.interface.PanelInterfaceRuntime
  bbvg.bot.foundation.PanelFoundationMixin
  admin_panel_v2.TelegramPanelV2
  admin_runtime.RuntimeAdminBot
  admin_bot.AdminBot
```

В MRO нет `admin_panel_runtime_v2`–`v40`. `v41` остаётся единственным тонким
compatibility-слоем.

## Критические методы

| Метод | Владелец |
|---|---|
| `handle_update` | `bbvg.bot.users.UserManagementRuntime` |
| `handle_callback` | `admin_panel_runtime_v41.TelegramPanelRuntimeV41` |
| `show_menu` | `bbvg.bot.runtime.TelegramPanelRuntime` |
| `show_active` | `bbvg.bot.runtime.TelegramPanelRuntime` |
| `show_user_detail` | `admin_panel_runtime_v41.TelegramPanelRuntimeV41` |
| `send` | `bbvg.bot.runtime.TelegramPanelRuntime` |
| `load_access`, `save_access` | `bbvg.bot.storage.PrivateStateRuntime` |
| `mark_personal_participation` | `personal_wheel_voting.PersonalWheelVotingMixin` |
| `set_candidate_mode`, `restore_candidate` | `bbvg.bot.interface.PanelInterfaceRuntime` |
| `decide_source_request` | `bbvg.bot.source_requests.SourceRequestRuntime` |

`save_access` меняет проверенный локальный snapshot немедленно и только ставит
encrypted bundle в фоновую очередь с retry и трёхсторонним merge. GitHub write
не выполняется в request-path `/start` или callback.

`notification_button_recovery.py` устанавливает дополнительные runtime-политики
для `show_settings`, recovery wheel-callback и объединённых результатов
автоучастия. Это композиция одного Control Center, а не второй consumer.

## Правило изменения

1. Определить владельца через `inspect.getmro()` и `method in cls.__dict__`.
2. Изменить предметный модуль.
3. Не добавлять новый `admin_panel_runtime_v*.py`.
4. Сохранить callback strings, порядок кнопок и формат приватного состояния.
5. Обновить эту карту, если владелец или MRO изменился.
6. Запустить `tests/production_acceptance.py --section interface`, полный
   `pytest` и `scripts/validate_control_center.sh`.
