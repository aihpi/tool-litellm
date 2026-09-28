import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).with_name("resolve_upstream_hunks.py")
TAIL = "".join(f"unchanged line {n}\n" for n in range(30))


def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True).stdout


def commit(repo: Path, path: str, text: str, message: str) -> None:
    (repo / path).write_text(text + TAIL)
    git(repo, "add", path)
    git(repo, "commit", "-qm", message)


def criss_cross(repo: Path, rename: bool) -> None:
    """Both sides merged the same two upstream commits but ordered the lines differently.

    ours ends up with X then Y, upstream with Y then X, and the two merge bases make git
    build a virtual base containing nested conflict markers, like the real nightly merge.
    """
    git(repo, "init", "-q", "-b", "main")
    git(repo, "config", "user.name", "t")
    git(repo, "config", "user.email", "t@example.com")
    git(repo, "config", "merge.conflictStyle", "merge")
    commit(repo, "f.txt", "a\nc\ne\n", "c0")
    git(repo, "checkout", "-qb", "a1")
    commit(repo, "f.txt", "a\nc\nX\ne\n", "a1")
    git(repo, "checkout", "-qb", "b1", "main")
    commit(repo, "f.txt", "a\nc\nY\ne\n", "b1")

    git(repo, "checkout", "-qb", "upstream", "b1")
    subprocess.run(["git", "-C", str(repo), "merge", "-q", "a1"], capture_output=True)
    commit(repo, "f.txt", "a\nc\nY\nX\ne\n", "upstream merges a1 as Y, X")
    if rename:
        git(repo, "mv", "f.txt", "g.txt")
        git(repo, "commit", "-qm", "upstream renames the file")

    git(repo, "checkout", "-qb", "ours", "a1")
    subprocess.run(["git", "-C", str(repo), "merge", "-q", "b1"], capture_output=True)
    commit(repo, "f.txt", "a\nc\nX\nY\ne\n", "we merged b1 as X, Y")


def merge_and_resolve(repo: Path, path: str) -> str:
    subprocess.run(["git", "-C", str(repo), "merge", "-q", "upstream"], capture_output=True)
    assert git(repo, "diff", "--name-only", "--diff-filter=U").split() == [path]
    subprocess.run([sys.executable, str(SCRIPT), "upstream", path], cwd=repo, check=True, capture_output=True)
    return (repo / path).read_text()


def still_conflicted(repo: Path) -> list[str]:
    return git(repo, "diff", "--name-only", "--diff-filter=U").split()


@pytest.mark.parametrize("rename", [False, True])
def test_takes_upstream_when_our_side_is_only_upstream_lines(tmp_path: Path, rename: bool) -> None:
    criss_cross(tmp_path, rename)
    path = "g.txt" if rename else "f.txt"

    assert merge_and_resolve(tmp_path, path) == "a\nc\nY\nX\ne\n" + TAIL
    assert still_conflicted(tmp_path) == []


def test_keeps_the_conflict_when_our_side_has_a_fork_line(tmp_path: Path) -> None:
    criss_cross(tmp_path, rename=False)
    commit(tmp_path, "f.txt", "a\nc\nX\nY\nZ\ne\n", "fork adds Z")

    resolved = merge_and_resolve(tmp_path, "f.txt")

    assert "<<<<<<< " in resolved and "Z" in resolved
    assert still_conflicted(tmp_path) == ["f.txt"]


def test_keeps_the_conflict_when_upstream_would_undo_a_fork_deletion(tmp_path: Path) -> None:
    criss_cross(tmp_path, rename=False)
    commit(tmp_path, "f.txt", "a\nX\nY\ne\n", "fork deletes c")

    resolved = merge_and_resolve(tmp_path, "f.txt")

    assert "<<<<<<< " in resolved
    assert still_conflicted(tmp_path) == ["f.txt"]


def test_leaves_a_modify_delete_conflict_alone(tmp_path: Path) -> None:
    criss_cross(tmp_path, rename=False)
    git(tmp_path, "rm", "-q", "f.txt")
    git(tmp_path, "commit", "-qm", "fork deletes the file")
    subprocess.run(["git", "-C", str(tmp_path), "merge", "-q", "upstream"], capture_output=True)

    result = subprocess.run([sys.executable, str(SCRIPT), "upstream", "f.txt"], cwd=tmp_path, capture_output=True)

    assert result.returncode == 0
    assert still_conflicted(tmp_path) == ["f.txt"]
