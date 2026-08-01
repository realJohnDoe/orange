"""Clone or fetch each enabled corpus repo to its pinned commit.

Idempotent: a repo already checked out at its pinned commit is left alone.
Repos with a `local_path` (meridian2) are never cloned or written to --
sync only verifies the pinned commit is checked out there and warns (does
not fail) on a mismatch, since overwriting a live development checkout
would be destructive.

Each clone is a single-commit shallow fetch (`git init` + `git fetch
--depth 1 origin <sha>` + `git checkout FETCH_HEAD`), not a full clone --
we only ever need the tree at the pinned commit.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path

from extractors.classify import RepoOverrides

MANIFEST_PATH = Path(__file__).parent / "manifest.toml"
CHECKOUTS_DIR = Path(__file__).parent / "checkouts"


@dataclass(frozen=True, slots=True)
class RepoEntry:
    name: str
    lang: str
    commit: str
    roots: tuple[str, ...]
    enabled: bool = True
    stage: int | None = None
    url: str | None = None
    local_path: str | None = None
    note: str = ""
    overrides: RepoOverrides = RepoOverrides()

    def __post_init__(self) -> None:
        if bool(self.url) == bool(self.local_path):
            raise ValueError(f"{self.name}: exactly one of url/local_path must be set")

    @property
    def checkout_path(self) -> Path:
        return Path(self.local_path) if self.local_path else CHECKOUTS_DIR / self.name


def load_manifest(path: Path = MANIFEST_PATH) -> list[RepoEntry]:
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    entries = [
        RepoEntry(
            name=r["name"],
            lang=r["lang"],
            commit=r["commit"],
            roots=tuple(r["roots"]),
            enabled=r.get("enabled", True),
            stage=r.get("stage"),
            url=r.get("url"),
            local_path=r.get("local_path"),
            note=r.get("note", ""),
            overrides=RepoOverrides(
                test_globs=tuple(r.get("test_globs", ())),
                generated_globs=tuple(r.get("generated_globs", ())),
            ),
        )
        for r in data["repo"]
    ]
    names = [e.name for e in entries]
    if len(set(names)) != len(names):
        dupes = sorted({n for n in names if names.count(n) > 1})
        raise ValueError(f"manifest.toml: duplicate repo names: {dupes}")
    return entries


def sync(entry: RepoEntry) -> None:
    if entry.local_path is not None:
        _verify_local(entry)
    else:
        _fetch_pinned_commit(entry)


def _verify_local(entry: RepoEntry) -> None:
    path = entry.checkout_path
    if not path.is_dir():
        raise FileNotFoundError(f"{entry.name}: local_path {path} does not exist")
    head = _git(path, "rev-parse", "HEAD").strip()
    if head != entry.commit:
        print(
            f"warning: {entry.name} is checked out at {head[:12]}, manifest pins "
            f"{entry.commit[:12]} -- results will reflect the working checkout, not the pin",
            file=sys.stderr,
        )


def _fetch_pinned_commit(entry: RepoEntry) -> None:
    assert entry.url is not None, f"{entry.name}: _fetch_pinned_commit requires a url"
    path = entry.checkout_path
    if (path / ".git").exists():
        head = _git(path, "rev-parse", "HEAD").strip()
        if head == entry.commit:
            return
    else:
        path.mkdir(parents=True, exist_ok=True)
        _git(path, "init", "-q")
        _git(path, "remote", "add", "origin", entry.url)
    _git(path, "fetch", "--depth", "1", "origin", entry.commit)
    _git(path, "checkout", "-q", "--detach", "FETCH_HEAD")


def _git(cwd: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=True
    )
    return result.stdout


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--stage", type=int, default=None, help="only sync repos in this stage"
    )
    args = parser.parse_args()

    entries = [e for e in load_manifest() if e.enabled]
    if args.stage is not None:
        entries = [e for e in entries if e.stage == args.stage]

    for entry in entries:
        print(f"syncing {entry.name} @ {entry.commit[:12]}...")
        sync(entry)


if __name__ == "__main__":
    main()
