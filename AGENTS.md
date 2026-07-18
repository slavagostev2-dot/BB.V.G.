# BB V.G. — обязательные инструкции для работы с репозиторием

Этот файл необходимо прочитать **до любых изменений**. После него прочитайте `docs/PROJECT_CHANGELOG_RU.md` и связанные архитектурные документы.

## 1. Главный принцип

Не создавайте новые файлы с номерами версий, временными именами или словами `final`, `new`, `fix`, `copy`, если ответственность уже принадлежит существующему модулю.

Запрещённые примеры: `admin_panel_runtime_v42.py`, `monitor_new.py`, `final_fix.py`, `chapter6.py`, `test2.py`.

Новый файл допустим только когда одновременно выполнены условия:

- существующий модуль не соответствует новой ответственности;
- расширение существующего файла ухудшит связность, тестируемость или безопасность;
- новому модулю можно дать устойчивое предметное имя без номера версии;
- определена папка, владеющая ответственностью;
- обновлены архитектурная карта и журнал изменений.

Цель проекта — уменьшать либо сохранять количество файлов. В итоговом отчёте объясняйте необходимость каждого нового файла.

## 2. Действующая архитектура

```text
bbvg/
  bot/          Telegram-панель, меню, callback, пользователи, источники, хранение
  monitor/      поиск источников, парсинг постов, обнаружение колёс
  domain/       жизненный цикл колеса, дедупликация, рейтинг
  storage/      общее состояние, шифрование, миграции
  operations/   heartbeat, диагностика, инциденты, эксплуатационные команды

tests/
  unit/
  integration/
  scenarios/

docs/
.github/workflows/
```

`personal_wheel_voting.py` владеет сквозным контрактом личного участия: идентичностью события по `action_id`, HMAC-псевдонимом участника, начислением очков всем источникам и финальным API-интерфейсом без общих кнопок удаления.

Обязательные документы:

- `docs/REFACTOR_PLAN_RU.md`
- `docs/CHAT_CONTEXT_RU.md`
- `docs/CODE_INVENTORY_RU.md`
- `docs/PROJECT_CHANGELOG_RU.md`
- `docs/RUNTIME_METHOD_INVENTORY_RU.md`

## 3. Действующие точки входа

- Telegram-панель: `bbvg/bot/runtime.py`.
- Совместимая production-команда: `python admin_panel_runtime_v41.py`.
- `admin_panel_runtime_v41.py` — только тонкий переходник на `bbvg.bot.runtime`, а не отдельная реализация.
- Предметные владельцы панели: `bbvg/bot/interface.py` (экраны и
  навигация), `users.py` (пользователи, роли и настройки),
  `sources.py` (источники), `wheels.py` (колёса и callback), `storage.py`
  (зашифрованное состояние), `runtime.py` (финальная композиция,
  lifecycle и очередь admin actions).
- Production MRO `bbvg.bot.runtime.TelegramPanelRuntime` не содержит
  классов из `admin_panel_runtime_v*`. Versioned-файлы остаются только
  как временная совместимость до главы 9 и не являются
  владельцами production-поведения.
- Монитор колёс: `bbvg_monitor_main.py`, `monitor.py` и тематические модули.

Не добавляйте параллельный runtime или второго consumer Telegram `getUpdates`.

## 4. Правила изменений

Перед изменением:

1. Найдите все импорты и текстовые ссылки.
2. Проверьте связанные workflow и тесты.
3. Определите затрагиваемые runtime-данные.
4. Сохраните callback-данные и формат состояния.
5. Проверьте возможность изменить существующий модуль без нового файла.
6. Перед крупным обновлением создайте и проверьте backup-ветку. Точные имя и SHA фиксируются в журнале изменения и в summary workflow ротации; изменяемый список веток не дублируется вручную.

После изменения:

1. Скомпилируйте изменённые Python-файлы.
2. Запустите относящиеся unit/integration/scenario tests.
3. Запустите полный `pytest` и production acceptance.
4. Проверьте свежий heartbeat панели.
5. Проверьте монитор, число источников и его heartbeat.
6. Обновите `docs/PROJECT_CHANGELOG_RU.md`.
7. Обновите этот файл и архитектурные документы при изменении правил, точек входа, зависимостей или backup.

Развёртывание нельзя считать успешным только по коммиту или компиляции: нужен подтверждённый живой процесс.

### GitHub Actions и точный production SHA

- Сторонние `actions/checkout`, `actions/setup-python` и `actions/upload-artifact` закрепляются только полным 40-символьным commit SHA; рядом сохраняется комментарий с проверенной версией. Moving tags вида `@v4` и `@v5` запрещены.
- Validation jobs получают exact event SHA: для pull request используется `github.event.pull_request.head.sha`, для push и ручного запуска - `github.sha`. Проверка не должна незаметно переходить на `main` или synthetic merge commit.
- `ref: main` разрешён только для явно long-running production, maintenance и authoritative backup jobs.
- Read-only checkout всегда использует `persist-credentials: false`.
- `contents: write` выдаётся только job, который действительно записывает state или очередь; `actions: write` - только job, который dispatch/restart другой workflow.
- `admin_panel_status.json` и `monitor_status.json` обязаны содержать `head_sha`, `workflow_run_id` и `run_attempt`, сохраняя совместимое поле `run_id`.

## 5. Обязательные бэкапы крупных обновлений

Перед крупным обновлением, рефакторингом, миграцией состояния, изменением workflow или массовым удалением:

1. Создайте ветку `backup/YYYY-MM-DD-description` либо `backup/before-description`.
2. Зафиксируйте точный SHA, дату, назначение и последнее подтверждённое состояние.
3. Не перемещайте backup-ветку после начала работы.
4. После успешного обновления создайте новый backup от подтверждённого production-состояния.
5. Новый обычный backup считается принятым только после проверки, что его ref существует, а commit является предком либо текущим commit ветки `main`.
6. После создания новой ветки `backup/*` workflow `.github/workflows/bot-state-backup.yml` автоматически оставляет ровно три последних проверенных обычных backup-ветки. Он обрабатывает события `create` и создание ветки через `push` независимо от изменённых файлов; слияние изменения самого workflow, ручной запуск и ежедневный schedule выполняют ту же ротацию как страховочные пути.
7. Только что созданная ветка всегда сохраняется; остальные упорядочиваются по времени commit их head. Ветки старше первых трёх удаляются.
8. Перед удалением workflow проверяет, что каждый старый backup является предком `main` и не содержит уникальных commits. При любой ошибке старые ветки не удаляются.
9. После успешной ротации проверьте summary workflow и убедитесь, что в `refs/heads/backup/` остались именно три ожидаемые ветки.
10. Обновите `docs/PROJECT_CHANGELOG_RU.md`.

Обычные публичные backup-ветки ограничены тремя. Специальный зашифрованный offline backup для переписывания Git-истории хранится вне `refs/heads/backup/` и в эту тройку не входит.

Откат выполняется возвратом актуальной backup-ветки или commit целиком. Не восстанавливайте набор файлов вручную по памяти, если существует backup.

## 6. Журнал проекта

`docs/PROJECT_CHANGELOG_RU.md` фиксирует:

- новые функции и исправления поведения;
- изменения обнаружения, дедупликации, таймеров и рейтинга;
- создание, перенос, объединение и удаление модулей;
- изменения workflow, хранения, миграций и точек входа;
- backup-точки, проверки, откаты и отклонённые решения.

Не записывайте автоматические heartbeat-коммиты, обычные сохранения JSON и форматирование без изменения смысла.

## 7. Контракты колёс

- каждое колесо из активного списка повторно проверяется через BetBoom API на каждой итерации монитора;
- ответ BetBoom с `action_id` и `duration_min`, но без `start_dttm` означает созданную, но ещё не запущенную акцию: она не показывается в активных и без уведомления перепроверяется до появления времени старта;
- подтверждённо неактивное колесо удаляется из активных вместе с относящимися к текущему событию отметками и кнопочными контекстами, без пользовательского технического уведомления;
- при временной ошибке повторной проверки колесо остаётся активным с понятной пометкой, а прежний таймер и жизненный цикл не теряются;
- одинаковый Telegram-пост повторно не отправляется;
- идентичность API-события определяется поколением сервера: при наличии `action_id` и `start_dttm` используется их совместная идентичность; одинаковые `action_id` + `start_dttm` повторно не отправляются;
- подтверждённый более поздний `start_dttm` открывает чистое новое поколение даже при прежнем `action_id`; участие, публикации, suppression и рейтинг старого поколения не наследуются;
- новый `action_id` на прежней ссылке также немедленно начинает чистое событие и не ждёт окончания таймера старой акции;
- если BetBoom временно не вернул `action_id`, применяются прежние правила ссылки и таймеров;
- ссылка с автоматическим таймером блокируется до прокрутки;
- сохранённый ручной таймер имеет приоритет для обратной совместимости;
- без автоматического или сохранённого ручного таймера колесо и одинаковая ссылка действуют 2 часа;
- после окончания окна ссылка может создать новое событие;
- новое событие не наследует участие и публикации старого;
- отметка «Участвую» всегда личная, в том числе у администратора и владельца;
- личное участие хранится по поколению события: при наличии серверного `generation_id` ключ включает поколение, а без него совместимо откатывается к `action_id`; отметка старого запуска не переносится на новый;
- один пользователь учитывается один раз для одного события; повторное нажатие не начисляет очки повторно;
- голос пользователя даёт каждому источнику колеса 1 очко, голос администратора или владельца — 5 очков;
- если колесо опубликовано в нескольких источниках, одинаковый вес начисляется каждому уникальному источнику;
- в публичную очередь рейтинга записывается HMAC-псевдоним, а не Telegram ID;
- финальное напоминание подавляется только для конкретного пользователя, отметившего участие в конкретном событии;
- уведомление о наступлении времени отправляется до автоматической очистки завершившегося колеса.
- основной монитор не вызывает Telegram `getUpdates`: единственным consumer входящих updates является production-панель; этот контракт проверяется запуском обоих путей с fake Telegram API, а не поиском текста в исходниках.
- двухчасовое окно колеса без автоматического или ручного таймера является фиксированным доменным контрактом и не переопределяется переменной workflow.
- каждая команда `admin_action_queue` идемпотентна по `command_id`; повтор любого поддержанного action не меняет state, health или rating.
- `state.json.wheel_generation_observations` хранит не более 1000
  обезличенных API-идентичностей за 14 дней: wheel key,
  `action_id`, `start_dttm`, первое/последнее наблюдение и счётчик.
  Журнал не содержит Telegram ID и не меняет решение о
  дедупликации. Отчёт: `python wheel_event_runtime.py
  --observation-report state.json`.

### Достоверный health и inventory источников

- Авторитетный inventory вычисляется из текущих `public_sources.txt` и `source_catalog.txt`; historical constants вроде 66/77 запрещены для критических health-решений.
- Диагностика отдельно показывает primary, nightly и общий operational total и сверяет их с `source_transport_state.json`.
- Успешный свежий transport smoke обязан совпадать с текущим inventory по primary/nightly/total, не иметь missing/error sources и автоматически закрывать прежний stale incident.
- `source_registry.json`, transport smoke и monitor heartbeat могут считать разные контуры только явно: registry/transport — весь primary + nightly inventory, monitor — primary текущей итерации.

## 8. Telegram-интерфейс

- Не изменяйте `callback_data` без миграции и тестов.
- Лимит callback — 64 байта.
- Не используйте декоративные цвета для связи колеса с кнопками.
- Значок должен обозначать состояние или действие.
- Повторное административное действие не начисляет рейтинг повторно.
- В актуальном API-режиме не показывайте и не исполняйте общие кнопки «Завершено», «Неактивное» или пользовательское скрытие колеса.
- Ручное указание или изменение времени скрыто и не исполняется; старые `bb:t:*` и `wheel:time:*` callback безопасно возвращают главное меню без изменения состояния.
- После нажатия «Участвую» исходное уведомление редактируется в главное меню; отдельное сообщение не создаётся.
- Удаление колеса из общего списка выполняет только автоматическая BetBoom API-проверка.
- Старые callback отключённых действий сохраняются как безопасная совместимость: они отвечают, что действие отключено, и не меняют общее состояние.
- Раздел «API и Legacy» обязан ясно показывать, что API является production-источником истины, а legacy checker возвращается только отдельным проверенным deploy.

## 9. Состояние и безопасность

- Не публикуйте токены, chat ID, user ID и расшифрованное состояние.
- Не заменяйте `BOT_STATE_KEY` токеном бота.
- Не удаляйте runtime JSON без миграционного анализа.
- Формат состояния должен быть обратимо мигрируемым.
- Любая запись локального authoritative/diagnostic state выполняется через
  атомарную замену файла в том же каталоге: flush, `fsync`, `os.replace`.
- `monitor_data.JSON_STATE_CONTRACTS` является машинно-проверяемым inventory
  всех 28 JSON. Новый JSON нельзя добавить без категории, владельца и schema
  contract либо явной отметки frozen archive.
- Один authoritative файл не коммитится автоматическими workflow из разных
  concurrency-групп. `public_sources.txt` и `source_catalog.txt` имеют двух
  предметных производителей, поэтому оба сериализованы общей группой
  `bb-vg-source-catalog-writer`.
- Encrypted bundle сохраняется remote CAS + three-way merge; локальная копия
  после remote success заменяется атомарно. Обычный panel writer и ручная
  ротация ключа используют одну группу `bb-vg-telegram-admin-panel`.
- `notification_delivery_state.json` принимает legacy v2 и мигрируется в v3
  без потери `entries`. V3 содержит только HMAC digest, delivery timestamp и
  expiring interprocess claim; claim автоматически освобождается по TTL после
  аварии процесса.
- `admin_action_queue.json` изменяется только Contents API CAS с повтором 409 и
  422; `command_id` идемпотентен, а очередь и журналы результатов ограничены.

### Ownership matrix всех JSON

| Файл | Категория | Единственный владелец записи |
|---|---|---|
| `activation_runtime_state.json` | archive | frozen Mini App |
| `admin_action_queue.json` | authoritative | `admin_action_queue` CAS |
| `admin_panel_status.json` | diagnostic | control-center workflow |
| `bot_access.json` | compatibility | encrypted private state |
| `bot_private_state.enc.json` | authoritative | control center CAS / exclusive key rotation |
| `candidate_moderation.json` | authoritative | control center moderation |
| `discovery_state.json` | authoritative | nightly discovery |
| `identifier_sources.json` | config | source catalog |
| `incident_state.json` | diagnostic | system health |
| `intelligence_state.json` | authoritative | source intelligence |
| `miniapp_deploy_runtime.json` | archive | frozen Mini App |
| `miniapp_deployment.json` | archive | frozen Mini App |
| `monitor_recovery_status.json` | diagnostic | monitor recovery |
| `monitor_status.json` | diagnostic | monitor |
| `notification_delivery_state.json` | authoritative | notification ledger; remote commit by monitor |
| `partners_catalog.json` | config | source catalog |
| `private_state_deployment.json` | archive | frozen Mini App |
| `source_health.json` | authoritative | monitor |
| `source_registry.json` | cache | source registry workflow |
| `source_requests.json` | compatibility | encrypted private state |
| `source_stats.json` | authoritative | monitor |
| `source_tier_state.json` | diagnostic | source-tier maintenance |
| `source_transport_state.json` | diagnostic | transport smoke |
| `state.json` | authoritative | monitor |
| `state_api/package.json` | archive config | frozen state API |
| `state_api_runtime.json` | archive | frozen state API |
| `system_check_state.json` | diagnostic | system health |
| `unknown_timer_samples.json` | authoritative | monitor |

## 10. Эксплуатационные действия

Разрешено при необходимости временно остановить или перезапустить панель/монитор, отправить ограниченное тестовое уведомление, выполнить тестовое административное действие и включить временную диагностику.

Условия: минимальное воздействие, метка `ТЕСТ`, отсутствие массовой рассылки без отдельной команды, удаление временной диагностики, подтверждение heartbeat и наличие backup.

## 11. Актуальные бэкапы и ротация

Авторитетный список обычных rollback-точек - текущие ветки в namespace `refs/heads/backup/`. Он не дублируется здесь вручную, потому что после каждого нового крупного обновления автоматически меняется.

Обязательные правила:

- после создания нового `backup/*` остаются ровно три последние проверенные ветки; если интеграция не сформировала событие создания, тот же inventory проверяется после слияния изменения workflow, при ручном запуске и ежедневном schedule;
- новый backup удалять запрещено;
- старые ветки удаляются только после проверки ancestry и отсутствия уникальных commits;
- точные имена и SHA сохранённых и удалённых веток записываются в summary workflow;
- при ошибке проверки ротация завершается без удаления;
- планирование и GitHub API-операции принадлежат `backup_rotation.py`; `--self-test` покрывает 0/1/2/3/4 refs, failed verification, idempotency, dry-run и защиту namespace;
- ручной `workflow_dispatch` по умолчанию выполняет dry-run без удаления; create/push новой `backup/*`, schedule и merged workflow change применяют реальную ротацию;
- creation и rotation используют единственную concurrency-группу `bb-vg-bot-state-backup`.

Pre-update backup перед введением ротации:

- Ветка: `backup/production-before-backup-retention-2026-07-16`
- Commit SHA: `199373a8dcf5dd35eb49bf9df6e444a4b2ec50e3`
- Назначение: полный откат к production до изменения backup workflow.

Для отката сначала получите текущие три `backup/*` refs, проверьте их SHA и выберите подходящую точку по журналу и summary последней ротации.

## 12. Удаление файлов

Файл удаляется только если:

- он не импортируется и не указан в workflow;
- полезное содержимое перенесено;
- тесты и production acceptance проходят;
- панель и монитор оставляют свежий heartbeat.

Git хранит историю, поэтому старые версии не нужно сохранять в основной ветке ради архива.

Постоянный аварийный архив прежней HTML-проверки колёс хранится вне ротации
обычных backup-веток: `archive/legacy-wheel-checker-2026-07-16`, commit
`2435b570db0825f98bcb8c102a7686ec19746a81`. Временный возврат выполняется
отдельным проверяемым PR из этой точки без отката runtime JSON.

## 13. Итоговый отчёт

Укажите:

- изменённые, созданные, перемещённые и удалённые файлы;
- обоснование новых файлов;
- сохранённые контракты;
- тесты и run ID;
- heartbeat панели и монитора;
- запись в журнале;
- актуальный backup и точный способ отката.
