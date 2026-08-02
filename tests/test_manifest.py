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


def test_every_entry_has_a_url() -> None:
    for entry in load_manifest():
        assert entry.url, f"{entry.name}: url must not be empty"


def test_lang_is_known() -> None:
    for entry in load_manifest():
        assert entry.lang in ("py", "ts")


def test_every_entry_has_a_valid_stage() -> None:
    for entry in load_manifest():
        assert entry.stage in (1, 2, 3), f"{entry.name}: stage {entry.stage!r} is not 1/2/3"


def test_stage_1_repos() -> None:
    stage_1_names = {e.name for e in load_manifest() if e.stage == 1}
    assert stage_1_names == {"flask", "requests", "rich", "meridian2"}


def test_meridian2_is_cloned_from_github() -> None:
    entries = {e.name: e for e in load_manifest()}
    assert entries["meridian2"].url == "https://github.com/realJohnDoe/meridian.git"
