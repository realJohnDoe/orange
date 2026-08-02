"""Normalized dependency-graph schema.

This is the contract both extractors (the ast-based Python one and the
dependency-cruiser-based TypeScript one) must emit, and the only shape
model/ and report/ ever read. See "Core design decision" and "Normalized
graph schema" in the Phase 0 plan.

Graph and Node validate their own invariants at construction time, so a
Graph that exists is already structurally sound: no duplicate node ids, no
edge pointing at a node that doesn't exist, type_only is always a subset of
imports, ids are POSIX-relative. Downstream code does not need to re-check
any of this.

There's no is_face field: it's a pure function of (id, lang) with no
extraction-time work behind it -- see extractors.classify.is_face(node.id,
graph.lang) rather than storing a value that's always fully recoverable
from data already here. is_barrel and type_only stay stored fields because
they encode facts about a file's *content* (is it purely re-exports? which
imports are type-only?) that only the extractor's parse of the source can
answer, and the source isn't available once a graph is cached as JSON.
"""

from __future__ import annotations

import gzip
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

Lang = Literal["py", "ts"]

_COMMIT_RE = re.compile(r"^[0-9a-f]{7,40}$")


@dataclass(frozen=True, slots=True)
class Node:
    id: str
    kind: Literal["file"]
    is_barrel: bool
    imports: tuple[str, ...] = ()
    type_only: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.id.startswith("/") or "\\" in self.id:
            raise ValueError(f"node id {self.id!r} must be a repo-root-relative POSIX path")
        if not set(self.type_only) <= set(self.imports):
            raise ValueError(f"{self.id}: type_only must be a subset of imports")


@dataclass(frozen=True, slots=True)
class Stats:
    unresolved_imports: int = 0
    external_imports_dropped: int = 0
    ambiguous: int = 0


@dataclass(frozen=True, slots=True)
class Graph:
    repo: str
    lang: Lang
    commit: str
    extractor: str
    roots: tuple[str, ...]
    generated_at: str
    nodes: tuple[Node, ...]
    stats: Stats = field(default_factory=Stats)

    def __post_init__(self) -> None:
        if not _COMMIT_RE.match(self.commit):
            raise ValueError(f"{self.repo}: {self.commit!r} is not a git commit sha")
        ids = [n.id for n in self.nodes]
        if len(set(ids)) != len(ids):
            dupes = sorted({i for i in ids if ids.count(i) > 1})
            raise ValueError(f"{self.repo}: duplicate node ids: {dupes}")
        id_set = set(ids)
        for node in self.nodes:
            for target in node.imports:
                if target not in id_set:
                    raise ValueError(f"{self.repo}: {node.id} imports unknown node {target!r}")


def to_dict(graph: Graph) -> dict[str, Any]:
    return {
        "repo": graph.repo,
        "lang": graph.lang,
        "commit": graph.commit,
        "extractor": graph.extractor,
        "roots": list(graph.roots),
        "generated_at": graph.generated_at,
        "stats": {
            "unresolved_imports": graph.stats.unresolved_imports,
            "external_imports_dropped": graph.stats.external_imports_dropped,
            "ambiguous": graph.stats.ambiguous,
        },
        "nodes": [
            {
                "id": n.id,
                "kind": n.kind,
                "is_barrel": n.is_barrel,
                "imports": list(n.imports),
                "type_only": list(n.type_only),
            }
            for n in graph.nodes
        ],
    }


def from_dict(data: dict[str, Any]) -> Graph:
    stats_data = data.get("stats", {})
    return Graph(
        repo=data["repo"],
        lang=data["lang"],
        commit=data["commit"],
        extractor=data["extractor"],
        roots=tuple(data["roots"]),
        generated_at=data["generated_at"],
        stats=Stats(
            unresolved_imports=stats_data.get("unresolved_imports", 0),
            external_imports_dropped=stats_data.get("external_imports_dropped", 0),
            ambiguous=stats_data.get("ambiguous", 0),
        ),
        nodes=tuple(
            Node(
                id=n["id"],
                kind=n["kind"],
                is_barrel=n["is_barrel"],
                imports=tuple(n.get("imports", ())),
                type_only=tuple(n.get("type_only", ())),
            )
            for n in data["nodes"]
        ),
    )


def write(graph: Graph, path: Path) -> None:
    """Write gzip-compressed JSON to path (typically corpus/graphs/<repo>.json.gz)."""
    payload = json.dumps(to_dict(graph), indent=2, sort_keys=True).encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wb") as f:
        f.write(payload)


def load(path: Path) -> Graph:
    with gzip.open(path, "rt", encoding="utf-8") as f:
        return from_dict(json.load(f))
