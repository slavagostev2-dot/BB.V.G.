from __future__ import annotations

import html

import monitor
import nightly_discovery
import notification_router

notification_router.install(monitor)


def main() -> int:
    manual_run = nightly_discovery.MANUAL_RUN

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
    nightly_discovery.save_discovery_state(state)

    if manual_run:
        recommended_text = ", ".join(f"@{value}" for value in recommended) or "нет"
        monitor.send_message(
            "✅ <b>Ночной поиск завершён</b>\n\n"
            f"Каналов в ночной проверке: {state.get('catalog_size', 0)}\n"
            f"Рекомендованы в основную: {html.escape(recommended_text)}\n"
            f"Новых уведомлений о колёсах: {state.get('notifications', 0)}\n"
            f"Повторов подавлено: {state.get('duplicate_wheels', 0)}\n"
            f"Ошибок: {state.get('error_count', 0)}\n\n"
            "Каналы не перенесены автоматически. Решение доступно в разделе кандидатов."
        )

    return result


if __name__ == "__main__":
    raise SystemExit(main())
