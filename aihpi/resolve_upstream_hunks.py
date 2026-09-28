"""Take upstream's side of merge-conflict hunks that carry none of the fork's own work.

A hunk qualifies when every non-blank line on our side was last written by a commit that
is already in upstream's history, and every merge-base line in the hunk still exists
somewhere in our file (blame cannot see deletions, so a fork removal must never be
undone; a line that merely moved is not a removal). Such hunks are upstream
arguing with an older copy of itself, typically content we merged from a branch that
later landed on main in a different form. Every other hunk is left as a normal conflict
for the next resolver.

Usage: python3 aihpi/resolve_upstream_hunks.py <upstream-ref> <file>...
"""

import re
import subprocess
import sys
from dataclasses import dataclass
from functools import cache
from pathlib import Path

MARKER = re.compile(r"^(<{7}|\|{7}|={7}|>{7})(?: (.*))?$")
SHA = re.compile(r"^[0-9a-f]{40} ")
VIRTUAL_BASE_MARKER = re.compile(r"^(<{8,}|={8,}|>{8,}|\|{8,})( |$)")


@dataclass(frozen=True, slots=True)
class Hunk:
    ours_label: str
    theirs_label: str
    ours: tuple[str, ...]
    base: tuple[str, ...]
    theirs: tuple[str, ...]


def git(*args: str) -> str:
    return subprocess.run(["git", *args], check=True, capture_output=True, text=True).stdout


def parse(text: str) -> list[str | Hunk] | None:
    parts: list[str | Hunk] = []
    lines = iter(text.split("\n"))
    for line in lines:
        m = MARKER.match(line)
        if not m:
            parts.append(line)
            continue
        if m[1] != "<" * 7:
            return None
        sections: dict[str, list[str]] = {"ours": [], "base": [], "theirs": []}
        side, labels = "ours", {"ours": m[2] or ""}
        for inner in lines:
            im = MARKER.match(inner)
            if im and im[1] == "|" * 7:
                side = "base"
            elif im and im[1] == "=" * 7:
                side = "theirs"
            elif im and im[1] == ">" * 7:
                labels["theirs"] = im[2] or ""
                break
            elif im:
                return None
            else:
                sections[side].append(inner)
        else:
            return None
        parts.append(Hunk(labels["ours"], labels["theirs"], *(tuple(sections[k]) for k in ("ours", "base", "theirs"))))
    return parts


def blamed_commits(path: str, start: int, count: int) -> set[str]:
    out = git("blame", "--porcelain", "-L", f"{start},+{count}", "HEAD", "--", path)
    commits, current = set(), ""
    for line in out.split("\n"):
        if SHA.match(line):
            current = line[:40]
        elif line.startswith("\t") and line[1:].strip():
            commits.add(current)
    return commits


def our_path(path: str) -> tuple[str, list[str]]:
    blob = next(e.split()[1] for e in git("ls-files", "-u", "--", path).splitlines() if e.split()[2] == "2")
    same_path = git("ls-tree", "HEAD", "--", path).split()[2:3] == [blob]
    renamed = (e.split("\t", 1)[1] for e in git("ls-tree", "-r", "HEAD").splitlines() if e.split()[2] == blob)
    head_path = path if same_path else next(renamed)
    return head_path, git("cat-file", "blob", blob).split("\n")


def resolve(path: str, upstream: str) -> tuple[int, int]:
    head_path, head_lines = our_path(path)
    git("checkout", "--conflict=diff3", "--", path)
    parts = parse(Path(path).read_text())
    if parts is None:
        return 0, -1

    @cache
    def in_upstream(commit: str) -> bool:
        return subprocess.run(["git", "merge-base", "--is-ancestor", commit, upstream]).returncode == 0

    our_lines = set(head_lines)
    out: list[str] = []
    taken = left = 0
    cursor = 0
    for part in parts:
        if isinstance(part, str):
            out.append(part)
            continue
        start = next(
            (
                i
                for i in range(cursor, len(head_lines) - len(part.ours) + 1)
                if tuple(head_lines[i : i + len(part.ours)]) == part.ours
            ),
            None,
        )
        dropped_by_us = [
            line for line in part.base if line.strip() and not VIRTUAL_BASE_MARKER.match(line) and line not in our_lines
        ]
        ours_is_upstream = start is not None and (
            not part.ours or all(in_upstream(c) for c in blamed_commits(head_path, start + 1, len(part.ours)))
        )
        if start is not None:
            cursor = start + len(part.ours)
        if ours_is_upstream and not dropped_by_us:
            out.extend(part.theirs)
            taken += 1
        else:
            out.extend(
                [f"<<<<<<< {part.ours_label}", *part.ours, "=======", *part.theirs, f">>>>>>> {part.theirs_label}"]
            )
            left += 1
    Path(path).write_text("\n".join(out))
    if left == 0:
        git("add", "--", path)
    return taken, left


def main() -> int:
    upstream, *paths = sys.argv[1:]
    for path in paths:
        try:
            taken, left = resolve(path, upstream)
        except (subprocess.CalledProcessError, StopIteration):
            taken, left = 0, -1
        if left < 0:
            print(f"{path}: not a plain text conflict, leaving it alone")
        else:
            print(f"{path}: {taken} hunk(s) taken from upstream, {left} left")
    return 0


if __name__ == "__main__":
    sys.exit(main())
