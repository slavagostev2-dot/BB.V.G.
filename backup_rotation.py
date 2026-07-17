from __future__ import annotations

import argparse
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any, Protocol

BACKUP_PREFIX = "backup/"
DEFAULT_KEEP_COUNT = 3
MAX_BACKUP_REFS = 50


class BackupRotationError(RuntimeError):
    """Raised when backup inventory or verification is unsafe."""


@dataclass(frozen=True)
class BackupRecord:
    name: str
    sha: str
    committed_at: str


@dataclass(frozen=True)
class RotationPlan:
    selected: BackupRecord | None
    retained: tuple[BackupRecord, ...]
    obsolete: tuple[BackupRecord, ...]


class GitHubClient(Protocol):
    def request(self, method: str, path: str) -> Any: ...


def validate_backup_name(name: str) -> None:
    if not name.startswith(BACKUP_PREFIX) or name == BACKUP_PREFIX:
        raise BackupRotationError(f"Unsafe backup ref: {name!r}")
    if name.startswith("refs/") or ".." in name.split("/"):
        raise BackupRotationError(f"Unsafe backup ref: {name!r}")


def plan_rotation(
    records: list[BackupRecord],
    *,
    created_ref: str = "",
    keep_count: int = DEFAULT_KEEP_COUNT,
) -> RotationPlan:
    if keep_count != DEFAULT_KEEP_COUNT:
        raise BackupRotationError("KEEP_BACKUPS must remain exactly 3")
    if len(records) > MAX_BACKUP_REFS:
        raise BackupRotationError(
            f"Refusing unexpected backup ref count: {len(records)}"
        )

    names: set[str] = set()
    for record in records:
        validate_backup_name(record.name)
        if record.name in names:
            raise BackupRotationError(f"Duplicate backup ref: {record.name}")
        names.add(record.name)

    if not records:
        if created_ref:
            validate_backup_name(created_ref)
            raise BackupRotationError(
                "Selected backup ref is missing from repository inventory"
            )
        return RotationPlan(None, (), ())

    selected_name = created_ref.strip()
    if selected_name:
        validate_backup_name(selected_name)
    else:
        selected_name = max(
            records, key=lambda item: (item.committed_at, item.name)
        ).name

    by_name = {record.name: record for record in records}
    selected = by_name.get(selected_name)
    if selected is None:
        raise BackupRotationError(
            "Selected backup ref is missing from repository inventory"
        )

    ordered = sorted(
        records,
        key=lambda item: (
            item.name == selected_name,
            item.committed_at,
            item.name,
        ),
        reverse=True,
    )
    retained = tuple(ordered[:keep_count])
    obsolete = tuple(ordered[keep_count:])
    if selected not in retained:
        raise BackupRotationError("Newly created backup was not retained")
    return RotationPlan(selected, retained, obsolete)


class HttpGitHubClient:
    def __init__(self, repository: str, token: str) -> None:
        if not repository or "/" not in repository:
            raise BackupRotationError("Repository must be owner/name")
        if not token:
            raise BackupRotationError("GH_TOKEN is required")
        self.api_root = f"https://api.github.com/repos/{repository}"
        self.headers = {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "User-Agent": "bbvg-backup-retention",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    def request(self, method: str, path: str) -> Any:
        request = urllib.request.Request(
            self.api_root + path,
            headers=self.headers,
            method=method,
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                body = response.read()
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise BackupRotationError(
                f"GitHub API {method} {path} failed: HTTP {exc.code}: {detail}"
            ) from exc
        return json.loads(body) if body else None


def ref_path(short_name: str) -> str:
    return "/git/ref/heads/" + urllib.parse.quote(short_name, safe="/")


def delete_ref_path(short_name: str) -> str:
    validate_backup_name(short_name)
    return "/git/refs/heads/" + urllib.parse.quote(short_name, safe="/")


def list_backup_refs(client: GitHubClient) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    page = 1
    while True:
        batch = client.request(
            "GET", f"/git/matching-refs/heads/backup/?per_page=100&page={page}"
        )
        if not isinstance(batch, list):
            raise BackupRotationError("Invalid GitHub backup ref inventory")
        refs.extend(batch)
        if len(batch) < 100:
            return refs
        page += 1


def load_records(client: GitHubClient) -> list[BackupRecord]:
    refs = list_backup_refs(client)
    if len(refs) > MAX_BACKUP_REFS:
        raise BackupRotationError(
            f"Refusing unexpected backup ref count: {len(refs)}"
        )
    commit_cache: dict[str, dict[str, Any]] = {}
    records: list[BackupRecord] = []
    prefix = "refs/heads/"
    for ref in refs:
        full_name = str(ref.get("ref") or "")
        if not full_name.startswith(prefix + BACKUP_PREFIX):
            raise BackupRotationError(
                f"Unexpected ref outside backup namespace: {full_name}"
            )
        short_name = full_name[len(prefix) :]
        validate_backup_name(short_name)
        sha = str((ref.get("object") or {}).get("sha") or "")
        if len(sha) != 40:
            raise BackupRotationError(f"Invalid SHA for {short_name}")
        commit = commit_cache.get(sha)
        if commit is None:
            commit = client.request("GET", f"/commits/{sha}")
            if not isinstance(commit, dict):
                raise BackupRotationError(f"Invalid commit payload for {short_name}")
            commit_cache[sha] = commit
        committed_at = str(
            (((commit.get("commit") or {}).get("committer") or {}).get("date"))
            or ""
        )
        if not committed_at:
            raise BackupRotationError(f"Missing commit time for {short_name}")
        records.append(BackupRecord(short_name, sha, committed_at))
    return records


def verify_ancestor(
    client: GitHubClient,
    record: BackupRecord,
    *,
    default_sha: str,
    default_branch: str,
) -> None:
    compare = client.request("GET", f"/compare/{record.sha}...{default_sha}")
    if not isinstance(compare, dict):
        raise BackupRotationError(f"Invalid compare result for {record.name}")
    if compare.get("status") not in {"ahead", "identical"}:
        raise BackupRotationError(
            f"Refusing {record.name}: it is not an ancestor of {default_branch}; "
            f"status={compare.get('status')}"
        )
    merge_base = compare.get("merge_base_commit") or {}
    if merge_base.get("sha") != record.sha:
        raise BackupRotationError(
            f"Refusing {record.name}: it has unique commits"
        )


def summary_markdown(plan: RotationPlan, *, dry_run: bool) -> str:
    mode = "dry run" if dry_run else "applied"
    lines = [
        "## Backup branch rotation",
        "",
        f"Mode: **{mode}**.",
        "",
        f"Retained: {len(plan.retained)}; deleted/planned: {len(plan.obsolete)}.",
        "",
        "### Retained",
    ]
    if plan.retained:
        lines.extend(f"- `{item.name}` at `{item.sha}`" for item in plan.retained)
    else:
        lines.append("- None")
    lines.extend(["", "### Deleted / planned"])
    if plan.obsolete:
        lines.extend(f"- `{item.name}` at `{item.sha}`" for item in plan.obsolete)
    else:
        lines.append("- None")
    return "\n".join(lines) + "\n"


def rotate(
    client: GitHubClient,
    *,
    created_ref: str = "",
    keep_count: int = DEFAULT_KEEP_COUNT,
    dry_run: bool = False,
) -> RotationPlan:
    repository_info = client.request("GET", "")
    if not isinstance(repository_info, dict):
        raise BackupRotationError("Invalid repository payload")
    default_branch = str(repository_info.get("default_branch") or "")
    if not default_branch:
        raise BackupRotationError("Repository has no default branch")
    default_ref = client.request("GET", ref_path(default_branch))
    default_sha = str(((default_ref or {}).get("object") or {}).get("sha") or "")
    if len(default_sha) != 40:
        raise BackupRotationError("Unable to resolve default branch SHA")

    records = load_records(client)
    plan = plan_rotation(
        records, created_ref=created_ref, keep_count=keep_count
    )
    if not records:
        return plan

    # All ordinary backups must be safe rollback points before any deletion.
    # A single failed verification leaves the complete existing pool unchanged.
    for record in records:
        verify_ancestor(
            client,
            record,
            default_sha=default_sha,
            default_branch=default_branch,
        )

    if not dry_run:
        for record in plan.obsolete:
            client.request("DELETE", delete_ref_path(record.name))

        remaining = load_records(client)
        remaining_names = sorted(item.name for item in remaining)
        expected_names = sorted(item.name for item in plan.retained)
        if remaining_names != expected_names:
            raise BackupRotationError(
                "Post-rotation inventory mismatch: "
                f"expected={expected_names}, actual={remaining_names}"
            )
        if len(remaining_names) != min(len(records), keep_count):
            raise BackupRotationError("Post-rotation backup count is invalid")
        if plan.selected and plan.selected.name not in remaining_names:
            raise BackupRotationError("Newly created backup was not retained")
    return plan


class FakeClient:
    def __init__(
        self,
        records: list[BackupRecord],
        *,
        bad_name: str = "",
    ) -> None:
        self.records = list(records)
        self.bad_name = bad_name
        self.deleted: list[str] = []
        self.default_sha = "f" * 40

    def request(self, method: str, path: str) -> Any:
        if method == "GET" and path == "":
            return {"default_branch": "main"}
        if method == "GET" and path == ref_path("main"):
            return {"object": {"sha": self.default_sha}}
        if method == "GET" and path.startswith(
            "/git/matching-refs/heads/backup/"
        ):
            return [
                {
                    "ref": f"refs/heads/{item.name}",
                    "object": {"sha": item.sha},
                }
                for item in self.records
            ]
        if method == "GET" and path.startswith("/commits/"):
            sha = path.rsplit("/", 1)[-1]
            item = next(record for record in self.records if record.sha == sha)
            return {"commit": {"committer": {"date": item.committed_at}}}
        if method == "GET" and path.startswith("/compare/"):
            sha = path.removeprefix("/compare/").split("...", 1)[0]
            item = next(record for record in self.records if record.sha == sha)
            if item.name == self.bad_name:
                return {
                    "status": "diverged",
                    "merge_base_commit": {"sha": "0" * 40},
                }
            return {
                "status": "ahead",
                "merge_base_commit": {"sha": sha},
            }
        if method == "DELETE":
            encoded = path.removeprefix("/git/refs/heads/")
            name = urllib.parse.unquote(encoded)
            validate_backup_name(name)
            self.deleted.append(name)
            self.records = [record for record in self.records if record.name != name]
            return None
        raise AssertionError(f"Unexpected fake request: {method} {path}")


def fixture_records(count: int) -> list[BackupRecord]:
    return [
        BackupRecord(
            f"backup/item-{index}",
            f"{index + 1:040x}",
            f"2026-07-{index + 1:02d}T00:00:00Z",
        )
        for index in range(count)
    ]


def self_test() -> None:
    for count in range(4):
        records = fixture_records(count)
        plan = plan_rotation(records)
        assert len(plan.retained) == count
        assert not plan.obsolete

    records = fixture_records(4)
    plan = plan_rotation(records, created_ref="backup/item-3")
    assert [item.name for item in plan.retained] == [
        "backup/item-3",
        "backup/item-2",
        "backup/item-1",
    ]
    assert [item.name for item in plan.obsolete] == ["backup/item-0"]

    # A newly created ref is retained even when its commit timestamp is older.
    plan = plan_rotation(records, created_ref="backup/item-0")
    assert plan.retained[0].name == "backup/item-0"
    assert "backup/item-0" not in {item.name for item in plan.obsolete}

    client = FakeClient(fixture_records(4))
    applied = rotate(client, created_ref="backup/item-3")
    assert client.deleted == ["backup/item-0"]
    assert len(applied.retained) == 3

    # Applying the planner again is idempotent.
    second = rotate(client, created_ref="backup/item-3")
    assert not second.obsolete
    assert client.deleted == ["backup/item-0"]

    # Dry-run verifies but never deletes.
    client = FakeClient(fixture_records(4))
    dry = rotate(client, created_ref="backup/item-3", dry_run=True)
    assert [item.name for item in dry.obsolete] == ["backup/item-0"]
    assert not client.deleted
    assert len(client.records) == 4

    # Failed verification performs no deletion.
    client = FakeClient(fixture_records(4), bad_name="backup/item-1")
    try:
        rotate(client, created_ref="backup/item-3")
    except BackupRotationError:
        pass
    else:
        raise AssertionError("Unsafe backup verification unexpectedly passed")
    assert not client.deleted
    assert len(client.records) == 4

    for unsafe in ("main", "backup/", "refs/heads/backup/item", "backup/../main"):
        try:
            validate_backup_name(unsafe)
        except BackupRotationError:
            continue
        raise AssertionError(f"Unsafe name passed validation: {unsafe}")

    print("BB V.G. backup rotation self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository", default=os.environ.get("GITHUB_REPOSITORY", ""))
    parser.add_argument("--token", default=os.environ.get("GH_TOKEN", ""))
    parser.add_argument("--created-ref", default=os.environ.get("CREATED_BACKUP_REF", ""))
    parser.add_argument("--keep", type=int, default=DEFAULT_KEEP_COUNT)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()

    if args.self_test:
        self_test()
        return 0

    client = HttpGitHubClient(args.repository, args.token)
    plan = rotate(
        client,
        created_ref=args.created_ref,
        keep_count=args.keep,
        dry_run=args.dry_run,
    )
    if plan.selected is None:
        print("No backup refs exist; nothing to rotate")
    else:
        mode = "dry-run" if args.dry_run else "complete"
        print(f"Backup rotation {mode}")
        for item in plan.retained:
            print(f"KEEP {item.name} {item.sha} {item.committed_at}")
        for item in plan.obsolete:
            print(f"DELETE {item.name} {item.sha} {item.committed_at}")

    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        with open(summary_path, "a", encoding="utf-8") as summary:
            summary.write(summary_markdown(plan, dry_run=args.dry_run))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
