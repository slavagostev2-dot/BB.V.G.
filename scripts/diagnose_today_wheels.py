from __future__ import annotations

import hashlib
import html
import json
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit

import requests
from bs4 import BeautifulSoup

UTC = timezone.utc
BARNAUL = timezone(timedelta(hours=7))
ROOT = Path(__file__).resolve().parents[1]
LINK_RE = re.compile(
    r"(?<![A-Za-z0-9._-])(?:https?://)?(?:www\.)?betboom\.ru/freestream/[A-Za-z0-9._~-]+",
    re.IGNORECASE,
)
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "Chrome/126.0 Safari/537.36"
)


def normalize_url(value: str) -> str:
    raw = html.unescape(str(value or "")).strip().rstrip(".,;:!?)]}\"'")
    if not raw.lower().startswith(("http://", "https://")):
        raw = "https://" + raw
    parts = urlsplit(raw)
    host = parts.netloc.casefold()
    if host.startswith("www."):
        host = host[4:]
    return f"https://{host}{parts.path.rstrip('/')}"


def wheel_key(url: str) -> str:
    return urlsplit(normalize_url(url)).path.rstrip("/").rsplit("/", 1)[-1].casefold()


def notification_key(source: str, message_id: int, link: str) -> str:
    raw = f"{str(source).strip().lstrip('@').casefold()}:{int(message_id)}:{wheel_key(link)}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def parse_datetime(value: object) -> datetime | None:
    try:
        result = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
    except ValueError:
        return None
    return result if result.tzinfo else result.replace(tzinfo=UTC)


def read_sources() -> list[str]:
    values: list[str] = []
    seen: set[str] = set()
    for raw in (ROOT / "public_sources.txt").read_text(encoding="utf-8").splitlines():
        value = raw.split("#", 1)[0].strip().lstrip("@")
        key = value.casefold()
        if value and key not in seen:
            seen.add(key)
            values.append(value)
    return values


def fetch_source(source: str, stamp: int) -> tuple[str, list[dict], str]:
    url = f"https://telegram.me/s/{source}?bbvg_audit={stamp}"
    headers = {
        "User-Agent": USER_AGENT,
        "Cache-Control": "no-cache, no-store, max-age=0",
        "Pragma": "no-cache",
    }
    try:
        response = requests.get(url, timeout=12, headers=headers, allow_redirects=True)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")
        rows: list[dict] = []
        for node in soup.select("div.tgme_widget_message[data-post]"):
            data_post = str(node.get("data-post") or "")
            if "/" not in data_post:
                continue
            observed, raw_id = data_post.rsplit("/", 1)
            try:
                message_id = int(raw_id)
            except ValueError:
                continue
            time_node = node.select_one("time[datetime]")
            date = parse_datetime(time_node.get("datetime") if time_node else None)
            parts: list[str] = []
            text_node = node.select_one("div.tgme_widget_message_text")
            if text_node is not None:
                parts.append(text_node.get_text("\n", strip=True))
            for anchor in node.select("a[href]"):
                href = html.unescape(str(anchor.get("href") or "")).strip()
                if href:
                    parts.append(href)
            text = "\n".join(dict.fromkeys(part for part in parts if part))
            links = list(dict.fromkeys(normalize_url(match.group(0)) for match in LINK_RE.finditer(text)))
            rows.append(
                {
                    "requested_source": source,
                    "observed_source": observed or source,
                    "message_id": message_id,
                    "date": date.isoformat() if date else None,
                    "links": links,
                }
            )
        return source, rows, ""
    except Exception as exc:  # diagnostic must finish even with partial transport failure
        return source, [], f"{type(exc).__name__}: {exc}"


def inspect_betboom(url: str) -> dict:
    try:
        response = requests.post(
            "https://betboom.ru/api/streamer-wheel/action/get-info",
            json={"streamer_link": normalize_url(url)},
            headers={
                "Content-Type": "application/json",
                "User-Agent": USER_AGENT,
                "x-platform": "web",
            },
            timeout=12,
        )
        try:
            payload = response.json()
        except ValueError:
            return {"http": response.status_code, "error": "invalid_json", "body": response.text[:300]}
        info = payload.get("info") if isinstance(payload, dict) else None
        result = {
            "http": response.status_code,
            "code": payload.get("code") if isinstance(payload, dict) else None,
            "error": payload.get("error") if isinstance(payload, dict) else None,
            "info": info,
        }
        if isinstance(info, dict):
            start = parse_datetime(info.get("start_dttm"))
            try:
                duration = float(info.get("duration_min", info.get("duration_in_minutes")))
            except (TypeError, ValueError):
                duration = None
            deadline = start + timedelta(minutes=duration) if start and duration else None
            result["summary"] = {
                "action_id": info.get("action_id"),
                "start_dttm": start.isoformat() if start else None,
                "duration_minutes": duration,
                "deadline": deadline.isoformat() if deadline else None,
                "is_ended": info.get("is_ended"),
                "is_early": info.get("is_early"),
            }
        return result
    except Exception as exc:
        return {"error": f"{type(exc).__name__}: {exc}"}


def post_state(state: dict, row: dict, link: str, api: dict) -> dict:
    requested = str(row["requested_source"])
    observed = str(row["observed_source"])
    message_id = int(row["message_id"])
    key = wheel_key(link)
    notification_keys = sorted(
        {
            notification_key(requested, message_id, link),
            notification_key(observed, message_id, link),
        }
    )
    seen = state.get("seen") if isinstance(state.get("seen"), dict) else {}
    pending = state.get("pending_posts") if isinstance(state.get("pending_posts"), dict) else {}
    contexts = state.get("button_contexts") if isinstance(state.get("button_contexts"), dict) else {}
    collections: dict[str, object] = {}
    for name in (
        "active_wheels",
        "participating_wheels",
        "inactive_wheels",
        "activation_alerts",
        "url_alerts",
        "wheel_action_history",
        "auto_participation_events",
    ):
        value = state.get(name)
        if isinstance(value, dict) and key in value:
            collections[name] = value[key]
    matching_contexts = [
        token
        for token, value in contexts.items()
        if isinstance(value, dict)
        and (
            str(value.get("post_key") or "") in notification_keys
            or (
                str(value.get("wheel_key") or "").casefold() == key
                and int(value.get("message_id", 0) or 0) == message_id
            )
        )
    ]
    api_action = None
    summary = api.get("summary") if isinstance(api, dict) else None
    if isinstance(summary, dict):
        try:
            api_action = int(summary.get("action_id"))
        except (TypeError, ValueError):
            api_action = None
    stored_action = None
    for name in ("active_wheels", "wheel_action_history", "auto_participation_events"):
        value = collections.get(name)
        if isinstance(value, dict):
            try:
                stored_action = int(value.get("action_id"))
            except (TypeError, ValueError):
                continue
            else:
                break
    current_generation_recorded = bool(api_action and stored_action == api_action)
    return {
        "wheel": key,
        "source": requested,
        "observed_source": observed,
        "message_id": message_id,
        "message_date": row.get("date"),
        "url": link,
        "notification_keys": notification_keys,
        "seen": {item: seen[item] for item in notification_keys if item in seen},
        "pending": {item: pending[item] for item in notification_keys if item in pending},
        "button_context_tokens": matching_contexts,
        "collections": collections,
        "api": api,
        "api_action_id": api_action,
        "stored_action_id": stored_action,
        "current_generation_recorded": current_generation_recorded,
        "unprocessed_post": not any(item in seen or item in pending for item in notification_keys),
    }


def main() -> None:
    state = json.loads((ROOT / "state.json").read_text(encoding="utf-8"))
    stats = json.loads((ROOT / "source_stats.json").read_text(encoding="utf-8"))
    health = json.loads((ROOT / "source_health.json").read_text(encoding="utf-8"))

    print("=== STATE SUMMARY ===")
    print(
        json.dumps(
            {
                "notification_key_version": state.get("notification_key_version"),
                "last_run_kind": state.get("last_run_kind"),
                "last_run_summary": state.get("last_run_summary"),
                "seen_count": len(state.get("seen", {})),
                "pending_count": len(state.get("pending_posts", {})),
                "active_count": len(state.get("active_wheels", {})),
                "kolesaBB_health": health.get("sources", {}).get("kolesaBB"),
                "kolesaBB_stats": stats.get("sources", {}).get("kolesaBB"),
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )

    print("=== PRODUCTION RUNTIME KolesaBB ===")
    os.environ.setdefault("BOT_TOKEN", "test-bot-token")
    os.environ.setdefault("BOT_STATE_KEY", "test-state-key")
    os.environ.setdefault("BOT_CHAT_ID", "1")
    os.environ.setdefault("ADMIN_USER_ID", "1")
    os.environ.setdefault("BBVG_TEST_MODE", "1")
    os.environ.setdefault("TELEGRAM_WEB_DOMAIN", "telegram.me")
    try:
        import bbvg_monitor_main as runtime

        monitor = runtime.monitor
        messages = monitor.fetch_public_channel("kolesaBB")
        print(
            json.dumps(
                {
                    "fetch_public_channel_module": getattr(monitor.fetch_public_channel, "__module__", ""),
                    "fetch_all_sources_module": getattr(monitor.fetch_all_sources, "__module__", ""),
                    "message_ids": [item.message_id for item in messages],
                    "wheel_posts": [
                        {
                            "message_id": item.message_id,
                            "date": item.date.isoformat(),
                            "source": item.source,
                            "links": monitor.extract_links(item.text),
                        }
                        for item in messages
                        if monitor.extract_links(item.text)
                    ],
                },
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
        )
        batch, errors, empty = monitor.fetch_all_sources(["kolesaBB"])
        print(
            json.dumps(
                {
                    "batch_ids": [item.message_id for item in batch.get("kolesaBB", [])],
                    "batch_errors": errors,
                    "batch_empty": empty,
                },
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
        )
    except Exception as exc:
        print(json.dumps({"production_runtime_error": f"{type(exc).__name__}: {exc}"}, ensure_ascii=False))

    local_today = datetime.now(BARNAUL).date()
    day_start = datetime.combine(local_today, datetime.min.time(), tzinfo=BARNAUL).astimezone(UTC)
    sources = read_sources()
    stamp = int(time.time() * 1000)
    fetched: dict[str, list[dict]] = {}
    fetch_errors: dict[str, str] = {}
    with ThreadPoolExecutor(max_workers=24) as pool:
        futures = {
            pool.submit(fetch_source, source, stamp + index): source
            for index, source in enumerate(sources)
        }
        for future in as_completed(futures):
            source, rows, error = future.result()
            if error:
                fetch_errors[source] = error
            else:
                fetched[source] = rows

    candidates: list[tuple[dict, str]] = []
    for rows in fetched.values():
        for row in rows:
            date = parse_datetime(row.get("date"))
            if date is None or date < day_start:
                continue
            for link in row.get("links", []):
                candidates.append((row, link))

    unique_urls = sorted({normalize_url(link) for _, link in candidates})
    api_by_url: dict[str, dict] = {}
    with ThreadPoolExecutor(max_workers=12) as pool:
        futures = {pool.submit(inspect_betboom, url): url for url in unique_urls}
        for future in as_completed(futures):
            url = futures[future]
            api_by_url[url] = future.result()

    report = [
        post_state(state, row, normalize_url(link), api_by_url.get(normalize_url(link), {}))
        for row, link in candidates
    ]
    report.sort(key=lambda item: (str(item.get("message_date") or ""), item["source"].casefold(), item["message_id"]))

    print("=== TODAY AUDIT ===")
    print(
        json.dumps(
            {
                "barnaul_day": str(local_today),
                "day_start_utc": day_start.isoformat(),
                "configured_sources": len(sources),
                "fetched_sources": len(fetched),
                "fetch_errors": fetch_errors,
                "wheel_publications": len(report),
                "unique_wheels": len({item["wheel"] for item in report}),
                "unprocessed_publications": [item for item in report if item["unprocessed_post"]],
                "generation_gaps": [
                    item
                    for item in report
                    if item.get("api_action_id") and not item.get("current_generation_recorded")
                ],
                "all_publications": report,
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )

    print("=== TARGET API ===")
    for target in (
        "https://betboom.ru/freestream/pomidor1",
        "https://betboom.ru/freestream/CTOM22",
    ):
        print(json.dumps({"url": target, "result": inspect_betboom(target)}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
