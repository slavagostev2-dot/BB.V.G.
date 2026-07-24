# BB V.G. — baseline контура колёс

Актуально на 25 июля 2026 года. Этот документ фиксирует **текущее production-поведение до рефакторинга**. Он не описывает целевую архитектуру и не разрешает менять пользовательские контракты без отдельного согласования.

## 1. Контрольная точка

- Источник истины на момент начала: `main`.
- Неизменяемая резервная ветка: `backup/pre-refactor-20260725`.
- Рабочая ветка этапа 1: `agent/architecture-baseline-20260725`.
- На этапе 1 разрешены только документация, тесты и проверки структуры.
- Production-код, workflow, runtime JSON, encrypted state и release-marker не изменяются.

## 2. Production-точки входа

| Контур | Точка входа |
|---|---|
| Основной монитор | `python bbvg_monitor_main.py` |
| Control Center | `python notification_button_recovery.py` |
| Первая попытка автоучастия | `python auto_participation_worker.py` |
| Независимый recovery | `python auto_participation_recovery.py` |
| Ночная проверка источников | `python nightly_discovery_entry.py` |

## 3. Фактическая композиция Monitor

Модули устанавливаются последовательно. Более поздний слой может оборачивать функцию, уже изменённую предыдущим слоем.

```text
monitor.py
  ↓ notification_router.install
monitor_entry.py
  • выбор канонической Telegram-публикации
  • notification-first для свежего уникального поста
  • сохранение всех публикаций и источников
  • draw-time alert
  ↓
bbvg_monitor_runtime.py
  • перевод большинства pending-записей в active_wheels
  • сохранение not_started в pending_posts
  • API revalidation активных колёс
  • BB V.G. reply markup
  ↓
bbvg_monitor_main.py
  1. notification preferences
  2. personal voting router
  3. recurring wheel identity
  4. Telegram transport
  5. button-aware Telegram links
  6. wheel event runtime
  7. metadata quality
  8. publication aggregation
  9. restart duplicate guard
  10. wheel link lifecycle
  11. local production overrides
  12. notification navigation
  13. wheel lifecycle
  14. personal reminder and auto-participation dispatch
  15. VK wheel notifications
```

Критическое следствие: итоговые `assess_new_wheel`, `assess_pending_wheel`, `remember_active_wheel`, `process_active_wheels`, `send_message` и `wheel_reply_markup` не принадлежат одному исходному файлу. Их поведение является результатом всей композиции.

## 4. Текущий путь обнаружения

1. Monitor получает список источников из `public_sources.txt` с учётом health/quarantine.
2. `telegram_transport` открывает публичные Telegram-страницы через разрешённый домен `telegram.me`.
3. `telegram_post_links_v2` сохраняет ссылки из текста и кнопок.
4. `monitor_entry` группирует публикации по BetBoom-идентификатору и выбирает каноническую публикацию. Все уникальные источники сохраняются для рейтинга.
5. Для каждой ссылки формируется publication key. Уже известный точный Telegram-пост повторно не обрабатывается.
6. Страница проверяется через BetBoom API `action/get-info`.
7. `wheel_event_runtime` сопоставляет `wheel_key + action_id + server_start_at` с текущим или новым поколением события.
8. `wheel_link_lifecycle` применяет окно повторного использования ссылки и не даёт старому событию подавить новое после завершения его окна.

## 5. Текущая классификация события

Основные состояния наблюдения:

- `active` — BetBoom подтвердил активное событие;
- `not_started` — объект BetBoom существует, но старт ещё не задан;
- `scheduled_availability` — известно будущее время открытия участия;
- `inactive` — событие завершено, закрыто либо административно отмечено неактивным;
- `verification_failed` — API временно не подтвердил состояние;
- `preliminary` — свежая уникальная Telegram-публикация допускается notification-first при неокончательной проверке;
- `duplicate_action`, `duplicate_link`, `duplicate_publication` — повтор текущего события или публикации.

`not_started` остаётся в `pending_posts`. Остальные незавершённые нереферальные события обычно переводятся в `active_wheels`, в том числе события без установленного дедлайна.

## 6. Текущий путь первичного уведомления

1. Классификатор возвращает `should_notify=True`.
2. Проверяются старое событие, участие, suppression и публикационный dedup.
3. `notify_new_link` либо специализированное availability-уведомление формирует карточку.
4. `notification_router` определяет категорию и получателей.
5. Реферальная политика подавляет все пользовательские wheel-уведомления для referral event.
6. `notification_integrity_v2` и remote checkpoint резервируют delivery identity.
7. Telegram должен подтвердить фактическую доставку. Формальный `ok` без получателя не считается доставкой.
8. После успешной доставки фиксируются активное событие, публикации, button context и delivery ledger.
9. В карточке сохраняются маршруты «Активные колёса» и «Главное меню» согласно текущему UI-контракту.

Стабильная пользовательская идентичность уведомления должна зависеть от события и фазы, а не от текста другого источника или изменения оставшегося времени.

## 7. Текущий жизненный цикл

`process_active_wheels` в production является цепочкой нескольких владельцев. За один цикл она выполняет:

- API revalidation активных событий;
- переход future availability в доступное участие;
- обычные и финальные напоминания;
- draw-time notification;
- lifecycle stamping;
- завершение и очистку события;
- запуск post-scan auto-participation dispatcher.

Событие с известным дедлайном завершается после наступления дедлайна согласно текущему grace-контракту. Событие без времени хранится ограниченный TTL и ждёт ручного времени без периодического пользовательского спама.

## 8. Текущий путь автоучастия

1. После сохранения текущего события `personal_reminder_filter` создаёт dispatch-запись.
2. `auto_participation_dispatch.py` сначала публикует актуальный `state.json`, затем вызывает `auto-participation.yml`.
3. Workflow выполняет независимые шаги:
   - основная event-попытка;
   - быстрый проход уже найденных active events;
   - второй аккаунт Вячеслава;
   - отдельный аккаунт `xFLARXx`;
   - полный source recovery;
   - повторные account-шаги по recovery-результату.
4. Все браузерные попытки используют `betboom_participation_browser.py` и отдельные storage state аккаунтов.
5. Браузер закрывает только cookie-согласие, не нажимает «Об акции» как подготовительное действие и ищет точную кнопку участия в документе и iframe.
6. Успех подтверждается точной success-меткой либо post-click layout: кнопка участия исчезла, «Об акции» появилась.
7. Workflow пишет только публичный outcome в `state.json`; финальное Telegram-сообщение не отправляется из workflow.

## 9. Текущий путь итогового сообщения

1. Основной и второй аккаунт Вячеслава записываются отдельными account event records.
2. `auto_participation_notifications.py` группирует их по точному base event token.
3. Итог создаётся только после settled outcome обоих аккаунтов.
4. Подтверждённый success имеет приоритет над ранее сохранённым failure.
5. Технические `timeout`, `browser_error` и `unconfirmed` остаются повторяемыми состояниями и не являются финальной пользовательской неудачей.
6. Control Center является единственным владельцем итоговой отправки:
   - ставит личную отметку участия;
   - начисляет идемпотентный рейтинг источникам;
   - обновляет исходную кнопку при доступном HMAC message reference;
   - отправляет не более одного короткого результата по двум аккаунтам;
   - сохраняет completion в encrypted state.
7. Реферальное событие и отключённая настройка `auto_participation` не отменяют браузерную попытку, личную отметку и рейтинг, но подавляют итоговое сообщение.

## 10. Recovery-handoff

Recovery может обнаружить событие, пропущенное текущим локальным snapshot Monitor. Тогда он создаёт или восстанавливает event record и ставит `recovered_initial_notification_pending_at`.

Живой Monitor импортирует из `origin/main` только такой подтверждённый recovery-handoff, не заменяя весь локальный lifecycle state. После импорта первоначальная карточка проходит обычный notification path. Этот механизм является действующим baseline и не удаляется на этапе 1.

## 11. Владельцы состояния

| Состояние | Текущий владелец |
|---|---|
| Источники, wheel lifecycle, публикации | Monitor и тематические wheel-модули |
| Auto-participation event/dispatch records | Auto-participation workflow с merge в актуальный Monitor state |
| Delivery ledger и claims | `notification_integrity_v2.py`, `notification_remote_checkpoint.py` |
| Пользователи, настройки, completion итогов | Единственный Control Center и encrypted state |
| Рейтинг и личные голоса | `personal_wheel_voting.py` и `source_stats.json` |
| Heartbeat Monitor/Control Center | соответствующие production workflow |

## 12. Замороженные контракты этапа 1

До отдельного согласования нельзя менять:

- порядок пользовательских сообщений;
- callback strings и кнопки;
- referral policy;
- правила двух аккаунтов Вячеслава и отдельного `xFLARXx`;
- exact event identity;
- дедупликацию доставки;
- retry/failure semantics;
- rating weight и multi-source credit;
- JSON/encrypted-state schema;
- workflow concurrency и continuity;
- production entrypoints.

## 13. Сценарные проверки baseline

`tests/scenarios/test_wheel_pipeline_baseline.py` фиксирует два контракта:

1. Production-композиция содержит действующие event, duplicate, lifecycle и auto-dispatch слои.
2. Подтверждённая активная публикация проходит путь:

```text
BetBoom assessment
→ одно первичное уведомление
→ active wheel с точным action identity
→ подтверждённое участие
→ два account outcomes
→ одно итоговое сообщение Control Center
```

Тесты не выполняют сетевые запросы, не запускают браузер, не пишут production JSON и не изменяют пользовательские данные.

## 14. Следующий этап

После принятия этого baseline можно начинать только пункт 2 плана: ввод единых моделей и инвентаризация полей. Удаление обёрток, перенос ответственности и изменение поведения относятся к последующим отдельным этапам.
