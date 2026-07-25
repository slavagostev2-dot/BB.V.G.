# BB V.G. — план очистки и рефакторинга

## Цель

Уменьшать количество параллельных реализаций и исторических файлов, не меняя
пользовательское поведение, callback/state-контракты и production continuity.

## Завершено 23 июля 2026 года

- подтверждена независимая backup-ветка до очистки;
- удалена мёртвая цепочка `admin_panel_runtime_v2.py`–`v24.py`;
- повторно удалены неиспользуемые `monitor_resilience.py` и
  `normalize_source_ratings.py`;
- пять chapter-обёрток заменены прямыми секциями единого
  `tests/production_acceptance.py`;
- удалены ложные Markdown-файлы со старым Python/YAML и устаревшие chapter-отчёты;
- активные workflow с историческим числом `66` получили предметные имена;
- workflow `v22-checks.yml` и три chapter-теста получили предметные имена;
- восстановлены README, карта кода, карта MRO и актуальный контекст;
- preflight запрещает возврат удалённых файлов и требует обязательные документы;
- release-marker стал единственным владельцем exact-SHA Control Center, а
  controlled recovery Monitor передан watchdog без self-retry при ошибке;
- отменённая или вытесненная смена Control Center больше не создаёт преемника:
  self-dispatch разрешён только после штатного успеха, а разрыв страхует
  почасовой schedule без eventual-consistency гонки Actions.

## Стабилизация 25 июля 2026 года

После повторяющихся регрессий кнопок, поиска, уведомлений и автоучастия дальнейший
функциональный рефакторинг приостановлен до введения обязательного safety-gate.

- создана неизменяемая точка `backup/before-production-stability-guardrails-20260725`;
- добавлена единая политика `engineering/PRODUCTION_STABILITY_POLICY_RU.md`;
- текущая поверхность прямых runtime-подмен Monitor и Control Center заморожена
  тестом `tests/test_production_stability_guardrails.py`;
- новый `module.function = wrapper`, новый слой `install()` и возврат лестницы
  versioned-runtime теперь должны останавливать PR CI;
- `current-checks.yml` запускает архитектурный guard до production acceptance и
  полного `pytest`;
- функциональный PR отделён от отдельного exact-SHA release commit;
- обязательная матрица теперь проверяет весь путь: callback → поиск → состояние →
  уведомление → автоучастие → итог → heartbeat.

## Выполнено 25 июля 2026 года — первый этап стабильного упрощения

- фактическим единственным владельцем `bb:p:<token>` и `wheel:part:<key>` закреплён
  существующий `PersonalWheelVotingMixin` в `personal_wheel_voting.py`;
- восстановление потерянного `button_contexts` перенесено к тому же владельцу;
- из `admin_panel_runtime_v41.py` удалены две отдельные callback-ветки;
- из `bbvg/bot/runtime.py` удалён промежуточный перехват тех же callback;
- из `notification_button_recovery.py` удалены собственные token-helper и override;
- удаление исходной карточки уведомления, открытие главного меню в том же сообщении,
  event-scoped личный голос и прежние callback-строки сохранены;
- отдельные self-test подтверждают владельца, общий runtime, compatibility-v41 и
  прежний fallback-токен уведомления `cba7abb40c5b77`.

## Оставшийся технический долг

Следующие файлы не являются подтверждённым мусором и остаются действующими:

1. `system_checks.py` + `system_checks_v2.py` + `system_checks_v3.py`.
   Нужен отдельный перенос расширений в одного владельца с regression health.
2. `admin_action.py` + `admin_action_v2.py` + `admin_action_v3.py`.
   Требуется сохранить очередь, `command_id`, rating identity и старые callback.
3. Активные предметные модули с суффиксом `v2`
   (`wheel_lifecycle_v2.py`, `notification_integrity_v2.py`,
   `notification_preferences_v2.py`, `telegram_post_links_v2.py`).
   Переименование допустимо только одной атомарной миграцией импортов и тестов.
4. Корневые compatibility-слои `admin_panel_v2.py`,
   `admin_panel_runtime_v41.py`. Они остаются в production MRO/entrypoint и не
   удаляются до полного переноса их действующих методов.
5. `bbvg_monitor_main.py` и `notification_button_recovery.py` содержат
   замороженную legacy-композицию runtime-подмен. Новые подмены запрещены; каждый
   следующий этап должен уменьшать их число.

## Порядок следующего этапа

1. Не добавлять новые функции до подтверждения стабильности восстановленного
   production baseline.
2. Выбрать только один контур: сначала callback/кнопки Control Center.
3. Построить import/MRO/state inventory и определить одного владельца каждого
   метода этого контура.
4. До переноса добавить end-to-end regression текущего пользовательского пути.
5. Перенести поведение к предметному владельцу и удалить прежнюю обёртку в том же
   PR; число runtime-подмен должно уменьшиться.
6. Выполнить architecture guard, targeted tests, полный pytest, все секции
   production acceptance, preflight, security audit и exact-SHA validation.
7. Выпустить отдельным минимальным release commit.
8. Проверить живые heartbeat, команды и кнопки; при регрессии выполнить откат.
9. Только после подтверждённого production создать новый стабильный backup и
   перейти к следующему контуру.
