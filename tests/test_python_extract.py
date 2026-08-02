"""Unit tests for the Python extractor's path/namespace logic.

Hermetic: builds tiny package trees under tmp_path rather than touching
corpus/checkouts, so these run in CI without network access. The grimp
integration itself is covered by running the extractor against the real
corpus (see the module docstring in extractors/python/extract.py).
"""

from pathlib import Path

import pytest

from extractors.python.extract import _module_to_relpath, _namespace_dirs, _prepared_root


def make_package(root: Path) -> Path:
    """A package with a regular subpackage and a PEP 420 namespace subpackage."""
    pkg = root / "pkg"
    (pkg / "regular").mkdir(parents=True)
    (pkg / "namespace").mkdir(parents=True)
    (pkg / "__init__.py").write_text("")
    (pkg / "core.py").write_text("")
    (pkg / "regular" / "__init__.py").write_text("")
    (pkg / "regular" / "thing.py").write_text("")
    # No __init__.py here -- this is the case grimp skips.
    (pkg / "namespace" / "orphan.py").write_text("")
    return root


def test_module_to_relpath_prefers_module_file(tmp_path: Path) -> None:
    make_package(tmp_path)
    assert _module_to_relpath("pkg.core", tmp_path) == "pkg/core.py"


def test_module_to_relpath_falls_back_to_init(tmp_path: Path) -> None:
    make_package(tmp_path)
    assert _module_to_relpath("pkg", tmp_path) == "pkg/__init__.py"
    assert _module_to_relpath("pkg.regular", tmp_path) == "pkg/regular/__init__.py"


def test_module_to_relpath_raises_for_unknown_module(tmp_path: Path) -> None:
    make_package(tmp_path)
    with pytest.raises(FileNotFoundError, match="no file for module"):
        _module_to_relpath("pkg.nope", tmp_path)


def test_namespace_dirs_finds_only_init_less_dirs_with_python(tmp_path: Path) -> None:
    make_package(tmp_path)
    assert _namespace_dirs(tmp_path, ["pkg"]) == [Path("pkg/namespace")]


def test_namespace_dirs_ignores_dirs_without_python_files(tmp_path: Path) -> None:
    make_package(tmp_path)
    (tmp_path / "pkg" / "assets").mkdir()
    (tmp_path / "pkg" / "assets" / "logo.svg").write_text("")
    assert _namespace_dirs(tmp_path, ["pkg"]) == [Path("pkg/namespace")]


def test_prepared_root_injects_shims_without_touching_source(tmp_path: Path) -> None:
    make_package(tmp_path)
    original = tmp_path / "pkg" / "namespace" / "__init__.py"

    with _prepared_root(tmp_path, ["pkg"]) as (staged, shims):
        assert staged != tmp_path, "must work on a copy, not the checkout"
        assert shims == {"pkg/namespace/__init__.py"}
        assert (staged / "pkg" / "namespace" / "__init__.py").exists()
        assert (staged / "pkg" / "core.py").exists(), "copy must be complete"
        assert not original.exists(), "source checkout must stay untouched"

    assert not original.exists()
    assert not staged.exists(), "temp tree must be cleaned up"


def test_prepared_root_is_a_noop_without_namespace_dirs(tmp_path: Path) -> None:
    make_package(tmp_path)
    (tmp_path / "pkg" / "namespace" / "__init__.py").write_text("")

    with _prepared_root(tmp_path, ["pkg"]) as (staged, shims):
        assert staged == tmp_path, "no copy needed when nothing to shim"
        assert shims == set()
