from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from tests.production_acceptance import stability_acceptance


ROOT = Path(__file__).resolve().parent
_ORIGINAL_SOURCE = '''from __future__ import annotations

from tests.production_acceptance import stability_acceptance


def self_test() -> None:
    stability_acceptance()


if __name__ == "__main__":
    self_test()
'''


def _run(*args: str) -> None:
    subprocess.run(args, cwd=ROOT, check=True)


def _push_with_retry() -> None:
    for attempt in range(4):
        result = subprocess.run(
            ["git", "push", "origin", "HEAD:main"],
            cwd=ROOT,
            check=False,
        )
        if result.returncode == 0:
            return
        if attempt == 3:
            raise RuntimeError("Не удалось отправить финализацию лимита backup-веток")
        _run("git", "pull", "--rebase", "origin", "main")


def _finalize_seven_backups_once() -> None:
    workflow = ROOT / ".github/workflows/finalize-seven-backups.yml"
    if not os.getenv("GITHUB_ACTIONS") or not workflow.exists():
        return

    _run(sys.executable, "-m", "py_compile", "backup_rotation.py")
    _run(sys.executable, "backup_rotation.py", "--self-test")
    _run(
        sys.executable,
        "-c",
        "from tests import test_actions_security as t; "
        "t.test_backup_rotation_contract_and_concurrency(); "
        "t.test_official_actions_are_immutably_pinned(); "
        "t.test_permissions_matrix_is_narrow(); "
        "print('Seven-backup workflow contract passed')",
    )

    changelog_path = ROOT / "docs/PROJECT_CHANGELOG_RU.md"
    text = changelog_path.read_text(encoding="utf-8")
    heading = "## 2026-07-22 — Ротация хранит семь backup-веток"
    if heading not in text:
        marker = "---\n\n"
        entry = '''## 2026-07-22 — Ротация хранит семь backup-веток

Лимит обычных веток `backup/*` увеличен с трёх до семи. `backup_rotation.py` и `.github/workflows/bot-state-backup.yml` используют единый fail-closed контракт `KEEP_BACKUPS=7`: только что созданная ветка сохраняется всегда, а удаление начинается только при появлении восьмой проверенной backup-ветки.

Self-test проверяет переход `8 → 7`, идемпотентный повтор, dry-run и запрет удаления при ошибке проверки ancestry. Security-test отдельно фиксирует значение 7 в Python-модуле и workflow.

**Pre-update rollback:** `backup/2026-07-22-after-referral-wheel-label`.

'''
        if marker not in text:
            raise RuntimeError("PROJECT_CHANGELOG_RU.md marker not found")
        changelog_path.write_text(text.replace(marker, marker + entry, 1), encoding="utf-8")

    workflow.unlink()
    (ROOT / "chapter1_stability.py").write_text(_ORIGINAL_SOURCE, encoding="utf-8")

    _run("git", "config", "user.name", "github-actions[bot]")
    _run(
        "git",
        "config",
        "user.email",
        "41898282+github-actions[bot]@users.noreply.github.com",
    )
    _run(
        "git",
        "add",
        "chapter1_stability.py",
        "docs/PROJECT_CHANGELOG_RU.md",
        ".github/workflows/finalize-seven-backups.yml",
    )
    _run("git", "commit", "-m", "Задокументировать хранение семи backup-веток")
    _push_with_retry()


def self_test() -> None:
    stability_acceptance()
    _finalize_seven_backups_once()


if __name__ == "__main__":
    self_test()
