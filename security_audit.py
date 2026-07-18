from __future__ import annotations

import argparse
import json
import re
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parent
TOKEN_RE = re.compile(r"(?<![A-Za-z0-9_])\d{6,12}:[A-Za-z0-9_-]{30,}(?![A-Za-z0-9_])")
HEX_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")
PRIVATE_KEY_MARKERS = (
    "-----BEGIN " + "PRIVATE KEY-----",
    "-----BEGIN RSA " + "PRIVATE KEY-----",
    "-----BEGIN OPENSSH " + "PRIVATE KEY-----",
)
FORBIDDEN_BASENAMES = {".env", "id_rsa", "id_ed25519"}
FORBIDDEN_SUFFIXES = {".session", ".pem", ".p12", ".pfx", ".key"}
TEXT_SUFFIXES = {
    ".py", ".json", ".yml", ".yaml", ".md", ".txt", ".toml", ".ini", ".cfg",
    ".js", ".css", ".html", ".sql", ".sh",
}
PUBLIC_PERSONAL_STATE = ("bot_access.json", "source_requests.json")
PUBLIC_DIAGNOSTIC_STATE = "system_check_state.json"
DELIVERY_STATE = "notification_delivery_state.json"
ENCRYPTED_STATE = "bot_private_state.enc.json"
PUBLIC_RUNTIME_PROVENANCE = (
    "state.json",
    "source_stats.json",
    "candidate_moderation.json",
)
VOTE_ACTOR_RE = re.compile(r"^vote_[0-9a-f]{32}$")
HISTORY_PERSONAL_STATE = (
    *PUBLIC_PERSONAL_STATE,
    PUBLIC_DIAGNOSTIC_STATE,
    *PUBLIC_RUNTIME_PROVENANCE,
)


@dataclass(frozen=True)
class Finding:
    code: str
    path: str
    detail: str


def _git(*args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or f"git {' '.join(args)} failed")
    return result.stdout


def tracked_files() -> list[Path]:
    return [ROOT / line for line in _git("ls-files").splitlines() if line]


def _load_object(path: Path, code: str) -> tuple[dict[str, Any] | None, list[Finding]]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return None, [Finding(code, path.name, type(exc).__name__)]
    if not isinstance(value, dict):
        return None, [Finding(code, path.name, "root is not an object")]
    return value, []


def _public_json_findings(path: Path) -> list[Finding]:
    value, findings = _load_object(path, "invalid_public_json")
    if value is None:
        return findings
    if path.name == "bot_access.json":
        checks = {
            "owner_id": value.get("owner_id"),
            "admins": value.get("admins"),
            "notification_recipients": value.get("notification_recipients"),
            "users": value.get("users"),
        }
        for field, raw in checks.items():
            if raw not in (None, "", [], {}):
                findings.append(Finding("public_personal_data", path.name, f"non-empty {field}"))
    elif path.name == "source_requests.json" and value.get("requests") not in (None, {}):
        findings.append(Finding("public_personal_data", path.name, "non-empty requests"))
    return findings


def _diagnostic_contains_personal(value: dict[str, Any]) -> bool:
    routing = value.get("notification_routing")
    if not isinstance(routing, dict):
        return False
    forbidden = {"admin_recipients", "user_recipients", "admin_kinds", "user_kinds"}
    if forbidden & set(routing):
        return True
    return any(isinstance(raw, list) for raw in routing.values())


def _diagnostic_findings(path: Path) -> list[Finding]:
    value, findings = _load_object(path, "invalid_public_diagnostic")
    if value is None:
        return findings
    if _diagnostic_contains_personal(value):
        findings.append(
            Finding(
                "public_personal_data",
                path.name,
                "notification routing contains recipient identifiers",
            )
        )
    integrity = value.get("notification_integrity")
    if isinstance(integrity, dict) and integrity.get("contains_personal_fields") is not False:
        findings.append(
            Finding(
                "public_personal_data",
                path.name,
                "notification integrity diagnostic is not explicitly anonymized",
            )
        )
    return findings


def _delivery_state_findings(path: Path) -> list[Finding]:
    value, findings = _load_object(path, "invalid_delivery_state")
    if value is None:
        return findings
    if value.get("format") not in {
        "bbvg-notification-delivery-v2",
        "bbvg-notification-delivery-v3",
    }:
        findings.append(Finding("invalid_delivery_state", path.name, "unsupported format"))
    if value.get("algorithm") != "HMAC-SHA256":
        findings.append(Finding("invalid_delivery_state", path.name, "unsupported algorithm"))
    allowed = {"format", "version", "algorithm", "retention_seconds", "entries", "claims"}
    unexpected = sorted(set(value) - allowed)
    if unexpected:
        findings.append(
            Finding("public_personal_data", path.name, f"unexpected fields: {', '.join(unexpected)}")
        )
    entries = value.get("entries")
    if not isinstance(entries, dict):
        findings.append(Finding("invalid_delivery_state", path.name, "entries is not an object"))
        return findings
    invalid_digests = [str(key) for key in entries if not HEX_DIGEST_RE.fullmatch(str(key))]
    if invalid_digests:
        findings.append(
            Finding(
                "public_personal_data",
                path.name,
                f"non-HMAC delivery keys: {len(invalid_digests)}",
            )
        )
    if any(not isinstance(timestamp, str) for timestamp in entries.values()):
        findings.append(Finding("invalid_delivery_state", path.name, "non-string timestamps"))
    claims = value.get("claims", {})
    if not isinstance(claims, dict):
        findings.append(Finding("invalid_delivery_state", path.name, "claims is not an object"))
    else:
        invalid_claims = [str(key) for key in claims if not HEX_DIGEST_RE.fullmatch(str(key))]
        if invalid_claims:
            findings.append(
                Finding(
                    "public_personal_data",
                    path.name,
                    f"non-HMAC claim keys: {len(invalid_claims)}",
                )
            )
        if any(not isinstance(timestamp, str) for timestamp in claims.values()):
            findings.append(Finding("invalid_delivery_state", path.name, "non-string claim timestamps"))
    return findings



def _runtime_provenance_value_findings(
    path_name: str, value: dict[str, Any]
) -> list[Finding]:
    findings: list[Finding] = []

    def non_admin_count(collection_name: str, field: str) -> int:
        collection = value.get(collection_name)
        if not isinstance(collection, dict):
            return 0
        return sum(
            1
            for record in collection.values()
            if isinstance(record, dict)
            and field in record
            and str(record.get(field) or "").strip().casefold() != "admin"
        )

    checks: tuple[tuple[str, str], ...] = ()
    if path_name == "state.json":
        checks = (
            ("inactive_wheels", "marked_by"),
            ("recently_completed_wheels", "confirmed_finished_by"),
        )
    elif path_name == "source_stats.json":
        checks = (("admin_wheel_decisions", "actor"),)
    elif path_name == "candidate_moderation.json":
        checks = (("ignored", "ignored_by"),)

    for collection_name, field in checks:
        count = non_admin_count(collection_name, field)
        if count:
            findings.append(
                Finding(
                    "public_personal_data",
                    path_name,
                    f"non-anonymized {collection_name}.{field}: {count}",
                )
            )

    if path_name == "source_stats.json":
        votes = value.get("personal_wheel_votes")
        invalid = 0
        if isinstance(votes, dict):
            for record in votes.values():
                actor = (
                    str(record.get("actor") or "").strip().casefold()
                    if isinstance(record, dict)
                    else ""
                )
                if not VOTE_ACTOR_RE.fullmatch(actor):
                    invalid += 1
        if invalid:
            findings.append(
                Finding(
                    "public_personal_data",
                    path_name,
                    f"invalid personal_wheel_votes actor token: {invalid}",
                )
            )
    return findings


def _runtime_provenance_findings(path: Path) -> list[Finding]:
    value, findings = _load_object(path, "invalid_public_runtime_state")
    if value is None:
        return findings
    return findings + _runtime_provenance_value_findings(path.name, value)


def _normalize_runtime_provenance_value(
    path_name: str, value: dict[str, Any]
) -> bool:
    changed = False
    checks: tuple[tuple[str, str], ...] = ()
    if path_name == "state.json":
        checks = (
            ("inactive_wheels", "marked_by"),
            ("recently_completed_wheels", "confirmed_finished_by"),
        )
    elif path_name == "source_stats.json":
        checks = (("admin_wheel_decisions", "actor"),)
    elif path_name == "candidate_moderation.json":
        checks = (("ignored", "ignored_by"),)

    for collection_name, field in checks:
        collection = value.get(collection_name)
        if not isinstance(collection, dict):
            continue
        for record in collection.values():
            if not isinstance(record, dict) or field not in record:
                continue
            if str(record.get(field) or "").strip().casefold() != "admin":
                record[field] = "admin"
                changed = True
    return changed


def migrate_current(paths: Iterable[Path] | None = None) -> list[str]:
    changed_paths: list[str] = []
    selected = list(paths or (ROOT / name for name in PUBLIC_RUNTIME_PROVENANCE))
    for path in selected:
        value, findings = _load_object(path, "invalid_public_runtime_state")
        if value is None:
            detail = findings[0].detail if findings else "invalid JSON"
            raise RuntimeError(f"Cannot migrate {path.name}: {detail}")
        if not _normalize_runtime_provenance_value(path.name, value):
            continue
        temporary = path.with_name(path.name + ".tmp")
        temporary.write_text(
            json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        temporary.replace(path)
        changed_paths.append(path.name)
    return changed_paths


def _encrypted_state_findings(path: Path) -> list[Finding]:
    value, findings = _load_object(path, "invalid_encrypted_state")
    if value is None:
        return findings
    if str(value.get("format") or "") not in {"bbvg-bot-state-v1", "bbvg-bot-state-v2"}:
        return [Finding("invalid_encrypted_state", path.name, "unsupported format")]
    forbidden = {
        "owner_id", "admins", "users", "chat_id", "username", "first_name", "last_name",
        "requester_id", "requester_chat_id",
    }
    leaked = sorted(forbidden & set(value))
    return [Finding("plaintext_in_encrypted_state", path.name, ", ".join(leaked))] if leaked else []


def scan_current(paths: Iterable[Path] | None = None) -> list[Finding]:
    findings: list[Finding] = []
    for path in paths or tracked_files():
        relative = path.relative_to(ROOT).as_posix()
        if path.name in FORBIDDEN_BASENAMES or path.suffix.casefold() in FORBIDDEN_SUFFIXES:
            findings.append(Finding("forbidden_sensitive_file", relative, "tracked sensitive file type"))
            continue
        if path.name in PUBLIC_PERSONAL_STATE:
            findings.extend(_public_json_findings(path))
        if path.name == PUBLIC_DIAGNOSTIC_STATE:
            findings.extend(_diagnostic_findings(path))
        if path.name == DELIVERY_STATE:
            findings.extend(_delivery_state_findings(path))
        if path.name in PUBLIC_RUNTIME_PROVENANCE:
            findings.extend(_runtime_provenance_findings(path))
        if path.name == ENCRYPTED_STATE:
            findings.extend(_encrypted_state_findings(path))
            continue
        if path.suffix.casefold() not in TEXT_SUFFIXES:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if TOKEN_RE.search(text):
            findings.append(Finding("telegram_bot_token", relative, "token-shaped value found"))
        for marker in PRIVATE_KEY_MARKERS:
            if marker in text:
                findings.append(Finding("private_key", relative, marker))
    return findings


def history_report() -> dict:
    """Report legacy personal-state revisions without printing their contents."""

    rows: list[dict[str, str]] = []
    for path in HISTORY_PERSONAL_STATE:
        commits = [line for line in _git("log", "--all", "--format=%H", "--", path).splitlines() if line]
        seen: set[str] = set()
        for commit in commits:
            if commit in seen:
                continue
            seen.add(commit)
            result = subprocess.run(
                ["git", "show", f"{commit}:{path}"],
                cwd=ROOT,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                check=False,
            )
            if result.returncode:
                continue
            try:
                value = json.loads(result.stdout)
            except json.JSONDecodeError:
                continue
            contains_personal = False
            if isinstance(value, dict) and path == "bot_access.json":
                contains_personal = bool(
                    value.get("owner_id")
                    or value.get("admins")
                    or value.get("notification_recipients")
                    or value.get("users")
                )
            elif isinstance(value, dict) and path == "source_requests.json":
                contains_personal = bool(value.get("requests"))
            elif isinstance(value, dict) and path == PUBLIC_DIAGNOSTIC_STATE:
                contains_personal = _diagnostic_contains_personal(value)
            elif isinstance(value, dict) and path in PUBLIC_RUNTIME_PROVENANCE:
                contains_personal = bool(
                    _runtime_provenance_value_findings(path, value)
                )
            if contains_personal:
                rows.append({"path": path, "commit": commit[:12]})
    return {
        "status": "legacy_history_contains_personal_state" if rows else "clean",
        "destructive_cleanup_required": bool(rows),
        "revisions": rows,
    }


def self_test() -> None:
    token_sample = "123456789:" + "A" * 35
    assert TOKEN_RE.search(token_sample)
    assert not _public_json_findings(ROOT / "bot_access.json")
    assert not _diagnostic_findings(ROOT / PUBLIC_DIAGNOSTIC_STATE)
    assert not _delivery_state_findings(ROOT / DELIVERY_STATE)
    sample = {
        "inactive_wheels": {"wheel": {"marked_by": "123456789"}},
        "recently_completed_wheels": {
            "wheel": {"confirmed_finished_by": "123456789"}
        },
    }
    assert _runtime_provenance_value_findings("state.json", sample)
    assert _normalize_runtime_provenance_value("state.json", sample)
    assert not _runtime_provenance_value_findings("state.json", sample)
    assert sample["inactive_wheels"]["wheel"]["marked_by"] == "admin"
    print("security audit self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--current", action="store_true")
    parser.add_argument("--history", action="store_true")
    parser.add_argument("--history-output")
    parser.add_argument("--fail-on-history", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--migrate-current", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return 0

    if args.migrate_current:
        changed = migrate_current()
        print(
            "Current runtime provenance migration: "
            + (", ".join(changed) if changed else "unchanged")
        )

    run_current = args.current or args.migrate_current or not args.history
    if run_current:
        findings = scan_current()
        for finding in findings:
            print(json.dumps(asdict(finding), ensure_ascii=False, sort_keys=True))
        if findings:
            print(f"Current-tree security audit failed: {len(findings)} finding(s).")
            return 2
        print("Current-tree security audit passed.")

    if args.history:
        report = history_report()
        rendered = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        if args.history_output:
            Path(args.history_output).write_text(rendered, encoding="utf-8")
        print(
            "History security audit: "
            f"status={report['status']}, revisions={len(report['revisions'])}"
        )
        if args.fail_on_history and report["destructive_cleanup_required"]:
            return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
