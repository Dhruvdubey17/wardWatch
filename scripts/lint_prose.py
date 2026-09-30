"""Check source files, docs and the README against the prose rules in CLAUDE.md.

It flags banned words and phrases, em and en dashes, emoji, and TODO or FIXME
markers. With no arguments it scans every tracked or untracked, non-ignored
text file in the repository. CLAUDE.md and PROGRESS.md are exempt because they
quote the rules themselves.
"""

from __future__ import annotations

import re
import subprocess
import sys
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path

BANNED_TERMS = (
    "delve",
    "seamless",
    "seamlessly",
    "leverage",
    "robust",
    "cutting-edge",
    "state-of-the-art",
    "comprehensive",
    "utilize",
    "harness",
    "empower",
    "elevate",
    "streamline",
    "game-changer",
    "blazing",
    "supercharge",
    "powerful",
    "elegant",
    "effortless",
    "it's worth noting",
    "note that",
    "in conclusion",
    "furthermore",
    "moreover",
    "let's",
    "we'll",
    "feel free",
    "happy coding",
    "as an AI",
)

EXEMPT_FILES = frozenset({"CLAUDE.md", "PROGRESS.md"})
# The linter and its tests must spell out the banned terms to check for them.
EXEMPT_PREFIXES = ("scripts/lint_prose.py", "scripts/tests/")
# Fixtures are copied or trimmed from external datasets and are not our prose.
EXEMPT_PARTS = ("tests/fixtures",)

TEXT_SUFFIXES = frozenset(
    {
        ".c",
        ".cc",
        ".cfg",
        ".cmake",
        ".cpp",
        ".css",
        ".h",
        ".hpp",
        ".ini",
        ".js",
        ".json",
        ".jsx",
        ".md",
        ".mjs",
        ".py",
        ".sh",
        ".sql",
        ".toml",
        ".ts",
        ".tsx",
        ".txt",
        ".yaml",
        ".yml",
    }
)
TEXT_NAMES = frozenset({"Makefile", "Dockerfile", "CMakeLists.txt", ".clang-tidy", ".clang-format"})
SKIPPED_NAMES = frozenset({"uv.lock", "pnpm-lock.yaml", "package-lock.json"})

# Letters, digits and underscores on either side mean the term is part of a
# longer identifier such as RobustScaler or elevated, which is allowed.
_WORD_CHAR = r"[A-Za-z0-9_]"


def _term_pattern(term: str) -> re.Pattern[str]:
    body = re.escape(term).replace("'", "['\N{RIGHT SINGLE QUOTATION MARK}]")
    body = body.replace(r"\ ", r"\s+")
    return re.compile(rf"(?<!{_WORD_CHAR}){body}(?!{_WORD_CHAR})", re.IGNORECASE)


TERM_PATTERNS = tuple((term, _term_pattern(term)) for term in BANNED_TERMS)
DASH_PATTERN = re.compile("[\N{EM DASH}\N{EN DASH}]")
MARKER_PATTERN = re.compile(r"(?<![A-Za-z0-9_])(TODO|FIXME)(?![A-Za-z0-9_])")
EMOJI_PATTERN = re.compile(
    "["
    "\U0001f000-\U0001faff"  # pictographs, emoticons, transport, symbols and extended-A
    "☀-➿"  # miscellaneous symbols and dingbats
    "⬀-⯿"  # arrows and stars used as emoji
    "️"  # emoji presentation selector
    "]"
)


@dataclass(frozen=True)
class Finding:
    path: str
    line: int
    column: int
    message: str

    def __str__(self) -> str:
        return f"{self.path}:{self.line}:{self.column}: {self.message}"


def check_text(path: str, text: str) -> list[Finding]:
    """Return every rule violation in one file's text."""
    findings: list[Finding] = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        for term, pattern in TERM_PATTERNS:
            findings.extend(
                Finding(path, line_number, match.start() + 1, f"banned term '{term}'")
                for match in pattern.finditer(line)
            )
        findings.extend(
            Finding(path, line_number, match.start() + 1, "em or en dash")
            for match in DASH_PATTERN.finditer(line)
        )
        findings.extend(
            Finding(path, line_number, match.start() + 1, "emoji")
            for match in EMOJI_PATTERN.finditer(line)
        )
        findings.extend(
            Finding(path, line_number, match.start() + 1, f"{match.group(1)} marker")
            for match in MARKER_PATTERN.finditer(line)
        )
    return findings


def is_checked(relative_path: str) -> bool:
    """Decide whether a repository-relative path is subject to the prose rules."""
    path = Path(relative_path)
    if relative_path in EXEMPT_FILES or path.name in SKIPPED_NAMES:
        return False
    if relative_path.startswith(EXEMPT_PREFIXES):
        return False
    if any(part in relative_path for part in EXEMPT_PARTS):
        return False
    return path.suffix in TEXT_SUFFIXES or path.name in TEXT_NAMES


def repository_files(root: Path) -> list[str]:
    output = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    return sorted({line for line in output.splitlines() if line})


def lint_paths(root: Path, relative_paths: Iterable[str]) -> Iterator[Finding]:
    for relative_path in relative_paths:
        if not is_checked(relative_path):
            continue
        file_path = root / relative_path
        if not file_path.is_file():
            continue
        text = file_path.read_text(encoding="utf-8", errors="replace")
        yield from check_text(relative_path, text)


def main(argv: list[str]) -> int:
    root = Path(__file__).resolve().parent.parent
    if argv:
        paths = [str(Path(arg).resolve().relative_to(root)) for arg in argv]
    else:
        paths = repository_files(root)
    findings = list(lint_paths(root, paths))
    for finding in findings:
        print(finding)
    if findings:
        print(f"lint_prose: {len(findings)} finding(s)", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
