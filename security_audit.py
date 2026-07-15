from __future__ import annotations

import argparse
import json
import re
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable

ROOT = Path(__file__).resolve().parent
TOKEN_RE = re.compile(r"(?<![A-Za-z0-9_])\d{6,12}:[A-Za-z0-9_-]{30,}(?![A-Za-z0-9_])")
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
ENCRYPTED_STATE = "bot_private_state.enc.json"


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


def _public_json_findings(path: Path) -> list[Finding]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return [Finding("invalid_public_json", path.name, type(exc).__name__)]
    if not isinstance(value, dict):
        return [Finding("invalid_public_json", path.name, "root is not an object")]
    findings: list[Finding] = []
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


def _encrypted_state_findings(path: Path) -> list[Finding]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return [Finding("invalid_encrypted_state", path.name, type(exc).__name__)]
    if not isinstance(value, dict):
        return [Finding("invalid_encrypted_state", path.name, "root is not an object")]
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
    for path in PUBLIC_PERSONAL_STATE:
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
    clean = ROOT / "bot_access.json"
    findings = _public_json_findings(clean)
    assert not findings, findings
    print("security audit self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--current", action="store_true")
    parser.add_argument("--history", action="store_true")
    parser.add_argument("--history-output")
    parser.add_argument("--fail-on-history", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return 0

    run_current = args.current or not args.history
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
