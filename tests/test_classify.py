import pytest

from extractors.classify import (
    DEFAULT_GENERATED_EXCLUDES,
    DEFAULT_TEST_EXCLUDES,
    is_barrel_py,
    is_barrel_ts,
    is_face,
    matches_any,
)

MATCHES_TEST_DEFAULTS = [
    pytest.param("src/calendar/agenda.test.ts", True, id="dot-test-ts"),
    pytest.param("src/calendar/agenda.tsx", False, id="not-a-test"),
    pytest.param("src/model/__tests__/foo.ts", True, id="tests-dir"),
    pytest.param("src/model/foo.ts", False, id="sibling-of-tests-dir"),
    pytest.param("a/b/conftest.py", True, id="conftest"),
    pytest.param("a/b/test_foo.py", True, id="test-prefix-py"),
    pytest.param("a/b/foo_test.py", True, id="test-suffix-py"),
    pytest.param("a/b/foo.py", False, id="plain-py"),
]


@pytest.mark.parametrize("path,expected", MATCHES_TEST_DEFAULTS)
def test_matches_any_against_default_test_excludes(path: str, expected: bool) -> None:
    assert matches_any(path, DEFAULT_TEST_EXCLUDES) == expected


def test_matches_any_against_default_generated_excludes() -> None:
    assert matches_any("src/routeTree.gen.ts", DEFAULT_GENERATED_EXCLUDES) is True
    assert matches_any("src/model/schema_pb2.py", DEFAULT_GENERATED_EXCLUDES) is True
    assert matches_any("src/model/schema.py", DEFAULT_GENERATED_EXCLUDES) is False


def test_matches_any_with_repo_specific_patterns() -> None:
    # e.g. a manifest entry's `exclude` merged in by report/run.py.
    assert matches_any("src/fixtures/foo.ts", ("**/fixtures/**",)) is True
    assert matches_any("src/fixtures/foo.ts", DEFAULT_TEST_EXCLUDES) is False


@pytest.mark.parametrize(
    "path,lang,expected",
    [
        ("src/model/__init__.py", "py", True),
        ("src/model/store.py", "py", False),
        ("src/feature/index.ts", "ts", True),
        ("src/feature/index.tsx", "ts", True),
        ("src/feature/store.ts", "ts", False),
    ],
)
def test_is_face(path: str, lang: str, expected: bool) -> None:
    assert is_face(path, lang) == expected


def test_is_barrel_py_pure_reexport() -> None:
    source = "from .foo import Foo\nfrom .bar import Bar\n\n__all__ = ['Foo', 'Bar']\n"
    assert is_barrel_py(source) is True


def test_is_barrel_py_with_logic_is_not_a_barrel() -> None:
    source = "from .foo import Foo\n\ndef helper():\n    return Foo()\n"
    assert is_barrel_py(source) is False


def test_is_barrel_py_empty_module_is_not_a_barrel() -> None:
    assert is_barrel_py("") is False


def test_is_barrel_py_syntax_error_is_not_a_barrel() -> None:
    assert is_barrel_py("def (:\n") is False


def test_is_barrel_ts_pure_reexport() -> None:
    source = "export * from './foo';\nexport { Bar } from './bar';\n"
    assert is_barrel_ts(source) is True


def test_is_barrel_ts_ignores_comments() -> None:
    source = "// re-exports\nexport * from './foo';\n/* block */\nexport { Bar } from './bar';\n"
    assert is_barrel_ts(source) is True


def test_is_barrel_ts_with_logic_is_not_a_barrel() -> None:
    source = "export * from './foo';\n\nexport function helper() { return 1; }\n"
    assert is_barrel_ts(source) is False


def test_is_barrel_ts_empty_file_is_not_a_barrel() -> None:
    assert is_barrel_ts("") is False


def test_is_barrel_ts_export_type_reexport() -> None:
    assert is_barrel_ts("export type { Foo } from './foo';\n") is True
