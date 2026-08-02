"""Structural checks on the checked-in corpus/manifest.toml -- no network access."""

import re

from corpus.sync import load_manifest

_SHA_RE = re.compile(r"^[0-9a-f]{40}$")


def test_manifest_loads() -> None:
    entries = load_manifest()
    assert len(entries) > 0


def test_every_entry_pins_a_full_commit_sha() -> None:
    for entry in load_manifest():
        assert _SHA_RE.match(entry.commit), f"{entry.name}: {entry.commit!r} is not a full sha"


def test_every_entry_has_nonempty_roots() -> None:
    for entry in load_manifest():
        assert entry.roots, f"{entry.name}: roots must not be empty"


def test_every_entry_has_exactly_one_source() -> None:
    # RepoEntry.__post_init__ already enforces this at construction; re-asserting
    # here documents the invariant against the real manifest data.
    for entry in load_manifest():
        assert (entry.url is None) != (entry.local_path is None)


def test_lang_is_known() -> None:
    for entry in load_manifest():
        assert entry.lang in ("py", "ts", "go")


def test_disabled_entries_explain_why() -> None:
    for entry in load_manifest():
        if not entry.enabled:
            assert entry.note, f"{entry.name}: disabled entries must set note"


def test_stage_1_repos_are_enabled() -> None:
    stage_1_names = {e.name for e in load_manifest() if e.enabled and e.stage == 1}
    assert stage_1_names == {"flask", "requests", "rich", "meridian2"}


def test_meridian2_is_cloned_from_github() -> None:
    entries = {e.name: e for e in load_manifest()}
    assert entries["meridian2"].url == "https://github.com/realJohnDoe/meridian.git"
    assert entries["meridian2"].local_path is None
