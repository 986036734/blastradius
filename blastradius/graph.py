"""Graph backends.

Production backend is :class:`FalkorGraph` — every impact question is
answered by a real Cypher query / graph algorithm in FalkorDB. Nothing
about the blast radius is computed in Python.

:class:`MemoryGraph` is a TEST-ONLY backend implementing the same
high-level methods with plain-Python BFS over the parsed edges. It exists
so unit tests can run without a database. The demo and the CLI always
use FalkorDB.
"""

from __future__ import annotations

from collections import deque

from .parser import RepoGraph, is_test_file

GRAPH_NAME = "blastradius"


def _rows(res) -> list[dict]:
    """Convert a FalkorDB result to a list of dicts.

    The client's ``header`` is ``[[type, name], ...]`` — the column name
    is the second element, not the whole pair.
    """
    columns = [h[1] if isinstance(h, (list, tuple)) else h for h in res.header]
    return [dict(zip(columns, row)) for row in res.result_set]


class FalkorGraph:
    """FalkorDB-backed code graph."""

    def __init__(self, host: str = "localhost", port: int = 6380, graph: str = GRAPH_NAME):
        from falkordb import FalkorDB

        self.db = FalkorDB(host=host, port=port)
        self.graph = self.db.select_graph(graph)

    def clear(self) -> None:
        self.graph.delete()

    def load(self, repo: RepoGraph) -> tuple[int, int]:
        """MERGE all parsed nodes/edges into FalkorDB. Returns (nodes, edges)."""
        for node in repo.nodes.values():
            self.graph.query(
                "MERGE (n:%s {key: $key}) "
                "SET n.name = $name, n.file = $file, n.lineno = $lineno, n.code = $code" % node.label,
                {"key": node.key, "name": node.name, "file": node.file,
                 "lineno": node.lineno, "code": node.code},
            )
        for edge in repo.edges:
            self.graph.query(
                "MATCH (a {key: $src}), (b {key: $dst}) "
                "MERGE (a)-[:%s]->(b)" % edge.type,
                {"src": edge.src, "dst": edge.dst},
            )
        return len(repo.nodes), len(repo.edges)

    # -- queries ---------------------------------------------------------

    def functions_named(self, simple_name: str) -> list[dict]:
        """All Function nodes with this simple (unqualified) name."""
        res = self.graph.query(
            "MATCH (f:Function) WHERE f.name = $name OR f.name ENDS WITH $suffix "
            "RETURN f.name AS name, f.file AS file, f.lineno AS lineno, f.code AS code",
            {"name": simple_name, "suffix": "." + simple_name},
        )
        return _rows(res)

    def blast_radius(self, func_name: str, depth: int = 5) -> list[dict]:
        """Every function that transitively CALLS ``func_name``.

        This is the impact set: changing ``func_name`` can break these
        callers. Real multi-hop Cypher, not Python traversal.
        """
        res = self.graph.query(
            "MATCH (start:Function {name: $name})<-[:CALLS*1..%d]-(caller:Function) "
            "RETURN DISTINCT caller.name AS name, caller.file AS file, "
            "caller.lineno AS lineno" % depth,
            {"name": func_name},
        )
        return _rows(res)

    def shortest_path(self, caller_name: str, changed_name: str) -> list[str]:
        """Shortest CALLS chain from ``caller_name`` back to ``changed_name``."""
        # FalkorDB only allows shortestPath in WITH / RETURN clauses.
        res = self.graph.query(
            "MATCH (a:Function {name: $caller}), (b:Function {name: $changed}) "
            "WITH a, b, shortestPath((a)-[:CALLS*]->(b)) AS p "
            "RETURN [n IN nodes(p) | n.name] AS path",
            {"caller": caller_name, "changed": changed_name},
        )
        if not res.result_set or res.result_set[0][0] is None:
            return []
        return list(res.result_set[0][0])

    def hot_spots(self, limit: int = 5) -> list[dict]:
        """Functions with the highest (in + out) CALLS degree."""
        # LIMIT is interpolated (not parameterized): FalkorDB versions differ
        # on parameterized LIMIT, and `limit` is an int from our own code.
        res = self.graph.query(
            "MATCH (f:Function)-[r:CALLS]-() "
            "RETURN f.name AS name, f.file AS file, count(r) AS degree "
            "ORDER BY degree DESC LIMIT %d" % int(limit),
        )
        return _rows(res)


class MemoryGraph:
    """TEST-ONLY in-memory backend with the same methods as FalkorGraph.

    Used by unit tests so they run without a database. Not used by the
    CLI or the demo.
    """

    def __init__(self, repo: RepoGraph):
        self.repo = repo
        self.callers: dict[str, set[str]] = {}  # func key -> keys that call it
        self.callees: dict[str, set[str]] = {}  # func key -> keys it calls
        for edge in repo.edges:
            if edge.type == "CALLS":
                self.callers.setdefault(edge.dst, set()).add(edge.src)
                self.callees.setdefault(edge.src, set()).add(edge.dst)

    def _node(self, key: str) -> dict:
        n = self.repo.nodes[key]
        return {"name": n.name, "file": n.file, "lineno": n.lineno, "code": n.code}

    def functions_named(self, simple_name: str) -> list[dict]:
        return [
            self._node(n.key)
            for n in self.repo.functions_named(simple_name)
        ]

    def blast_radius(self, func_name: str, depth: int = 5) -> list[dict]:
        starts = [n.key for n in self.repo.functions_named(func_name)]
        seen: set[str] = set()
        frontier = list(starts)
        for _ in range(depth):
            nxt: list[str] = []
            for key in frontier:
                for caller in self.callers.get(key, set()):
                    if caller not in seen and caller not in starts:
                        seen.add(caller)
                        nxt.append(caller)
            frontier = nxt
        return [self._node(k) for k in sorted(seen)]

    def shortest_path(self, caller_name: str, changed_name: str) -> list[str]:
        # BFS over CALLS edges from caller toward changed.
        start_keys = [n.key for n in self.repo.functions_named(caller_name)]
        target_keys = {n.key for n in self.repo.functions_named(changed_name)}
        prev: dict[str, str | None] = {k: None for k in start_keys}
        queue = deque(start_keys)
        found = None
        while queue:
            cur = queue.popleft()
            if cur in target_keys:
                found = cur
                break
            for nxt in sorted(self.callees.get(cur, set())):
                if nxt not in prev:
                    prev[nxt] = cur
                    queue.append(nxt)
        if found is None:
            return []
        path, cur = [], found
        while cur is not None:
            path.append(self.repo.nodes[cur].name)
            cur = prev[cur]
        return list(reversed(path))

    def hot_spots(self, limit: int = 5) -> list[dict]:
        degree: dict[str, int] = {}
        for edge in self.repo.edges:
            if edge.type == "CALLS":
                degree[edge.src] = degree.get(edge.src, 0) + 1
                degree[edge.dst] = degree.get(edge.dst, 0) + 1
        ranked = sorted(degree.items(), key=lambda kv: (-kv[1], kv[0]))[:limit]
        out = []
        for key, deg in ranked:
            d = self._node(key)
            d["degree"] = deg
            out.append(d)
        return out


def impact_report(graph, func_name: str) -> dict:
    """Shared shaping: callers -> affected files -> affected tests."""
    targets = graph.functions_named(func_name)
    if not targets:
        raise ValueError(f"no function named {func_name!r} in the graph")
    if len(targets) > 1:
        names = ", ".join(f"{t['name']} ({t['file']})" for t in targets)
        raise ValueError(
            f"ambiguous function name {func_name!r}; matches: {names}. "
            "Rename or use a qualified name."
        )
    target = targets[0]
    callers = graph.blast_radius(target["name"])
    files = sorted({target["file"]} | {c["file"] for c in callers})
    tests = sorted({c["file"] for c in callers if is_test_file(c["file"])})
    hot = graph.hot_spots()
    chain = []
    if callers:
        # Prefer a chain starting at a test (the most meaningful one for
        # the demo: test -> ... -> changed function), else first caller.
        start = next((c for c in callers if is_test_file(c["file"])), callers[0])
        chain = graph.shortest_path(start["name"], target["name"])
    return {
        "function": target,
        "callers": callers,
        "files": files,
        "tests": tests,
        "hot_spots": hot,
        "chain": chain,
    }
