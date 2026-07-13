from __future__ import annotations

import html
import json

import monitor
import nightly_discovery
import notification_router

notification_router.install(monitor)


def sync_demoted_sources() -> list[str]:
    """Add previously tracked primary sources that are no longer in the fast list.

    The source history is authoritative for membership discovery; wheel-producing
    channels remain in public_sources.txt, while every historical source removed
    from that file is retained in the nightly catalog instead of being lost.
    """
    active = nightly_discovery.unique(
        monitor.read_list(nightly_discovery.ACTIVE_PATH)
    )
    active_keys = {value.casefold() for value in active}
    catalog = nightly_discovery.unique(
        monitor.read_list(nightly_discovery.CATALOG_PATH)
    )
    catalog_keys = {value.casefold() for value in catalog}

    stats_path = nightly_discovery.ROOT / "source_stats.json"
    try:
        payload = json.loads(stats_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        payload = {}
    sources = payload.get("sources", {}) if isinstance(payload, dict) else {}

    added: list[str] = []
    if isinstance(sources, dict):
        for source in sources:
            clean = str(source).strip().lstrip("@")
            key = clean.casefold()
            if not clean or key in active_keys or key in catalog_keys:
                continue
            catalog.append(clean)
            catalog_keys.add(key)
            added.append(clean)

    if added:
        nightly_discovery.write_sources(
            nightly_discovery.CATALOG_PATH,
            catalog,
            "# Ночная проверка: резервные источники и каналы без найденных колёс.\n"
            "# Возврат в основную проверку выполняется только администратором.",
        )
        print(f"Moved {len(added)} historical sources into nightly monitoring.")
    return added


def main() -> int:
    manual_run = nightly_discovery.MANUAL_RUN
    moved_to_nightly = sync_demoted_sources()

    # The discovery engine may identify a source as suitable for frequent checks,
    # but the source lists are changed only after an administrator chooses an
    # action in Telegram. This keeps discovery and moderation separate.
    def keep_lists_unchanged(path, values, header):
        print(f"Candidate recommendations collected; {path.name} is awaiting Telegram moderation.")

    nightly_discovery.write_sources = keep_lists_unchanged
    nightly_discovery.MANUAL_RUN = False

    result = nightly_discovery.main()

    state = nightly_discovery.load_discovery_state()
    recommended = [str(value) for value in state.get("promoted", []) if str(value)]
    state["recommended_for_primary"] = recommended
    state["promoted"] = []
    state["catalog_size"] = len(
        nightly_discovery.unique(monitor.read_list(nightly_discovery.CATALOG_PATH))
    )
    state["active_size"] = len(
        nightly_discovery.unique(monitor.read_list(nightly_discovery.ACTIVE_PATH))
    )
    state["moved_to_nightly"] = moved_to_nightly
    nightly_discovery.save_discovery_state(state)

    if manual_run:
        recommended_text = ", ".join(f"@{value}" for value in recommended) or "нет"
        monitor.send_message(
            "✅ <b>Ночной поиск завершён</b>\n\n"
            f"Каналов в ночной проверке: {state.get('catalog_size', 0)}\n"
            f"Перенесено из основной базы: {len(moved_to_nightly)}\n"
            f"Рекомендованы в основную: {html.escape(recommended_text)}\n"
            f"Новых уведомлений о колёсах: {state.get('notifications', 0)}\n"
            f"Повторов подавлено: {state.get('duplicate_wheels', 0)}\n"
            f"Ошибок: {state.get('error_count', 0)}\n\n"
            "Каналы не перенесены обратно автоматически. Решение доступно в разделе кандидатов."
        )

    return result


if __name__ == "__main__":
    raise SystemExit(main())
