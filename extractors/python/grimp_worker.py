"""Run grimp against one corpus checkout and emit its import graph as JSON.

Deliberately a standalone script with NO imports from this project -- it must
never import typer, rich, or anything that pulls them in. Reason: grimp locates
a package via importlib, and importlib.util.find_spec() returns the *cached*
spec from sys.modules when the name is already imported, ignoring sys.path
entirely. Our CLI depends on typer, which imports `rich` -- and `rich` is
itself a corpus repo. Extracting it in-process would silently analyze the
installed rich from site-packages instead of the pinned checkout, producing a
plausible-looking-but-wrong graph. Verified: with rich pre-imported, grimp
returns site-packages' 100 modules instead of a 2-module package sitting at
sys.path[0]; in a clean interpreter it correctly honors sys.path.

So extract.py invokes this in a subprocess, and this file stays import-clean.

Usage:  python grimp_worker.py <sys_path_root> <package> [<package> ...]
Output: JSON on stdout, see WorkerResult in extract.py for the shape.
"""

from __future__ import annotations

import json
import sys


def main(argv: list[str]) -> int:
    sys_path_root, package_names = argv[1], argv[2:]
    # Prepend so the checkout wins over any same-named installed distribution.
    sys.path.insert(0, sys_path_root)

    import grimp

    # cache_dir=None disables grimp's on-disk cache: it keys on package name and
    # would happily serve a previous repo's graph for a same-named package.
    head, *rest = package_names
    all_imports = grimp.build_graph(head, *rest, cache_dir=None)
    value_imports = grimp.build_graph(
        head, *rest, exclude_type_checking_imports=True, cache_dir=None
    )
    # Same build, but with third-party/stdlib targets retained, purely so we can
    # report how many edges were dropped as external.
    with_external = grimp.build_graph(
        head, *rest, include_external_packages=True, cache_dir=None
    )

    internal = set(all_imports.modules)
    modules: dict[str, dict[str, list[str]]] = {}
    external_dropped = 0

    for module in sorted(internal):
        imports = all_imports.find_modules_directly_imported_by(module)
        value_only = value_imports.find_modules_directly_imported_by(module)
        modules[module] = {
            "imports": sorted(imports),
            # grimp exposes type-checking awareness only as a whole-graph flag,
            # so a type-only edge is one present in the full graph but absent
            # from the value-only graph.
            "type_only": sorted(imports - value_only),
        }
        external_dropped += len(
            with_external.find_modules_directly_imported_by(module) - internal
        )

    json.dump(
        {"modules": modules, "external_imports_dropped": external_dropped},
        sys.stdout,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
