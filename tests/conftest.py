from collections.abc import Callable
from pathlib import Path

import pytest

from extractors.schema import Graph, load

GRAPHS = Path(__file__).resolve().parent.parent / "corpus" / "graphs"


@pytest.fixture(scope="session")
def corpus_graph() -> Callable[[str], Graph]:
    """Load a checked-in extracted graph by repo name.

    Session-scoped and cached: these are the real recorded corpus, so tests that
    use them are pinning behaviour against actual repos rather than fixtures.
    """
    cache: dict[str, Graph] = {}

    def get(repo: str) -> Graph:
        if repo not in cache:
            cache[repo] = load(GRAPHS / f"{repo}.json.gz")
        return cache[repo]

    return get
