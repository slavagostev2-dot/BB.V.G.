from __future__ import annotations

from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    file = Path(path)
    text = file.read_text(encoding="utf-8")
    if new in text:
        return
    if old not in text:
        raise RuntimeError(f"marker missing in {path}: {old[:80]!r}")
    file.write_text(text.replace(old, new, 1), encoding="utf-8")


replace_once(
    "bbvg/bot/runtime.py",
    "from typing import Any\n",
    "from typing import Any\nfrom datetime import datetime\n",
)
replace_once(
    "bbvg/bot/runtime.py",
    "from admin_panel_runtime_v38 import TelegramPanelRuntimeV38\n",
    "from admin_panel_runtime_v38 import TelegramPanelRuntimeV38\nfrom bbvg.bot.sources import load_source_registry, source_registry_fallback\n",
)

methods = '''
    def _registry_snapshot(self, snap: Any) -> dict[str, Any]:
        registry = load_source_registry()
        if not registry.get("sources"):
            registry = source_registry_fallback(snap)
        if not registry.get("generated_at"):
            candidates = [
                str(row.get("last_checked_at") or "")
                for row in registry.get("sources", [])
                if isinstance(row, dict) and str(row.get("last_checked_at") or "")
            ]
            registry["generated_at"] = max(candidates, default=None)
        return registry

    def show_sources(self) -> None:
        snap = self.snapshot(force=True)
        registry = self._registry_snapshot(snap)
        summary = registry.get("summary") if isinstance(registry.get("summary"), dict) else {}
        groups = self.source_sets(snap)
        primary = groups.get("primary", [])
        reserve = groups.get("reserve", [])
        paused = groups.get("paused", [])
        generated_at = registry.get("generated_at")
        updated = (
            f"{self.fmt_dt(generated_at)} ({self.age_text(generated_at)})"
            if generated_at
            else "время обновления пока не записано"
        )
        problems = int(summary.get("unavailable", 0) or 0) + int(
            summary.get("pending", 0) or 0
        )
        lines = [
            "📡 <b>Источники</b>",
            "",
            f"Всего в реестре: <b>{int(summary.get('total', len(primary) + len(reserve)) or 0)}</b>",
            f"Основная проверка: <b>{int(summary.get('primary', len(primary)) or 0)}</b>",
            f"Ночное наблюдение: <b>{int(summary.get('nightly', len(reserve)) or 0)}</b>",
            f"Проверено: <b>{int(summary.get('checked', 0) or 0)}</b>",
            f"Доступно: <b>{int(summary.get('available', 0) or 0)}</b>",
            f"Требуют внимания: <b>{problems}</b>",
            f"Реестр обновлён: <b>{html.escape(updated)}</b>",
        ]
        rows: list[list[dict[str, str]]] = [
            [{"text": f"⚡ Основная проверка ({len(primary)})", "callback_data": "source_list:primary:0"}],
            [{"text": f"🌙 Ночное наблюдение ({len(reserve)})", "callback_data": "source_list:reserve:0"}],
            [{"text": f"⏸ Приостановлены ({len(paused)})", "callback_data": "source_list:paused:0"}],
        ]
        if self.is_admin():
            rows.append([{"text": "➕ Добавить источник", "callback_data": "source:add"}])
        self.send("\n".join(lines), reply_markup=self.with_nav(rows))

    def show_analytics(self, days: int = 1) -> None:
        days = days if days in {1, 7, 30} else 1
        snap = self.snapshot(force=True)
        overview = self.period_overview(snap, days)
        totals = self.period_totals(snap.stats, days)
        current = self._collect_current_wheels()
        multi_source = sum(
            len(
                self._sources_for_item(
                    snap,
                    str(item.get("_key") or item.get("identifier") or ""),
                    item,
                )
            ) > 1
            for item in current
        )

        rated: list[tuple[str, int, int]] = []
        latest_source = ""
        latest_at: datetime | None = None
        source_rows = snap.stats.get("sources") if isinstance(snap.stats, dict) else {}
        if isinstance(source_rows, dict):
            for source, raw in source_rows.items():
                if not isinstance(raw, dict):
                    continue
                score = int(raw.get("quality_score", 0) or 0)
                votes = int(raw.get("personal_votes", 0) or 0)
                if score > 0:
                    rated.append((str(source), score, votes))
                candidate = self.parse_dt(raw.get("last_wheel_post_at"))
                if candidate and (latest_at is None or candidate > latest_at):
                    latest_at = candidate
                    latest_source = str(source)
        rated.sort(key=lambda item: (-item[1], -item[2], item[0].casefold()))
        registry = self._registry_snapshot(snap)
        registry_summary = (
            registry.get("summary") if isinstance(registry.get("summary"), dict) else {}
        )

        lines = [
            f"📊 <b>Аналитика {html.escape(self.period_title(days))}</b>",
            "",
            "<b>Находки</b>",
            f"🎡 Публикаций с колёсами: <b>{overview['wheel_posts']}</b>",
            f"📡 Источников с находками: <b>{overview['sources_with_wheels']}</b>",
            f"🔔 Отправлено уведомлений: <b>{overview['notifications']}</b>",
            f"🛡 Повторов подавлено: <b>{int(totals.get('duplicates_suppressed', 0) or 0)}</b>",
            f"⚠️ Ошибок источников: <b>{int(totals.get('errors', 0) or 0)}</b>",
        ]
        if days > 1:
            lines.append(
                f"📈 Среднее публикаций в день: <b>{overview['wheel_posts'] / days:.1f}</b>"
            )
            if overview.get("best_day"):
                best_day, best_count = overview["best_day"]
                lines.append(f"⭐ Лучший день: <b>{html.escape(str(best_day))}</b> — {best_count}")
        if overview.get("top_sources"):
            lines.extend(["", "<b>Топ источников по находкам</b>"])
            for index, (source, count) in enumerate(overview["top_sources"][:5], 1):
                lines.append(f"{index}. @{html.escape(source)} — {count}")

        lines.extend(
            [
                "",
                "<b>Участие и рейтинг</b>",
                f"🙋 Личных голосов: <b>{int(totals.get('personal_votes', 0) or 0)}</b>",
                f"🏆 Начислено очков источникам: <b>{int(totals.get('personal_vote_points', 0) or 0)}</b>",
                f"📊 Источников с рейтингом: <b>{len(rated)}</b>",
            ]
        )
        if rated:
            lines.append(
                f"🥇 Лидер: <b>@{html.escape(rated[0][0])}</b> — "
                f"{rated[0][1]} оч. ({rated[0][2]} голос.)"
            )

        lines.extend(
            [
                "",
                "<b>Сейчас</b>",
                f"🔥 Активных колёс: <b>{overview['active']}</b>",
                f"⏱ С известным временем: <b>{overview['active_with_time']}</b>",
                f"🔗 Найдены в нескольких каналах: <b>{multi_source}</b>",
                f"✅ Вы участвуете: <b>{overview['participating']}</b>",
            ]
        )
        if latest_at:
            lines.append(
                f"🕘 Последняя находка: <b>@{html.escape(latest_source)}</b>, "
                f"{self.fmt_dt(latest_at.isoformat())}"
            )
        lines.extend(
            [
                "",
                "<b>Покрытие источников</b>",
                f"✅ Доступно: <b>{int(registry_summary.get('available', 0) or 0)} из "
                f"{int(registry_summary.get('total', 0) or 0)}</b>",
            ]
        )
        if registry.get("generated_at"):
            lines.append(f"🗂 Реестр обновлён: <b>{self.fmt_dt(registry['generated_at'])}</b>")

        rows: list[list[dict[str, str]]] = [
            [
                {"text": "Сегодня", "callback_data": "page:analytics:1"},
                {"text": "7 дней", "callback_data": "page:analytics:7"},
                {"text": "30 дней", "callback_data": "page:analytics:30"},
            ],
            [
                {"text": "🏆 Рейтинг", "callback_data": "page:ranking"},
                {"text": "📡 Источники", "callback_data": "page:sources"},
            ],
        ]
        if self.is_admin():
            rows.append([{"text": "📭 Давно без колёс", "callback_data": "page:report:inactive"}])
        self.send("\n".join(lines), reply_markup=self.with_nav(rows))

    def show_ranking(self) -> None:
        snap = self.snapshot(force=True)
        rows: list[tuple[str, int, int]] = []
        source_rows = snap.stats.get("sources") if isinstance(snap.stats, dict) else {}
        if isinstance(source_rows, dict):
            for source, raw in source_rows.items():
                if not isinstance(raw, dict):
                    continue
                score = int(raw.get("quality_score", 0) or 0)
                if score > 0:
                    rows.append((str(source), score, int(raw.get("personal_votes", 0) or 0)))
        rows.sort(key=lambda item: (-item[1], -item[2], item[0].casefold()))
        lines = [
            "🏆 <b>Рейтинг источников</b>",
            "",
            "Пользователь даёт каждому источнику события 1 очко; "
            "администратор или владелец — 5 очков.",
            "Если колесо найдено в нескольких каналах, одинаковый вес получает каждый канал.",
            "",
        ]
        medals = ["🥇", "🥈", "🥉"]
        for index, (source, score, votes) in enumerate(rows[:20], 1):
            mark = medals[index - 1] if index <= 3 else f"{index}."
            lines.append(
                f"{mark} <b>@{html.escape(source)}</b> — <b>{score}</b> оч. ({votes} голос.)"
            )
        if not rows:
            lines.append("Пока нет источников с положительным рейтингом.")
        self.send(
            "\n".join(lines),
            reply_markup=self.with_nav(
                [[{"text": "🔄 Обновить рейтинг", "callback_data": "page:ranking"}]]
            ),
        )
'''
replace_once(
    "bbvg/bot/runtime.py",
    "\n    def show_active(self, page: int = 0) -> None:\n",
    "\n" + methods + "\n    def show_active(self, page: int = 0) -> None:\n",
)

replace_once(
    "bbvg/bot/sources.py",
    '''    assert fallback["summary"] == {
        "total": 2,
        "primary": 1,
        "nightly": 1,
        "checked": 2,
        "available": 1,
        "unavailable": 1,
        "pending": 0,
    }
''',
    '''    assert fallback["summary"] == {
        "total": 2,
        "primary": 1,
        "nightly": 1,
        "checked": 2,
        "available": 1,
        "unavailable": 1,
        "pending": 0,
    }
    assert fallback["generated_at"] == "2026-07-16T00:00:00Z"
''',
)

entry = '''## 2026-07-18 — Расширена аналитика и исправлен multi-source рейтинг

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

'''
path = Path("docs/PROJECT_CHANGELOG_RU.md")
text = path.read_text(encoding="utf-8")
if entry not in text:
    path.write_text(text.replace("---\n\n", "---\n\n" + entry, 1), encoding="utf-8")
