"""Git commit and run identifiers recorded with every report and bundle."""

import subprocess
from datetime import UTC, datetime
from pathlib import Path


def git_sha(repository: Path) -> str:
    """The HEAD commit, with -dirty when tracked files have uncommitted changes."""
    try:
        sha = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=repository, capture_output=True, text=True, check=True
        ).stdout.strip()
        dirty = subprocess.run(
            ["git", "status", "--porcelain", "--untracked-files=no"],
            cwd=repository,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"
    return f"{sha}-dirty" if dirty else sha


def run_id(sha: str, now: datetime | None = None) -> str:
    moment = now or datetime.now(UTC)
    return f"{moment:%Y%m%dT%H%M%SZ}-{sha[:7]}"
