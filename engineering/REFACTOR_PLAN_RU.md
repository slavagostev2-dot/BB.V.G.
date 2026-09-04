# BB V.G. — план очистки и рефакторинга

## Цель

Уменьшать количество параллельных реализаций и исторических файлов, не меняя
подтверждённые callback/state-контракты и production continuity. С 5 сентября
2026 года приоритет смещён с внутренней полировки на пользовательское
упрощение основного `BB.V.G.`: удачные идеи отдельного `BB-Wheel` переносятся
только как замена или упрощение существующего поведения, без второго runtime и
без копирования его серверной архитектуры.

## Продуктовый план с 5 сентября 2026 года

`BB.V.G.` остаётся единственным production-продуктом. `BB-Wheel` используется
только как архив проверенных идей и regression-сценариев.

Порядок работ:

1. **Telegram UI.** Сделать основной интерфейс компактным: единый `🎡 Колёса`,
   ручное добавление колеса, понятные `Источники`, `Настройки` и `Управление`.
   Убрать из основного UX рейтинг, лишнюю статистику, ручные lifecycle-действия
   и дублирующие экраны. Старые callback сохранять совместимыми, пока живут уже
   отправленные сообщения.
2. **Wheel core.** Не создавать второй pipeline. Уже выпущенная ручная подача
   колеса должна продолжать использовать существующую admin-action очередь и
   стандартный BetBoom/event lifecycle. Довести сохранённые `pending` и события
   без точного времени до фоновой перепроверки; при появлении точного времени
   отправлять одно отдельное Telegram-уведомление без повторного первичного
   уведомления/VK.
3. **Источники.** Упростить пользовательскую модель до списка действующих
   источников, ручного добавления и очереди найденных кандидатов. Существующий
   `source_intelligence.py` упрощать на месте, а не заменять новым discovery.
   Кандидат с реальным wheel-link имеет приоритет; нерешённый кандидат получает
   повторное напоминание через 24 часа.
4. **Referral.** Отдельным совместимым этапом перейти от
   `normal/referral/suspected_referral` к целевым `normal/referral_confirmed`.
   Telegram-текст после миграции не является доказательством referral; явный
   `referral_ineligible` BetBoom остаётся сильным подтверждением. До этого PR
   текущий production-контракт не ломать.
5. **Автоучастие.** Сохранить независимость трёх production-аккаунтов,
   монотонность success и один агрегированный итог. Затем постепенно свести
   account-specific реализации к одному generic orchestrator/registry без
   создания ещё одного параллельного worker.
6. **Уведомления.** Сократить пользовательские категории до действительно
   нужных wheel-событий; административно оставить найденные источники и общие
   проблемы. Существующий delivery ledger и at-most-once/durable semantics не
   заменять.
7. **Глубокая очистка.** Только после стабилизации предыдущих пользовательских
   этапов продолжать удаление compatibility/v2/v3 технического долга по одному
   владельцу за PR.

Для каждого функционального этапа обязательны: backup текущего production SHA,
end-to-end regression существующего пути до изменения, architecture guard,
targeted tests, полный `pytest`, все секции production acceptance, preflight,
security audit, exact-SHA validation, отдельный минимальный release commit и
проверка живых heartbeat после deploy.

Серверные механизмы `BB-Wheel` (`Docker/Compose` production, постоянная
серверная SQLite, OS-level lock, локальные auth JSON, локальный backup/restore)
не являются частью этого плана, пока `BB.V.G.` работает на GitHub Actions.

## Выполнено 26 июля 2026 года — durable event architecture

- production-dispatch использует branch/tag ref, а exact deployment SHA
  остаётся только идентификатором выполняемого кода;
- подтверждённые browser-results публикуются CAS-merge в `runtime-state`,
  который читает Control Center, без runtime-коммитов в `main`;

- введён единый `EventStore` и удалён GitHub `state.json` из синхронного пути
  dispatcher;
- event, dispatch outbox и notification outbox создаются одной SQLite
  транзакцией до внешнего уведомления;
- cursor recovery защищён от redirect/alias poisoning collector-каналов;
- reconciliation работает в production-цикле и восстанавливает пропущенные
  активные генерации;
- account results монотонны и разделены по `owner_id/account_key`;
- Monitor, Control Center и browser dispatch связаны exact-SHA deployment
  manifest;
- heartbeat больше не является deployment-коммитом в `main`.

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
  прежний fallback-токен уведомления `cba7abb40c5b77`;
- полная button-matrix и весь pytest проходят на чистом итоговом дереве без
  временных workflow, patch-скриптов и диагностических файлов.

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

## Инварианты следующего этапа

1. Каждый PR затрагивает только один предметный контур.
2. До изменения добавляется или подтверждается end-to-end regression текущего
   пользовательского пути.
3. Поведение переносится к существующему предметному владельцу; новая обёртка
   рядом со старой не допускается.
4. При замене старого поведения ненужная ветка/кнопка/обёртка удаляется в том же
   PR, если это не нарушает callback/state compatibility уже отправленных
   сообщений.
5. Любой старый callback, который ещё может находиться в Telegram-сообщении,
   либо продолжает работать, либо получает явный compatibility-route без нового
   владельца бизнес-логики.
6. Выполняются architecture guard, targeted tests, полный pytest, все секции
   production acceptance, preflight, security audit и exact-SHA validation.
7. Функциональный merge не считается deploy: после него выпускается отдельный
   минимальный release-marker commit и проверяются живые heartbeat/команды.
8. При production-регрессии выполняется откат на последний подтверждённый
   release/backup SHA; следующий этап не начинается до восстановления baseline.

## Выполнено 25 июля 2026 года — консолидация навигации

- кнопки навигации настроек, статуса и дополнительных разделов формируются предметными владельцами без post-render фильтрации;
- `admin_panel_runtime_v41.py` больше не подменяет `self.send` для удаления callback;
- построение компактного меню закреплено за `PanelInterfaceRuntime`;
- следующий этап не должен затрагивать личное участие и защищённую загрузку snapshot.
