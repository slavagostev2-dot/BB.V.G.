# BB V.G. — журнал значимых изменений проекта

Этот документ читается после `AGENTS.md`. Он фиксирует изменения поведения, архитектуры, структуры, workflow, состояния и эксплуатации. Новые записи добавляются сверху.

Не добавляйте сюда автоматические heartbeat-коммиты, обычные сохранения runtime JSON и форматирование без изменения смысла.

---

## 2026-07-18 — Исправлен `KeyError` отчёта «Давно без колёс»

**Причина:** production `source_sets()` после переноса панели в стабильные
модули возвращает группу `quiet`, а `show_inactive_report()` продолжал читать
исторический ключ `inactive` через индекс словаря. Тест подменял `source_sets`
словарём со старым ключом и поэтому не воспроизводил production-путь.

**Что изменено:** экран использует актуальный `quiet` с безопасной
совместимостью для старого `inactive`; тест строит реальные fast/stats данные и
вызывает настоящий `source_sets()`. Callback, пагинация, расчёт семи дней и
формат состояния не менялись.

**Откат:** вернуть commit hotfix целиком; миграция данных не требуется.

## 2026-07-18 — Расширена основная проверка и отфильтрован шум разведки

**Причина:** прежняя разведка считала кандидатом почти каждое `@упоминание` и
ссылку `t.me` в известных каналах. Поэтому в находки попадали обычные
пользователи, служебные аккаунты и Telegram-боты, не имеющие отношения к
колёсам. Одновременно 79 уже проверенных публичных каналов из окружения
BetBoom/стримеров оставались вне основной проверки, хотя хотя бы косвенно могли
публиковать колёса.

**Что изменено:**

- 79 публичных косвенно релевантных каналов добавлены в
  `public_sources.txt`; основной inventory вырос с 78 до 157, ночной остался 3,
  общий — 160. Первые 26 были доступны в исходном state, ещё 53 подтвердились
  первым production-запуском нового тематического фильтра;
- разведка принимает ссылку только при тематическом контексте колёс/акций,
  ставок, стримов, киберспорта или поддерживаемых игр;
- username с обязательным Telegram bot-суффиксом `bot` отбрасывается даже при
  наличии тематических слов рядом;
- публичная страница кандидата дополнительно проверяется на собственные
  тематические публикации, а рейтинг больше не растёт только за факт
  упоминания и доступности страницы;
- старые результаты широкого regex сохранены в JSON для аудита, но помечаются
  `legacy_unclassified` и не показываются как новые находки;
- новый тематический публичный кандидат автоматически подхватывается ночным
  мониторингом сразу после успешной разведки; failed/cancelled run ничего не
  маршрутизирует, а только подтверждённое активное колесо повышает источник в
  основной режим;
- разведка теперь охватывает до 250 известных источников и проверяет до 150
  отфильтрованных кандидатов, поэтому расширенный inventory 160 не обрезается
  прежними лимитами 100/150;
- transport smoke переименован из исторического «66 источников», запускается
  при изменении любого tier-файла и после успешного ночного discovery, после
  завершения повторно вызывает health;
- успешный ночной discovery также перестраивает source registry, поэтому
  `[skip ci]` в runtime commit не оставляет новый ночной inventory в pending до
  следующего расписания;
- health сравнивает `sources_scanned` с `known_sources` того же intelligence
  run, а не с уже расширенным downstream inventory; полнота нового nightly
  пула отдельно подтверждается discovery и transport smoke;
- схема callback и существующие решения модерации не менялись; новые файлы не
  создавались.

**Pre-update backup:**
`backup/before-relevant-source-intelligence-expansion-2026-07-18` →
`e6dcf3e772344133f44551bf19f59eec470cab95`.

**Проверки до deploy:** compile изменённых Python-модулей; относящиеся и
полный набор из 140 pytest-тестов + 9 subtests, включая отсечение bot/noise,
отбор ночных кандидатов и маршруты панели; self-test, production acceptance,
security audit и preflight на первом этапе из 107 источников прошли. Первый
production heartbeat подтвердил полный проход 104/104 за 7 секунд без ошибок;
итоговый inventory 157 + 3 и его heartbeat фиксируются после второй публикации.

**Production:** изменение развёрнуто последовательными зелёными PR #74–#79:
`f347cfd5c0e5bc165bebd1eac3836b3240126754`,
`511cbb9a1653bea470873934a05ee3a3ec3d8116`,
`077f0922183d17386bea76695677e0fdc3fd8b0a`,
`35afeaeb299e39a50e61ba3687b0e21b3394e711`,
`57cf9502d4c7cc21e7aaf3737bfc422a8daefb61` и
`cd05ba6c4047aa8823cb62b138844e9802dc01df`.

Финальная живая проверка подтвердила:

- основной монитор — 157/157, 0 ошибок, полный проход 12 секунд;
- intelligence — 160/160 известных на старте, 0 ошибок, 184 тематических
  кандидата, 150 проверенных страниц;
- nightly discovery — 59 источников, из них 56 добавлены автоматически,
  0 ошибок и 0 повышений без активного колеса;
- transport smoke — 216/216 доступных, 0 ошибок;
- source registry — 216 available, 0 pending, 0 unavailable;
- system health — `ok`, findings отсутствуют; панель — `running`.

**Post-update backup:**
`backup/after-relevant-source-intelligence-expansion-2026-07-18` →
`4c7cc2b440b69afb9a9b17697e3e9b9a4ca2ec69`. Ref идентичен подтверждённому
production main на момент создания. Ротация оставила ровно три ветки: этот
post-backup, `backup/before-relevant-source-intelligence-expansion-2026-07-18`
и `backup/after-participation-reminder-active-cleanup-2026-07-18`.

**Откат:** вернуть commits изменения целиком либо перейти на pre-update backup;
миграции состояния нет, исторические intelligence-записи не удаляются.

## 2026-07-18 — Устранены ложные напоминания и зависшие завершённые колёса

**Причина:** панель сохраняла личное участие в encrypted state и одновременно
ставила privacy-safe команду рейтинга в очередь. Монитор применял команду до
напоминаний, но фильтр напоминаний читал только локальную копию encrypted state,
которая могла ещё не содержать новую отметку. Активный список дополнительно
показывал колесо до 30 минут после deadline, даже когда production lifecycle уже
считал событие завершённым.

**Что изменено:**

- фильтр напоминаний использует актуальный `personal_wheel_votes` текущего цикла
  и сопоставляет пользователя по HMAC actor-token и точному event key;
- голос старого `action_id`/поколения не подавляет напоминание нового события;
- active-list renderer сразу исключает наступивший deadline и terminal
  `closed`/`finished`/`inactive` status;
- участие пользователя не влияет на срок присутствия колеса в активном списке;
- callback strings, схема encrypted state и очередь команд не менялись; новые
  runtime-файлы не создавались.

**Pre-update backup:**
`backup/before-participation-reminder-active-cleanup-2026-07-18` →
`f8864eca5cbfff911dbfc44f7e2c40c9c8c899bd`.

**Проверки до deploy:** compile изменённых модулей, 135 pytest tests и 9
subtests, panel/self-test, production acceptance, security audit и preflight
81 источника (78 primary, 3 nightly).

**Production:** PR #72 слит squash-коммитом
`6b6e33f275a92acf2db7983197ba0d55575133f8`. Панель и монитор запущены на
его descendant `75ce8c0bc3fef7300c0aa8136c2662d28a06d821`; первая итерация проверила
78 из 78 primary-источников без ошибок. Завершённое `ewc1` исчезло из
`active_wheels`, в списке осталось только событие с будущим deadline.

**Post-update backup:**
`backup/after-participation-reminder-active-cleanup-2026-07-18` →
`e965ddc60240f802b2d1ac56aed58952c13367be`. Автоматическая ротация оставила
ровно три обычные backup-ветки: этот post-backup,
`backup/before-participation-reminder-active-cleanup-2026-07-18` и
`backup/after-analytics-intelligence-repair-2026-07-18`.

**Откат:** вернуть commit исправления целиком либо перейти на pre-update backup;
миграция состояния не требуется.

## 2026-07-18 — Восстановлены находки аналитики и разведка источников

**Причина:** два последовательно вызываемых loader статистики использовали
разные rating epoch (`2026-07-14` и `2026-07-17`). Каждый цикл монитора
переключал значение туда-обратно и повторно удалял `wheel_posts`, поэтому
аналитика показывала ноль находок. После главы 5 в стабильный MRO также не были
перенесены `show_intelligence`, detail/action methods и маршруты `intel:*`:
кнопка «Разведка источников» оставалась в меню, но неизвестная страница
возвращала пользователя на главный экран.

**Что изменено:**

- оба loader используют единый production epoch `2026-07-17`, повторная
  загрузка больше не удаляет новые счётчики;
- один раз восстанавливаются точные доступные находки после epoch из
  уникальных `recent_post_keys` и сохранённых `seen_at`; другие метрики и
  timestamps не пересчитываются приблизительно;
- экран разведки, списки «Новые находки»/«С колёсами», карточка кандидата и
  действия кандидата перенесены в стабильный `bbvg/bot/interface.py`;
- `page:intelligence`, `intel:list:*`, `intel:detail:*`, mode/ignore/restore
  снова маршрутизируются и проверяют роль администратора;
- versioned runtime, callback strings, схема приватного состояния и порядок
  кнопок не менялись; новые файлы не создавались.

**Pre-update backup:**
`backup/before-analytics-intelligence-repair-2026-07-18` →
`32fa6fb1f3212ef0facf3a2310e0236b8f06b393`.

**Production:** PR #70 слит commit
`f84a53e7a1bfd012a76d87f12f1c43c49f4ea62d`. На четвёртом непрерывном
цикле монитор остался `running`, проверил 78 из 78 primary-источников без
ошибок и не повторил восстановление. Счётчик находок сохранился и вырос с 22
до 23: 16 за `2026-07-17` и 7 за `2026-07-18`; health остался `ok`.

**Post-update backup:**
`backup/after-analytics-intelligence-repair-2026-07-18` →
`3a66cd56d74c4977db1cbffc758fa94c572cf8b4`. Автоматическая ротация оставила
ровно три обычные backup-ветки: этот post-backup,
`backup/after-wheel-generation-observations-2026-07-18` и
`backup/before-analytics-intelligence-repair-2026-07-18`.

**Откат:** вернуть commit исправления целиком либо перейти на pre-update
backup. Восстановленные `wheel_posts` получены из уже существующих уникальных
ключей публикаций и остаются валидными данными.

## 2026-07-18 — Включён журнал повторных поколений колеса

**Причина:** нужно проверить production-гипотезу, что BetBoom
может несколько раз за день запускать новые колёса по одной
ссылке и повторно использовать `action_id`. Сразу менять
дедупликацию нельзя: поздний репост старого колеса можно
ошибочно принять за новое.

**Что изменено:**

- в существующем `state.json` введён аддитивный
  `wheel_generation_observations`, новый JSON-файл не создаётся;
- для каждой комбинации wheel key, `action_id` и `start_dttm`
  хранятся first/last seen, счётчик и API-статусы;
- отдельно фиксируются отсутствующие ID или время старта;
- retention ограничен 14 днями и 1000 уникальными
  идентичностями; Telegram ID, chat ID и тексты сообщений не
  записываются;
- `--observation-report state.json` показывает одинаковые ID
  с разными стартами, смену ID на одной ссылке и пробелы
  серверной идентичности;
- журнал не влияет на текущую логику уведомлений,
  участия, рейтинга и дедупликации.

**Что проверить 21.07.2026:** есть ли записи в
`same_action_id_multiple_starts`; сколько ссылок попало в
`same_link_multiple_action_ids`; есть ли длительные
`missing_server_identity`; совпадают ли кандидаты с реальными
новыми Telegram-публикациями после завершения прежнего
колеса. Только после этой сверки решать, нужен ли
дополнительный time-based fallback.

**Pre-update backup:**
`backup/before-wheel-generation-observations-2026-07-18` →
`d589f10db710a9b1466bf35287f5c4e396286800`; rotation оставила
ровно три backup-ветки.

**Production:** PR #68 слит commit
`7f97164dc953c81af91897aef7e8a68182a014dc`. Первый живой цикл
записал три обезличенные идентичности по трём ссылкам без
пропусков `action_id`/`start_dttm`; повторный ID с новым стартом
на первой выборке не обнаружен. `monitor_status.json` сообщил
`running`, 78 проверенных primary-источников и production
`head_sha`, включающий deploy commit.

**Post-update backup:**
`backup/after-wheel-generation-observations-2026-07-18` →
`806f0e3728311bce72b82ca91ec9ea3371520b6d`. После автоматической
ротации подтверждены ровно три обычные backup-ветки:
`backup/after-chapter5-panel-architecture-2026-07-18`,
`backup/before-wheel-generation-observations-2026-07-18` и
`backup/after-wheel-generation-observations-2026-07-18`.

**Откат:** вернуть commit целиком или перейти на pre-update
backup; добавочное поле может быть проигнорировано старым
кодом без отдельной миграции.

## 2026-07-18 — Глава 5: production-панель освобождена от versioned MRO

**Причина:** фактическое поведение Telegram-панели зависело от
точного порядка 32 классов, включая 18 versioned runtime-слоёв.
Одинаковые `handle_callback`, `render_page` и UI-методы многократно
переопределялись, а CI проверял исторические классы вместо
реального production runtime.

**Что изменено:**

- эффективные экраны, матрица меню, role checks, callback routing,
  callback-token до 64 байт, пагинация, notification policy, heartbeat и
  очередь admin actions перенесены в устойчивые `bbvg/bot/*.py`;
- `bbvg.bot.runtime.TelegramPanelRuntime` сокращён с 32 до 14 классов
  MRO; в production MRO больше нет ни одного `admin_panel_runtime_v*`;
- `admin_panel_runtime_v41.py` сохранён как тонкий production-переходник;
  остальные versioned-файлы не удалялись и ждут доказанного
  удаления в главе 9;
- validation workflow, full pytest и production acceptance теперь проверяют
  финальный `bbvg.bot.runtime`, его реальных владельцев методов
  и отсутствие versioned MRO;
- callback strings, encrypted state format, роли, порядок кнопок и
  функционал монитора не изменены.

**Файлы:** изменены только существующие `bbvg/bot/*.py`, тесты,
действующие validation workflow, `AGENTS.md` и этот журнал. Новые
постоянные файлы не создавались, файлы не удалялись, схема
состояния и миграции не затрагивались.

**Pre-update backup:**
`backup/before-chapter5-panel-architecture-2026-07-18` →
`81b3299847ea6677b9bda2585bca141dc6517ed1`; rotation workflow
`29638871568` проверил ref и оставил ровно три обычных
backup-ветки.

**Проверки:** baseline — `123 passed, 9 subtests`; после
рефакторинга — `125 passed, 9 subtests`; production acceptance — `all`;
самопроверки foundation, storage, users, runtime и v41 adapter,
security audit, preflight `78 primary + 3 nightly` и YAML parse прошли.
Первый CI выявил скрытый side effect v36: импорт устанавливал
HMAC notification policy. Установка перенесена в стабильный
runtime; повторно успешны все пять checks: runs `29639865583`,
`29639865630`, `29639865610`, `29639865585`, `29639865598`.
PR #66 слит squash-коммитом
`1c73abcd2b77f9f8b9b993d1b35de11fb7bf4142`.

**Production cutover:** control-center run `29639903231` успешно выполнил
validation, сверку role fingerprint, проверку encrypted state и запись
статуса. Свежий heartbeat `2026-07-18T09:53:18.984133+00:00`
подтверждает `status=running`, version 41, dedicated state key и один
consumer `getUpdates`; checkout SHA `3be20e46...` является прямым
потомком result SHA и отличается только diagnostic state.
System health — `ok`, findings `0`, active incidents `0`; монитор
остался `running`, `78/78` primary, `3` nightly, source errors `0`.

**Post-deploy backup:**
`backup/after-chapter5-panel-architecture-2026-07-18` →
`ccd8a8fd44f5f15b16659acb3e665ef724c38669`. Ротация оставила
ровно три проверенные ветки: post-update, pre-update и
`backup/after-chapter4-state-concurrency-2026-07-18`; прежний pre-chapter4
backup удалён после ancestry-проверки.

**Откат:** вернуть merge commit главы 5 целиком либо перейти на
`backup/before-chapter5-panel-architecture-2026-07-18`; state migration не требуется.

## 2026-07-18 — Глава 4: состояние, атомарность и параллельные записи

**Причина:** authoritative JSON сохранялись разными локальными реализациями,
`source_intelligence`, discovery и source tier использовали обычный
`write_text`, два workflow могли одновременно менять оба tier-файла, локальная
копия encrypted bundle после успешного remote CAS писалась неатомарно, а
notification claim защищал только потоки одного процесса.

**Что изменено:**

- все 28 отслеживаемых JSON получили машинно-проверяемые category, owner и
  schema contract в существующем `monitor_data.py`; полная ownership matrix
  закреплена в `AGENTS.md`;
- добавлена единая durable atomic replacement для monitor, health, incident,
  registry, transport, intelligence, discovery и tier state: временный файл в
  том же каталоге, flush, `fsync`, `os.replace`, очистка temp после сбоя;
- encrypted bundle после remote Contents API CAS теперь сохраняется локально
  через атомарную замену; three-way merge сохраняет роли, удаление пользователя,
  concurrent registration и независимые заявки;
- `notification_delivery_state` читает legacy v2 и мигрируется в v3 без потери
  delivery entries; v3 хранит ограниченные expiring claims под межпроцессным
  file lock, поэтому два процесса не отправляют одно сообщение, а аварийная
  claim автоматически освобождается по TTL;
- admin panel больше не коммитит delivery ledger при startup migration: remote
  commit принадлежит монитору; key rotation и encrypted panel writer используют
  одну concurrency-группу;
- nightly discovery и source-tier maintenance сериализованы общей группой
  `bb-vg-source-catalog-writer`, потому что оба владеют
  `public_sources.txt`/`source_catalog.txt`;
- сохранены Contents API CAS/retry очереди на HTTP 409/422, идемпотентный
  `command_id` и bounded cleanup queue/applied/results;
- добавлены behavioral tests для crash-before-replace, truncated temp, wrong
  key, deletion+addition three-way merge, 409+422 CAS, bounded queue,
  interprocess claim и полного inventory JSON.

**Изменённые файлы:** существующие storage/runtime-модули,
`tests/test_concurrency_and_ci.py`, `tests/test_notifications.py`, связанные
validation и state-writing workflow, `AGENTS.md`, этот журнал. Новые постоянные
файлы не создавались; callback и wheel event identity не менялись.

**Schema migration:** `notification_delivery_state.json` v2 → v3 и
`discovery_state.json` v1 → v2 выполняются повторяемо действующими owners; старые
версии читаются до первой успешной записи. Wheel state v6, encrypted bundle v2,
пользователи, роли, заявки, статистика и delivery entries сохраняются.

**Pre-update backup:**
`backup/before-chapter4-state-concurrency-2026-07-18` →
`ab0bdf47259d680dcd8696108c993b97b54fe67e`; ref проверен как предок `main`.
Ротация оставила ровно три ветки и удалила прежнюю
`backup/after-analytics-multisource-routing-2026-07-18` только после ancestry
проверки.

**Проверки:** baseline `114 passed`; профильный compile и
concurrency/notification suite — `23 passed`; финальный локальный pytest —
`123 passed`, production acceptance, security audit и preflight (`81 approved:
78 primary, 3 nightly`) успешны. PR #63 прошёл шесть обязательных checks
(runs `29636198716`, `29636198689`, `29636198694`, `29636198687`,
`29636198691`, `29636198699`) и слит как
`6229e088899c0fa3d77d0267fbffd2c80623ba46`.

**Production cutover:** monitor run `29636234832` подтвердил `78/78` доступных
primary-источников, `0` ошибок и свежий heartbeat. Ledger повторяемо мигрировал
из v2 в v3: сохранены все 75 неистёкших delivery entries, две записи старше
окна retention штатно удалены, активных claims после cutover нет. Discovery
state мигрировал v1 → v2. Encrypted bundle имеет тот же Git blob
`2aa1875527a6e0ad50f5ba941f8084b0aeb2e0f0`; размеры всех коллекций wheel
state v6, source stats, admin queue и moderation до/после совпали.

Первый production run панели обнаружил ещё один ownership-конфликт: panel
выполняла `notification_integrity_v2.py --prune`, оставляла ledger
незакоммиченным и не могла сделать rebase при конкурентной записи статуса.
Hotfix оставил monitor единственным writer ledger, закрепил это тестом, прошёл
пять checks (runs `29636373900`, `29636373879`, `29636373863`, `29636373864`,
`29636373874`) и слит как
`700951a7acc428c603d4966701bf79a9411f0f80`. Panel run `29636395542`
возобновлён с точным production SHA и свежим heartbeat; system health — `ok`,
findings `0`.

**Post-deploy backup:**
`backup/after-chapter4-state-concurrency-2026-07-18` →
`d31ed72926381972e5d9374d4fda2f84c83e6ae3`. Автоматическая rotation оставила
ровно три backup-ветки: post-update, pre-update и
`backup/after-wheel-generation-2026-07-18`; самая старая
`backup/before-wheel-generation-2026-07-18` удалена.

**Откат:** вернуть merge commit главы 4 либо перейти на pre-update backup.
Если cutover ledger уже выполнен, v3 обратно совместим по delivery entries, но
предпочтителен полный откат к backup вместо ручной замены отдельных JSON.

## 2026-07-18 — Восстановлены кнопки панели после обновления аналитики

**Причина:** расширение аналитики заменило целиком клавиатуры экранов аналитики и источников. Из-за этого прежние действия изменили порядок, а на экране источников исчезли обновление реестра, рейтинг, ночное наблюдение, разведка и предложение источника.

**Что изменено:**

- экран аналитики снова использует прежний порядок периодов 1/7/30 дней и сохраняет административный переход к давно неактивным источникам;
- экран источников снова показывает прежний role-aware набор кнопок: обновление реестра и рейтинг для всех, основные источники и предложение источника для пользователя, ночное наблюдение, разведку и добавление источника для администратора;
- новые расчёты аналитики, multi-source учёт и обновлённый текст экранов не откатываются;
- добавлены точные тесты состава и порядка кнопок для пользователя и администратора.

**Изменённые файлы:** `bbvg/bot/runtime.py`, `tests/test_button_matrix.py`, `docs/PROJECT_CHANGELOG_RU.md`. Новых файлов и миграций состояния нет.

**Точка сравнения:** pre-update backup `backup/before-analytics-multisource-2026-07-18` → `d6a04a4c5e2f8bc4d4277b8a7f480472294024c5`.

**Откат:** вернуть коммит этой правки; данные и схема состояния не затрагиваются.

## 2026-07-18 — Расширена аналитика и исправлен multi-source рейтинг

**Причина:** аналитика показывала только несколько базовых счётчиков, экран источников терял `source_registry.generated_at`, а canonical-message rewrite заменял исходный канал публикации. Поэтому повтор колеса в другом канале подавлялся как уведомление правильно, но второй канал мог не сохраниться и не получить рейтинг. Production-пример: `zonertg8` (`action_id=693`) был опубликован в `mechanogun` и `kolesaBB`, однако три голоса на 11 очков сохранились только у первого источника.

**Что изменено:**

- аналитика за 1/7/30 дней показывает публикации, источники, уведомления, повторы, ошибки, среднее и лучший день, топ каналов, голоса и очки, лидера рейтинга, активные multi-source события, последнюю находку и покрытие реестра;
- экран источников показывает состояние реестра и фактическое время его обновления;
- source streams сохраняют настоящий Telegram-канал каждого поста, а canonical publication используется только для API, таймера и единственного уведомления;
- поздно найденный второй источник идемпотентно добавляется ко всем уже существующим голосам события без второго голоса пользователя и без двойного начисления;
- рейтинг объясняет актуальные веса: пользователь 1, администратор или владелец 5 каждому уникальному источнику события.

**Изменённые файлы:** `monitor_entry.py`, `personal_wheel_voting.py`, `bbvg_monitor_main.py`, `bbvg/bot/sources.py`, `bbvg/bot/runtime.py`, `tests/test_lifecycle.py`, `tests/test_personal_wheel_voting.py`, `docs/PROJECT_CHANGELOG_RU.md`. Новых постоянных файлов не создаётся; временные apply-файлы удаляются до merge. Callback и приватное состояние не меняются.

**Pre-update backup:** `backup/before-analytics-multisource-2026-07-18` → `d6a04a4c5e2f8bc4d4277b8a7f480472294024c5`.

**Откат:** вернуть merge commit целиком либо перейти на pre-update backup. Уже начисленные корректные очки вторым источникам остаются валидными данными.

## 2026-07-17 — Глава 3: функциональные контракты и health привязаны к текущему inventory

**Причина:** системная диагностика продолжала использовать исторические пороги 66/77 и могла держать critical incident при живом мониторе и уже успешной транспортной проверке полного пула. Двухчасовое окно без таймера одновременно задавалось как 24 часа в monitor/workflow и исправлялось monkey-install до 2 часов. Единственный `getUpdates` consumer проверялся поиском строки, а не поведением. Дополнительно BetBoom возвращал `action_id` и `duration_min` для ещё не запущенной акции без `start_dttm`, а notification-first слой ошибочно превращал такой ответ в активную карточку.

**Что изменено:**

- двухчасовой untimed contract закреплён непосредственно в monitor; workflow больше не переопределяют `UNKNOWN_DEDUP_HOURS`; lifecycle проверяет контракт и не мутирует его при install;
- historical `EXPECTED_SOURCE_COUNT` удалён из runtime, preflight, validation и workflow; health вычисляет текущий authoritative inventory из primary/nightly списков;
- transport smoke сверяет status/domain, точные primary/nightly/total counts, missing и error sources; сообщения больше не содержат устаревшее число 66;
- registry/transport интерпретируются как полный пул, а monitor heartbeat — как primary-проверка; source-health matrix больше не смешивает transport и tier findings;
- behavioral test запускает monitor feedback и production panel с fake Telegram API: monitor не вызывает `getUpdates`, панель является единственным consumer;
- очередь проверена для каждого поддержанного administrative action: повтор того же `command_id` не меняет state, health и rating;
- frozen-time fixture доказывает открытие transport incidents при плохом снимке и их закрытие после свежего точного inventory;
- ответ API с настроенной длительностью, но без `start_dttm`, теперь считается ещё не запущенной акцией: уведомление и активная карточка не создаются, а ранее ошибочно добавленная карточка удаляется вместе с участием и suppression-state и переводится в тихую повторную проверку;
- существующие сценарии доказывают Telegram-post dedup, timer/manual/2h, reused URL, очистку участия/публикаций при новом event, один rating на source/event, новый rating для нового action, reminders, draw notification и persistent delivery ledger без массовой отправки.

**Изменённые файлы:** `monitor.py`, `monitor_entry.py`, `bbvg_monitor_runtime.py`, `wheel_link_lifecycle.py`, `system_checks.py`, `preflight.py`, `monitor_validation_v41.py`, действующие `.github/workflows/*.yml`, `tests/test_chapter3_contracts.py`, `tests/test_lifecycle.py`, `tests/production_acceptance.py`, `AGENTS.md`, `docs/PROJECT_CHANGELOG_RU.md`. Создан только `tests/test_chapter3_contracts.py` как устойчивый behavioral-контракт главы; временный audit workflow удалён до merge. Runtime JSON и форматы callback не меняются PR.

**Pre-update backup:** `backup/before-chapter3-contracts-health-2026-07-17` → `27e356d48378963c4e44af76fe32bff8367fb10b`.

**Проверки до PR:** targeted behavioral suite — 70 tests и 9 subtests; полный pytest — 106 tests и 9 subtests; `preflight.py`, `monitor_validation_v41.py`, system self-tests и `tests/production_acceptance.py --section all` успешны. Финальные CI run IDs, result SHA, live health и post-update backup фиксируются после deploy.

**Диагностика ложной карточки:** run `29591043004` подтвердил ответ HTTP 200 с `action_id=878`, `duration_min=15`, `is_ended=false`, но без `start_dttm`; именно отсутствие проверки этого сочетания было причиной попадания неработающего колеса в активные.

**Откат:** вернуть merge commit главы 3 целиком либо перейти на `backup/before-chapter3-contracts-health-2026-07-17`; state migration не требуется.

## 2026-07-17 — Глава 2: GitHub Actions закреплены за exact SHA и минимальными правами

**Причина:** production и validation workflow использовали moving action tags, часть read-only checkout сохраняла Git credentials, права записи выдавались всему workflow, а heartbeat не показывал точный выполняемый commit. Ротация трёх backup была встроена в YAML без безопасного dry-run и изолированного тестового контракта.

**Что изменено:**

- все используемые `actions/checkout`, `actions/setup-python` и `actions/upload-artifact` закреплены полными 40-символьными SHA с комментариями проверенных версий;
- validation workflow checkout выполняется по exact event SHA и сверяет именно его, а read-only checkout использует `persist-credentials: false`;
- `contents: write` оставлен только конкретным state/queue writer jobs, `actions: write` — только jobs, выполняющим dispatch/restart; лишние права убраны у source migration и transport activation;
- long-running production и maintenance jobs по-прежнему явно используют `ref: main`;
- heartbeat панели и монитора записывает `head_sha`, `workflow_run_id` и `run_attempt`, сохраняя совместимое `run_id`;
- создан устойчивый модуль `backup_rotation.py`: он проверяет namespace, inventory, ancestry и отсутствие уникальных commits до любого удаления, поддерживает dry-run и оставляет три последние валидные обычные backup-точки;
- manual запуск backup workflow по умолчанию является dry-run, а create/push новой backup-ветки, schedule и merged workflow change выполняют реальную ротацию в единственной concurrency-группе;
- fixture-контракт покрывает 0/1/2/3/4 refs, failed verification без удаления, idempotency, dry-run и запрет удаления вне `backup/*`.

**Action provenance:** `actions/checkout` v4.2.2 → `11bd71901bbe5b1630ceea73d27597364c9af683`; `actions/setup-python` v5.6.0 → `a26af69be951a213d495a4c3e4e4022e16d87065`; `actions/upload-artifact` v4.6.2 → `ea165f8d65b6e75b540449e92b4886f43607fa02`.

**Изменённые файлы:** действующие `.github/workflows/*.yml`, `monitor_health.py`, `monitor_shift_v41.sh`, `AGENTS.md`, `docs/PROJECT_CHANGELOG_RU.md`. Созданы `backup_rotation.py` как единственный владелец политики ротации и `tests/test_actions_security.py` как offline-контракт Actions. Runtime JSON, callback и concurrency state writers не менялись.

**Pre-update backup:** `backup/before-chapter2-actions-hardening-2026-07-17`, SHA `be8421d29f2d4d688ee22eeeac2c9ce5b5ba3589`; ref проверен как точное совпадение baseline.

**Проверки:** до PR прошли YAML parse всех workflow, `py_compile`, `backup_rotation.py --self-test`, `monitor_health.py --self-test`, shell syntax и профильный pytest-контракт. На final head PR №48 успешны runs `29579368810`, `29579368732`, `29579368716`, `29579368718`, `29579368737` и rotation-contract `29579368709`; полный pytest и production acceptance входят в обязательный current-check. Live heartbeat и post-update backup проверяются после merge.

**Откат:** вернуть merge commit главы 2 целиком; при необходимости перейти на `backup/before-chapter2-actions-hardening-2026-07-17`. State migration не требуется.

## 2026-07-17 — Диагностика рейтинга переведена на журнал личных голосов

**Причина:** после перехода на политику `personal_votes_v1` системная диагностика продолжала вычислять ожидаемый рейтинг только из `admin_wheel_decisions`. Поэтому корректные 11 очков от владельца, администратора и пользователя ошибочно определялись как одно несовпадение с отсутствующими административными решениями.

**Что изменено:**

- при политике `personal_votes_v1` ожидаемый рейтинг каждого источника восстанавливается из `personal_wheel_votes`;
- каждая запись проходит тот же контракт ролей, весов и HMAC-псевдонима, что и production-начисление;
- владелец и администратор учитываются по 5 очков, пользователь — 1 очко, каждый источник события учитывается один раз;
- прежняя проверка подтверждённых административных решений по 40 очков сохранена как совместимость для legacy-состояния;
- ложный инцидент `rating_score_mismatch` закрывается после следующего системного прогона, но настоящее расхождение журнала и `quality_score` остаётся критическим;
- self-test проверяет правильную сумму `5 + 5 + 1 = 11`, обнаружение настоящего расхождения и legacy-сценарий на 40 очков.

**Изменённые файлы:** `system_checks_v2.py`, `docs/PROJECT_CHANGELOG_RU.md`. Новые постоянные файлы не создавались; runtime JSON и форматы callback не изменялись.

**Pre-update backup:** `backup/before-personal-rating-diagnostic-fix-2026-07-17`, SHA `b974eac5dbe63a03e5cac93692a6f28e10bc0ab3`.

**Откат:** вернуть commit исправления целиком; рейтинг и личные отметки пользователей восстанавливать не требуется, поскольку изменение затрагивает только проверку диагностики.

## 2026-07-17 — Удалено периодическое уведомление о штатной работе монитора

**Причина:** отчёт «BB V.G. работает» не содержал полезного действия и засорял чат при штатной проверке источников.

**Что изменено:** автоматический двенадцатичасовой status-report больше не отправляется. Уведомления о найденных колёсах, напоминания, ручная проверка и сообщения об ошибках сохраняются. Состояние монитора по-прежнему доступно через интерфейс бота.

**Изменённые файлы:** `bbvg_monitor_main.py`, `docs/PROJECT_CHANGELOG_RU.md`. Новые постоянные файлы не создавались.

**Откат:** вернуть commit изменения целиком.

## 2026-07-17 — Глава 1: административные Telegram ID удалены из публичного runtime state

**Причина:** `state.json`, `source_stats.json` и `candidate_moderation.json` сохраняли Telegram ID администратора в технических provenance-полях. Эти значения не нужны для функциональной идентичности и не должны публиковаться в Git.

**Что изменено:**

- `marked_by`, `confirmed_finished_by`, `admin_wheel_decisions.*.actor` и `ignored_by` теперь сохраняют только значение `admin`;
- текущие три JSON-файла мигрируются атомарно без изменения wheel keys, rating event keys, времён, счётчиков и прочих данных;
- личные голоса продолжают использовать существующий стабильный HMAC-псевдоним формата `vote_*`;
- `security_audit.py --current` проверяет все три публичных runtime-файла и отклоняет raw provenance или некорректный actor token;
- `security_audit.py --migrate-current` выполняет повторяемую безопасную миграцию.

**Encrypted backup gate:** run `29551631560`, artifact `bbvg-encrypted-state-29551631560`, source SHA `12785cfcfc6353b76b862e599d23e8aa5e5acab2`. Проверены расшифровка только текущим production `BOT_STATE_KEY`, exact SHA, encrypted SHA-256, HMAC state fingerprint, restore smoke и обезличенные aggregates. Artifact хранится вне public Git refs.

**Изменённые файлы:** `.github/workflows/bot-state-backup.yml`, `wheel_lifecycle_v2.py`, `monitor_data.py`, `admin_action_v3.py`, `admin_panel_runtime_v5.py`, `security_audit.py`, `state.json`, `source_stats.json`, `candidate_moderation.json`, `tests/test_lifecycle.py`, `docs/PROJECT_CHANGELOG_RU.md`. Новые файлы не создавались.

**Откат:** вернуть merge commit главы 1 целиком и восстановить encrypted state из artifact run `29551631560`; ручное восстановление отдельных полей не требуется.

## 2026-07-17 — Технические разделы скрыты от обычных пользователей

**Причина:** обычным пользователям не нужны внутренние сведения о production API, legacy checker и отключённых административных механизмах. Кнопка состояния системы должна находиться внутри настроек, а не занимать место в главном меню.

**Что изменено:**

- кнопка «Работа системы» удалена из главного меню и перенесена в раздел «Настройки» для всех ролей;
- обычные пользователи больше не видят кнопки «API и Legacy» и «Отключённый функционал»;
- администраторы и владелец сохраняют доступ к обоим техническим разделам внутри настроек;
- старые callback технических разделов для обычного пользователя безопасно возвращают его в настройки;
- из текста настроек для всех ролей удалена строка «Проверка активных колёс выполняется через BetBoom API.»;
- callback-данные действующих разделов и формат пользовательского состояния не изменялись.

**Изменённые файлы:** `bbvg/bot/runtime.py`, `docs/PROJECT_CHANGELOG_RU.md`. Новые файлы не создавались.

**Pre-update backup:** `backup/before-settings-role-cleanup-2026-07-17`, SHA `8316ee3bb63894f4a8d2262a66577fc4ad3cbaac`.

**Проверка:** self-test текущего runtime проверяет главное меню и настройки обычного пользователя и администратора, отсутствие удалённой строки и недоступность технических разделов для обычной роли. Дополнительно обязательны полный PR CI, production acceptance и свежий heartbeat панели после merge.

## 2026-07-17 — Рейтинг источников сброшен и начинается с нуля

**Причина:** владелец потребовал обнулить весь публичный рейтинг и начать новый отсчёт с 17 июля 2026 года.

**Что изменено:**

- перед первой итерацией monitor shift выполняется одноразовый идемпотентный сброс рейтинга с epoch `2026-07-17` в часовом поясе `Asia/Barnaul`;
- удаляются прежние очки, решения администратора, личные голоса, количества найденных колёс и связанные дневные рейтинговые показатели;
- очищаются top-level журналы `admin_wheel_decisions` и `personal_wheel_votes`, чтобы новый рейтинг не зависел от старой эпохи;
- сохраняются эксплуатационные счётчики проверок, число просмотренных сообщений, список источников, дедупликация Telegram-постов, активное состояние колёс и пользовательские данные;
- после сброса продолжает действовать политика `personal_votes_v1`: пользователь даёт каждому источнику 1 очко, администратор или владелец — 5 очков;
- `SOURCE_RATING_EPOCH_DAY=2026-07-17` экспортируется для всех итераций монитора, поэтому прежняя epoch `2026-07-14` больше не восстанавливается.

**Изменённые файлы:** `monitor_shift_v41.sh`, `docs/PROJECT_CHANGELOG_RU.md`. Новые файлы не создавались. `source_stats.json` обновляется самой первой production-итерацией и сохраняется обычным runtime commit.

**Pre-update backup:** `backup/before-rating-reset-2026-07-17`, SHA `a42291e29f526c4bc00535d579d110a6baddf0a8`.

**Проверка:** обязательны полный PR CI, успешная production-итерация, подтверждение `source_rating_epoch_day=2026-07-17`, отсутствие рейтинговых полей со старыми значениями и свежие heartbeat панели и монитора.

## 2026-07-16 — Участие сделано личным, а рейтинг привязан к пользователю и action_id

**Причина:** кнопка «Участвую» у администратора меняла общее состояние колеса и могла влиять на напоминания других пользователей. Общие действия «Завершено», «Неактивное» и пользовательское скрытие также конфликтовали с авторитетной повторной BetBoom API-проверкой.

**Что изменено:**

- «Участвую» всегда сохраняет личную отметку нажавшего пользователя, включая администратора и владельца;
- участие хранится по идентичности события: при наличии `action_id` отметка относится только к этому `action_id` и не переносится на новую акцию той же ссылки;
- голос пользователя даёт каждому уникальному источнику колеса 1 очко, голос администратора или владельца — 5 очков;
- один HMAC-псевдоним учитывается один раз для одного события; повторное нажатие не дублирует рейтинг и не раскрывает Telegram ID в публичной очереди;
- если колесо найдено в нескольких каналах, очки начисляются каждому источнику;
- финальное напоминание подавляется только для конкретного пользователя, который участвует именно в текущем событии;
- в актуальном API-интерфейсе удалены общие кнопки «Завершено», «Неактивное» и пользовательское скрытие; старые callback отвечают безопасным сообщением и не меняют общее состояние;
- администратору сохранён ручной ввод времени, а удаление активного колеса выполняет только автоматическая BetBoom API-проверка;
- активный список показывает краткую карточку со всеми источниками и личным состоянием участия;
- добавлены разделы настроек «API и Legacy» и «Отключённый функционал» с явным описанием конфликтов и безопасного возврата архивного checker отдельным deploy;
- существующий порядок уведомления о наступлении времени до автоматической очистки колеса сохранён.

**Новый модуль:** `personal_wheel_voting.py` объединяет одну устойчивую сквозную ответственность, которую совместно используют панель, очередь административных команд, рейтинг и маршрутизация уведомлений: event identity, HMAC actor token, idempotent multi-source credit и финальный API button contract.

**Изменённые файлы:** `personal_wheel_voting.py`, `bbvg/bot/runtime.py`, `admin_action_queue.py`, `admin_action_v3.py`, `personal_reminder_filter.py`, `bbvg_monitor_main.py`, `tests/test_button_matrix.py`, `tests/test_personal_wheel_voting.py`, `AGENTS.md`, `docs/PROJECT_CHANGELOG_RU.md`.

**Pre-update backup:** `backup/before-personal-wheel-voting-2026-07-16`, SHA `93251a671ff319b4c1c690283ce935e084e7e5e8`.

**Проверки PR №38:** успешны runs `29526552899`, `29526552827`, `29526552833`, `29526552822` и `29526552954`; обязательный current-check включает полный pytest, self-test, production acceptance и workflow validation. После добавления документации проверки запускаются повторно на новом head PR.

## 2026-07-16 — Активные колёса переведены на обязательную повторную API-проверку

**Причина:** новая BetBoom API-проверка применялась при обнаружении колеса и при повторе после временной ошибки, но уже подтверждённые карточки больше не перепроверялись. Поэтому колесо, которое BetBoom завершил досрочно, могло оставаться в активном списке до старого локального таймера.

**Что изменено:**

- каждая итерация монитора проверяет через BetBoom API все карточки в `active_wheels`, а не только ранее неподтверждённые;
- подтверждённо неактивное колесо удаляется молча вместе с участием, временными кнопочными контекстами и прочим изменяемым состоянием текущего события;
- его `action_id` сохраняется в истории, поэтому повтор старой акции не создаст новое уведомление;
- подтверждённо активная карточка получает актуальные `action_id`, серверный таймер и время доступности;
- ручное время администратора остаётся приоритетным; если API подтвердил активность, но не вернул таймер, старое время из текста Telegram не восстанавливается;
- при временной ошибке API карточка не удаляется: сохраняются прежний таймер и жизненный цикл, а `verification_status=failed` даёт пользовательскому интерфейсу понятную жёлтую пометку;
- если BetBoom назначил той же ссылке новый `action_id`, участие, ручное время и напоминания прежнего события очищаются, а карточка начинает чистое событие.

**Изменённые файлы:** `bbvg_monitor_runtime.py`, `bbvg_monitor_main.py`, `tests/test_recurring_event_hotfix.py`, `AGENTS.md`, `docs/PROJECT_CHANGELOG_RU.md`. Новые файлы не создавались.

**Pre-update backup:** `backup/before-active-wheel-revalidation-2026-07-16`, SHA `defa89375d3f3d6020e6b7c2aa5279312dc49556`.

**Проверки до публикации:** 38 профильных и 83 полных pytest-теста, `py_compile`, `preflight.py` и полный `tests/production_acceptance.py --section all` прошли.

**Production:** PR №36 слит в `main`, merge commit `69147db8da11a6ff3d647dc0e7c129bc4576fe5f`. Успешны runs `29520907350`, `29520907344`, `29520907461`, `29520907344`, `29520907402` и `29520907594`. Первая итерация monitor run `29520997610` в `2026-07-16T17:44:53.646670+00:00` проверила 78 из 78 источников без ошибок и повторно проверила все четыре активные карточки. `papa` (`action_id=852`) и `sysman` (`action_id=854`) удалены как подтверждённо неактивные; `cct1` (`action_id=837`) и `zonertg7` (`action_id=692`) оставлены активными с подтверждёнными таймерами. `wheel_api_health.status=ok`, последовательных ошибок 0. Панель продолжила работу в run `29518021398`, heartbeat `2026-07-16T17:42:39.830027+00:00`.

**Post-update backup:** `backup/after-active-wheel-revalidation-2026-07-16`, SHA `064c8aa8a020dc78c0e37b489c6aa868da9ab893`; commit подтверждён как предок текущего `main`. Ротация оставила ровно три обычные ветки: post-update, pre-update и `backup/after-wheel-api-resilience-2026-07-16`. Постоянный `archive/legacy-wheel-checker-2026-07-16` сохранён отдельно.

## 2026-07-16 — Добавлены аварийный архив, контроль API и дедупликация по action_id

**Причина:** прежняя HTML-проверка была удалена из активного `main` после перехода на BetBoom API, но для временного аварийного возврата требовалась постоянная точка вне ротации. Кроме того, двухчасовое правило ссылки и идентификатор акции должны работать совместно, а повторные сбои API — превращаться в один понятный административный инцидент.

**Что изменено:**

- прежняя система проверки целиком сохранена в постоянной ветке `archive/legacy-wheel-checker-2026-07-16` на commit `2435b570db0825f98bcb8c102a7686ec19746a81`; namespace `archive/*` не обрабатывается ротацией трёх обычных `backup/*`;
- после трёх подряд проверок, каждая из которых уже исчерпала три сетевые попытки, создаётся единый инцидент `wheel_api_validation_failure`; он отправляется только администраторам, отражается в `system_check_state.json` для GPT и не повторяется до восстановления;
- первая успешная проверка сбрасывает счётчик и закрывает инцидент с одним уведомлением о восстановлении;
- последний подтверждённый `action_id` хранится отдельно от временной карточки колеса: повтор той же акции не уведомляется даже после завершения двухчасового окна;
- новый `action_id` на той же ссылке немедленно очищает участие, публикации, таймеры и решения старого события и создаёт новое;
- если API временно не вернул `action_id`, продолжают действовать прежние правила: автоматический таймер, ручной таймер с приоритетом и двухчасовое окно без времени;
- колесо без автоматического или ручного времени больше не продлевается до семи дней: оно удаляется через два часа; для будущего открытия два часа отсчитываются после момента доступности;
- `action_id` сохраняется в истории завершённых и неактивных событий, поэтому идентичность не теряется при смене состояния.

**Pre-update backups:** исходная точка разработки `backup/before-wheel-api-resilience-2026-07-16`, SHA `55bc720efebb7e8077d86ded9f6842f3601921e8`; актуальная точка непосредственно перед развёртыванием `backup/before-wheel-api-resilience-deploy-2026-07-16`, SHA `71c16dc55cd2882cf2f17f42f849b24cfb4099bc`.

**Временный возврат:** по команде пользователя создать отдельный PR из `archive/legacy-wheel-checker-2026-07-16`, восстановив код проверки, но сохранив актуальные runtime JSON и пользовательское состояние. Полный reset `main` для такого возврата не требуется.

**Проверки:** 24 профильных и 78 полных pytest-тестов, все self-test затронутых модулей, preflight и полный `tests/production_acceptance.py --section all` прошли. В PR №34 успешны runs `29517904110`, `29517904124`, `29517904098`, `29517904092` и `29517904094`.

**Production:** PR №34 слит в `main`, merge commit `e7ce74a35ddb256df37aeab72135c4cbe903a41f`. Monitor run `29518038775` на первой итерации проверил 78 из 78 источников, ошибок источников 0, `restart_recommended=false`, успешный heartbeat `2026-07-16T17:01:58.639236+00:00`. Control center run `29518021398` оставил heartbeat версии 41 `2026-07-16T17:02:05.692962+00:00`. System health run `29518044376` завершился успешно; матрица новой проверки `wheel_api=ok`, старый отдельный инцидент transport smoke не изменился.

**Post-update backup:** `backup/after-wheel-api-resilience-2026-07-16`, SHA `abdadeed459eec4808cbbcc733b95a99a61547e9`. Ротация оставила ровно три обычные backup-ветки; постоянный `archive/legacy-wheel-checker-2026-07-16` сохранён отдельно.

## 2026-07-16 — Новые колёса переведены на обязательную проверку BetBoom API

**Причина:** разбор HTML-страницы зависел от изменяемых CSS-классов и мог пропустить действующее колесо либо показать уже завершённое. Флаг `is_ended` также не всегда обновлялся одновременно с окончанием таймера.

**Что изменено:**

- каждая новая ссылка проверяется запросом `POST /api/streamer-wheel/action/get-info` с тремя попытками;
- активность определяется по ответу BetBoom, а окончание — по `start_dttm + duration_min`; истёкший таймер имеет приоритет над запоздавшим `is_ended=false`;
- подтверждённые завершённые, ранние и несуществующие акции отбрасываются без пользовательского уведомления и без добавления в активные;
- при временном сетевом или форматном сбое колесо сохраняется по прежним правилам с одной понятной жёлтой пометкой, без технического текста для обычного пользователя;
- только неподтверждённые из-за сбоя записи повторно проверяются; успешная перепроверка убирает пометку, а подтверждённый ложный результат удаляется молча;
- `action_id` сохраняется в событии и отделяет новую акцию на повторно использованной ссылке от старых публикаций, участия и решений;
- серверный таймер и время будущего открытия передаются в уведомление и активный список; успешный ответ без таймера сохраняет прежнюю красную пометку «Время прокрутки неизвестно»;
- уже принятое подтверждённое колесо после окончания таймера не перепроверяется новой системой: далее действует прежний жизненный цикл завершения, ручного времени, участия и повторного использования ссылки;
- слой свежих Telegram-публикаций больше не может превратить подтверждённый ответ `inactive` в предварительное уведомление.

**Фактические точки изменения:** `monitor.py` (API-классификация и метаданные), `monitor_entry.py` (защита окончательного неактивного решения), `bbvg_monitor_runtime.py` (тихий отсев и повтор только временно неподтверждённых), `wheel_event_runtime.py` (будущее открытие и смена `action_id`), `wheel_link_lifecycle.py` и `wheel_metadata_quality.py` (порядок обёрток и сохранение метаданных).

**Проверки:** реальные ответы `zonertg7` (`action_id=692`, активно) и `kyzko` (`action_id=866`, таймер истёк) разобраны корректно; несуществующая акция отвергнута; `py_compile`, `self_test.py`, 73 pytest-теста, полный `tests/production_acceptance.py`, preflight и все self-test из monitor workflow прошли.

**Pre-update backup:** `backup/before-wheel-api-validation-2026-07-16`, SHA `2435b570db0825f98bcb8c102a7686ec19746a81`. После ротации подтверждены ровно три backup-ветки.

**Production:** PR №32 слит в `main`, merge commit `5aaf789048a5b9888c0f75859659bcde9f70abc5`. Новый monitor run `29515767141` выполнил первую итерацию за 5 секунд: проверено и доступно 78 из 78 источников, ошибок источников 0. Control center run `29515750749` запущен с версией 41 и новым heartbeat `2026-07-16T16:29:37.348201+00:00`.

**Post-update backup:** `backup/after-wheel-api-validation-2026-07-16`, SHA `82ea2e8228fcffc4d7a1e0df402ca58e14ee2c46`. Ротация сохранила ровно три backup-ветки.

## 2026-07-16 — Введена автоматическая ротация трёх backup-веток

**Причина:** до изменения в репозитории накопилось девять обычных `backup/*` веток, а ограничение по количеству отсутствовало.

**Что изменено:**

- существующий `.github/workflows/bot-state-backup.yml` обрабатывает создание только веток `backup/*`; для интеграций, не формирующих событие `create`, добавлен `push` любой новой `backup/**` без path-фильтра, а слияние изменения самого workflow, ручной и ежедневный запуски выполняют ту же ротацию как fallback;
- только что созданный backup сначала проверяется как предок либо текущий commit `main`;
- новый backup сохраняется, а из остальных остаются две самые свежие по времени commit их head;
- перед удалением каждая устаревшая ветка проверяется на отсутствие уникальных commits;
- при любой ошибке проверка завершается до удаления;
- чтение ref использует GitHub API `GET /git/ref/...`, а удаление — отдельный корректный endpoint `DELETE /git/refs/...`;
- право `contents: write` выдано только job ротации; ежедневный encrypted-state backup сохранил прежнее поведение;
- после ротации workflow повторно получает inventory и требует ровно три ожидаемые backup-ветки.

**Проверки:** YAML разобран; оба embedded Python blocks скомпилированы; отдельно проверен plural DELETE endpoint; mock-сценарии подтвердили create-ротацию 10 -> 3, fallback-ротацию 11 -> 3, отсутствие удаления при двух ветках, блокировку ветки с уникальными commits и блокировку непроверенного нового backup.

**Pre-update backup:** `backup/production-before-backup-retention-2026-07-16`, SHA `199373a8dcf5dd35eb49bf9df6e444a4b2ec50e3`.

## 2026-07-16 — Объединённый runtime развёрнут в production

**Причина:** архитектурный рефакторинг был проверен в отдельной ветке, но должен был быть безопасно применён к живому `main` без отката пользователей, ролей, статистики, источников и heartbeat.

**Что изменено:**

- PR №26 слит в `main`, merge commit `935abf06622933abe52922047cd18ff6f25075f6`;
- production использует предметные модули `bbvg/bot/foundation.py`, `interface.py`, `source_requests.py`, `sources.py`, `storage.py`, `users.py`, `wheels.py`, `runtime.py`;
- `admin_panel_runtime_v41.py` сокращён до тонкого переходника на `bbvg.bot.runtime`;
- синхронизированы production, recovery, private-state и current-check workflow;
- полный pytest, production acceptance, transport, recovery и smoke-проверки прошли на чистом deployment candidate;
- временные diagnostic workflow и probe-файлы удалены;
- создан post-deploy backup `backup/production-after-runtime-v42-deploy-2026-07-16`.

**Затронутые файлы:** `bbvg/bot/*`, совместимые runtime-слои, `.github/workflows/*`, `tests/production_acceptance.py`, contract-тесты, `preflight.py`, `system_checks.py`, `rating_policy.py`, `source_tier_maintenance_v2.py`, `AGENTS.md`.

**Проверки:** финальный clean candidate `c6b6308080b715774c2e93acabe0eaa917b33453` прошёл пять обязательных PR-проверок. После merge панель работает в run `29501869549`, heartbeat `2026-07-16T13:27:32.646163+00:00`; монитор работает в run `29501869738`, проверяет 78 источников, ошибок источников 0.

**Актуальный backup:** `backup/production-after-runtime-v42-deploy-2026-07-16`, SHA `597ba2376858ad8e537c29008508ec1c7b3770ed`.

**Откат:** переместить `main` на `backup/production-after-runtime-v42-deploy-2026-07-16`. Для возврата к состоянию до нового runtime использовать `backup/production-before-runtime-v42-deploy-2026-07-16`.

## 2026-07-16 — Удалены связующие runtime-файлы и пользовательские compatibility-слои

**Причина:** после переноса поведения в предметные модули ряд файлов оставался только ради исторических импортов.

**Что изменено:** удалены v13, v17, v19, v21, v27, v33, v34, v35; ранее были удалены v10–v12, v14–v16, v18, v20, v22–v24, v39 и v40. `interface`, `source_requests`, `wheels`, `users`, `sources` и `storage` связаны напрямую.

**Проверки:** каждый файл удалялся отдельным коммитом с полным pytest, compatibility/consolidated acceptance и MRO audit.

**Backup:** этапные ветки `backup/refactor-after-foundation-links-cleanup-2026-07-16` и `backup/refactor-before-user-settings-cleanup-2026-07-16`.

## 2026-07-16 — Объединены пользовательские настройки и приватность

**Причина:** персональные уведомления, удаление данных и управление уведомлениями владельцем были распределены по v33–v35.

**Что изменено:** `UserSettingsMixin` в `bbvg/bot/users.py` стал владельцем пользовательских настроек, приватности и owner-managed notification UI. Исторические v33–v35 сначала были сокращены до обёрток, затем удалены.

**Проверки:** run `29486265773`, затем последовательные runs удаления до `29489184430`.

## 2026-07-16 — Объединена подсистема зашифрованного хранения

**Причина:** загрузка приватного состояния, трёхстороннее слияние и защита ролей были распределены по v25, v34 и v35.

**Что изменено:** `bbvg/bot/storage.py` стал единственным владельцем encrypted bundle, миграционного fallback, удалённой загрузки, трёхстороннего merge, нормализации ролей, заявок и получателей уведомлений. `bbvg/bot/sources.py` стал владельцем реестра источников.

**Проверки:** runs `29483070934`, `29483384648`, `29483547791`, `29483620762`.

**Backup:** `backup/refactor-after-storage-2026-07-16`, SHA `913d761c988be754c37eec07ec0b4d054a510c4f`.

## 2026-07-16 — Объединены фундамент, интерфейс, заявки и пользовательская база панели

**Причина:** production-поведение было распределено по десяткам runtime-файлов с номерами версий.

**Что изменено:** созданы предметные модули `foundation.py`, `interface.py`, `source_requests.py`, `wheels.py`, `users.py`, `sources.py`, `storage.py`, `runtime.py`; фактическая MRO и владельцы методов документируются автоматически.

**Проверки:** прямые self-test тематических модулей, полный pytest, legacy/consolidated acceptance и MRO audit. Ключевые runs: `29471609401`, `29472303783`, `29472606919`, `29473287075`, `29473921732`.

## 2026-07-16 — Удалены неиспользуемые файлы и объединён CLI рейтинга

**Что изменено:** удалены `admin_panel_runtime_v24.py`, `monitor_resilience.py`, `normalize_source_ratings.py` и альтернативная D1-ветка v23. Нормализация рейтинга перенесена в `rating_policy.py` с `normalize_file()`, атомарным сохранением, CLI `--path` и `--self-test`.

**Проверки:** runs `29471789845`, `29471849429`, `29471933656`, `29474022528`.

## 2026-07-16 — Объединены production acceptance-проверки

**Причина:** пять `chapter*.py` выполняли одну ответственность и дублировали логику.

**Что изменено:** создан `tests/production_acceptance.py` с секциями `stability`, `unified`, `ci`, `interface`, `lifecycle`; старые chapter-файлы превращены в тонкие совместимые обёртки.

**Проверки:** параллельные runs `29471332024` и `29471388626`.

## 2026-07-16 — Введены архитектурные правила, журнал и обязательные backups

**Что изменено:** добавлен `AGENTS.md`, установлен приоритет изменения существующих тематических модулей, зафиксирована целевая структура каталогов и обязательное создание backup до и после крупных обновлений.

**Первоначальный production backup:** `backup/v41-before-cleanup-2026-07-16`.

## 2026-07-16 — Упрощён интерфейс активных колёс

**Что изменено:** декоративные цветные кружки удалены; связь карточки и действий строится по номеру; callback-данные и URL сохранены.

## 2026-07-16 — Введён жизненный цикл повторного использования ссылки колеса

**Что изменено:** автоматический таймер блокирует ссылку до прокрутки; ручной таймер имеет приоритет; без таймера действует окно 2 часа; после окончания окна ссылка может создать новое событие; новое событие не наследует участие и публикации старого.

## 2026-07-16 — Исправлены сводка и рейтинг завершённого колеса

**Что изменено:** сводка формируется внутри панели; действие «Завершено» начисляет рейтинг источникам один раз и не дублирует очки.
