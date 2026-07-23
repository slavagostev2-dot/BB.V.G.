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
- preflight запрещает возврат удалённых файлов и требует обязательные документы.
- release-marker стал единственным владельцем exact-SHA Control Center, а
  controlled recovery Monitor передан watchdog без self-retry при ошибке.

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

## Порядок следующего этапа

1. Зафиксировать чистый baseline и новый backup.
2. Для одной группы построить import/method/state inventory.
3. Перенести поведение в существующий предметный модуль.
4. Заменить импорты и workflow.
5. Добавить отрицательный контракт против возврата старого слоя.
6. Выполнить полный pytest, production acceptance, preflight, security audit и
   exact-SHA Control Center validation.
7. После deploy проверить heartbeat и только затем перейти к следующей группе.
