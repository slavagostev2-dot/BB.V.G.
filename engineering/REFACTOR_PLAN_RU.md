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

## Завершено 25 июля 2026 года — этап 1 нового рефакторинга

- создана неизменяемая контрольная ветка `backup/pre-refactor-20260725` от
  фактического `main` перед началом работ;
- создан `engineering/WHEEL_PIPELINE_BASELINE_RU.md` с фактической цепочкой
  поиска, классификации, уведомлений, lifecycle, автоучастия, recovery и
  финализации результата;
- добавлены scenario-регрессии текущей production-композиции и пути
  `active publication → initial notification → confirmed participation →
  two account outcomes → one Control Center result`;
- production-код, workflow, runtime JSON, encrypted state и release-marker на
  этом этапе не изменялись.

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
5. Контур колёс остаётся последовательной композицией нескольких runtime-слоёв.
   Его дальнейшее упрощение выполняется только после принятия baseline и
   отдельной инвентаризации моделей/полей без изменения поведения.

## Порядок следующего этапа

1. Использовать `engineering/WHEEL_PIPELINE_BASELINE_RU.md` как замороженный
   пользовательский и runtime-контракт.
2. Построить инвентарь полей одного wheel-event и результатов каждого аккаунта.
3. Ввести единые модели рядом с существующим кодом без переключения production.
4. Добавить преобразование текущего state в модели и обратно с regression-тестами.
5. Только после проверки моделей выбрать первую группу поведения для переноса в
   существующего предметного владельца.
6. Для каждого переноса заменять импорты и workflow, удаляя прежний путь в том же PR.
7. Выполнять полный pytest, production acceptance, preflight, security audit и
   exact-SHA Control Center validation.
8. После deploy проверять heartbeat и только затем переходить к следующей группе.
