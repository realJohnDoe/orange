"""Extract a normalized import graph from the Python corpus repos.

Uses grimp (the engine under import-linter) rather than a hand-rolled ast
pass, per plan.md's "do not write parsers". grimp already handles the parts
that are easy to get subtly wrong -- relative-import level arithmetic, the
submodule-vs-attribute ambiguity in `from pkg.sub import name`, and
TYPE_CHECKING blocks including aliased forms like `t.TYPE_CHECKING`.

grimp is invoked in a subprocess (see grimp_worker.py for why that isolation
is mandatory, not stylistic).

Node granularity: grimp works in dotted module names, which map 1:1 onto
files -- `flask.app` -> src/flask/app.py, `flask.json` -> src/flask/json/
__init__.py. A package's __init__.py is therefore its own node, which is
exactly the "face" the cost model expects.

Namespace-package workaround: grimp deliberately skips "orphan" directories
-- a subdirectory with no __init__.py nested inside a regular package (see
grimp/adaptors/modulefinder.py). Such a directory is nonetheless a real PEP
420 namespace subpackage and importable at runtime: flask/sansio/ is one,
and dropping it cost flask 3 of its 24 modules and ~28 of its ~95 edges,
biasing the repo flat in exactly the dimension the cost function measures.
So we copy the source root to a temp tree, drop an empty __init__.py into
each such directory, and run grimp there. The injected files become nodes
that don't exist in reality, so they are removed afterwards -- verified
lossless, since nothing imports the namespace package itself, only modules
within it (asserted below rather than assumed).
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path, PurePosixPath
from typing import Annotated

import typer

from corpus.sync import RepoEntry, load_manifest
from extractors.classify import is_barrel_py
from extractors.schema import Graph, Node, Stats, write

GRAPHS_DIR = Path(__file__).resolve().parents[2] / "corpus" / "graphs"
WORKER = Path(__file__).resolve().parent / "grimp_worker.py"


def _run_worker(sys_path_root: Path, packages: list[str]) -> dict:
    result = subprocess.run(
        [sys.executable, str(WORKER), str(sys_path_root), *packages],
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(result.stdout)


def _namespace_dirs(sys_path_root: Path, packages: list[str]) -> list[Path]:
    """Directories holding .py files but no __init__.py, inside a real package."""
    found = []
    for package in packages:
        for directory in sorted((sys_path_root / package).rglob("*")):
            if not directory.is_dir() or (directory / "__init__.py").exists():
                continue
            if any(directory.glob("*.py")):
                found.append(directory.relative_to(sys_path_root))
    return found


@contextmanager
def _prepared_root(sys_path_root: Path, packages: list[str]) -> Iterator[tuple[Path, set[str]]]:
    """Yield a root grimp can fully traverse, plus the paths of any injected shims.

    Copies to a temp tree rather than touching the checkout, so an interrupted
    run can't leave a phantom __init__.py behind to silently pollute later runs.
    """
    namespaces = _namespace_dirs(sys_path_root, packages)
    if not namespaces:
        yield sys_path_root, set()
        return

    with tempfile.TemporaryDirectory(prefix="dsl-nspkg-") as tmp:
        staged = Path(tmp) / "root"
        shutil.copytree(sys_path_root, staged)
        shims = set()
        for namespace in namespaces:
            (staged / namespace / "__init__.py").touch()
            shims.add((namespace / "__init__.py").as_posix())
        yield staged, shims


def _module_to_relpath(module: str, sys_path_root: Path) -> str:
    """Map a dotted module name to a path relative to its sys.path root."""
    base = sys_path_root / Path(*module.split("."))
    for candidate in (base.with_suffix(".py"), base / "__init__.py"):
        if candidate.is_file():
            return candidate.relative_to(sys_path_root).as_posix()
    raise FileNotFoundError(f"no file for module {module!r} under {sys_path_root}")


def extract(entry: RepoEntry) -> Graph:
    checkout = entry.checkout_path
    if not checkout.is_dir():
        raise FileNotFoundError(f"{entry.name}: not synced -- run corpus/sync.py first")

    # A root like "src/flask" means package `flask` importable from "src".
    # Group by that parent so packages sharing one sys.path entry are analyzed
    # together and grimp can see edges between them.
    by_sys_path: dict[Path, list[str]] = {}
    for root in entry.roots:
        root_path = PurePosixPath(root)
        by_sys_path.setdefault(checkout / root_path.parent, []).append(root_path.name)

    nodes: list[Node] = []
    external_dropped = 0
    for sys_path_root, packages in by_sys_path.items():
        prefix = sys_path_root.relative_to(checkout).as_posix()
        with _prepared_root(sys_path_root, sorted(packages)) as (staged, shims):
            payload = _run_worker(staged, sorted(packages))
            relpaths = {
                module: _module_to_relpath(module, staged) for module in payload["modules"]
            }
        external_dropped += payload["external_imports_dropped"]

        for module, edges in payload["modules"].items():
            if relpaths[module] in shims:
                continue  # injected namespace shim -- not a real file
            targets = tuple(sorted(relpaths[t] for t in edges["imports"]))
            if shims & set(targets):
                raise AssertionError(
                    f"{entry.name}: {module} imports an injected namespace shim; "
                    "dropping shims would lose real edges"
                )
            path = f"{prefix}/{relpaths[module]}" if prefix != "." else relpaths[module]
            nodes.append(
                Node(
                    id=path,
                    kind="file",
                    is_barrel=is_barrel_py((checkout / path).read_text(encoding="utf-8")),
                    imports=tuple(
                        f"{prefix}/{t}" if prefix != "." else t for t in targets
                    ),
                    type_only=tuple(
                        f"{prefix}/{t}" if prefix != "." else t
                        for t in sorted(relpaths[t] for t in edges["type_only"])
                    ),
                )
            )

    return Graph(
        repo=entry.name,
        lang="py",
        commit=entry.commit,
        extractor="grimp",
        roots=entry.roots,
        nodes=tuple(sorted(nodes, key=lambda n: n.id)),
        # unresolved_imports stays 0: grimp resolves every internal import or
        # classifies it external, so Python has no unresolved category.
        stats=Stats(external_imports_dropped=external_dropped),
    )


def main(
    repo: Annotated[
        str | None, typer.Option(help="only extract this repo (default: all Python repos)")
    ] = None,
) -> None:
    entries = [e for e in load_manifest() if e.lang == "py"]
    if repo is not None:
        entries = [e for e in entries if e.name == repo]
        if not entries:
            raise typer.BadParameter(f"no Python repo named {repo!r} in the manifest")

    for entry in entries:
        graph = extract(entry)
        out = GRAPHS_DIR / f"{entry.name}.json.gz"
        write(graph, out)
        edges = sum(len(n.imports) for n in graph.nodes)
        type_only = sum(len(n.type_only) for n in graph.nodes)
        barrels = sum(n.is_barrel for n in graph.nodes)
        print(
            f"{entry.name:10} {len(graph.nodes):4} nodes {edges:5} edges "
            f"({type_only} type-only, {barrels} barrels) -> {out.name}"
        )


if __name__ == "__main__":
    typer.run(main)
