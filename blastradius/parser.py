"""Parse a Python repo into graph elements using stdlib ``ast``.

Node keys are unique strings:
- File:     ``file:<relpath>``
- Function: ``func:<relpath>:<QualName>`` (methods: ``ClassName.method``)
- Class:    ``class:<relpath>:<QualName>``

Edges:
- File DEFINES Function / Class
- File IMPORTS File (local imports only)
- Function CALLS Function

CALLS resolution is name-based: a call ``foo()`` links to every defined
function named ``foo``; ``obj.method()`` links to every defined method
named ``method``. This can over-link when two functions share a name in
different files. That is a deliberate trade-off for a weekend build: the
graph is a *may-call* over-approximation, so the blast radius never
misses a real caller, at the cost of occasionally including a false one.
The README documents this.
"""

from __future__ import annotations

import ast
import os
from dataclasses import dataclass, field


@dataclass
class Node:
    key: str
    label: str
    name: str  # display name: relpath for files, qualname for funcs/classes
    file: str  # relpath of the defining file
    lineno: int = 0
    code: str = ""  # source text (functions only)


@dataclass
class Edge:
    src: str  # node key
    dst: str  # node key
    type: str  # DEFINES | IMPORTS | CALLS


@dataclass
class RepoGraph:
    nodes: dict = field(default_factory=dict)  # key -> Node
    edges: list = field(default_factory=list)  # Edge

    def functions_named(self, simple_name: str) -> list:
        """All Function nodes whose simple (unqualified) name matches."""
        return [
            n
            for n in self.nodes.values()
            if n.label == "Function" and n.name.split(".")[-1] == simple_name
        ]


def is_test_file(relpath: str) -> bool:
    base = os.path.basename(relpath)
    return base.startswith("test_") or base.endswith("_test.py")


class _FunctionVisitor(ast.NodeVisitor):
    """Collect defs, calls and imports inside one module."""

    def __init__(self, relpath: str, source: str):
        self.relpath = relpath
        self.source = source
        self.lines = source.splitlines()
        self.classes: list[tuple[str, int, int]] = []  # (qualname, lineno, end)
        self.functions: list[dict] = []  # {qualname, lineno, end, calls}
        self.imports: list[str] = []  # imported module dotted names
        self._scope: list[str] = []  # class stack for qualnames
        self._current_calls: list[str] | None = None

    def _src_segment(self, node: ast.AST) -> str:
        try:
            seg = ast.get_source_segment(self.source, node)
        except Exception:
            seg = None
        return seg or ""

    def visit_ClassDef(self, node: ast.ClassDef):
        qualname = ".".join(self._scope + [node.name])
        self.classes.append((qualname, node.lineno, getattr(node, "end_lineno", node.lineno)))
        self._scope.append(node.name)
        self.generic_visit(node)
        self._scope.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef):
        qualname = ".".join(self._scope + [node.name])
        calls: list[str] = []
        prev = self._current_calls
        self._current_calls = calls
        self.generic_visit(node)
        self._current_calls = prev
        self.functions.append(
            {
                "qualname": qualname,
                "lineno": node.lineno,
                "end": getattr(node, "end_lineno", node.lineno),
                "code": self._src_segment(node),
                "calls": calls,
            }
        )

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_Call(self, node: ast.Call):
        if self._current_calls is not None:
            name = self._call_name(node.func)
            if name:
                self._current_calls.append(name)
        self.generic_visit(node)

    @staticmethod
    def _call_name(func: ast.AST) -> str | None:
        if isinstance(func, ast.Name):
            return func.id
        if isinstance(func, ast.Attribute):
            return func.attr
        return None

    def visit_Import(self, node: ast.Import):
        for alias in node.names:
            self.imports.append(alias.name)
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom):
        if node.module:
            self.imports.append(node.module)
        self.generic_visit(node)


def parse_repo(root: str) -> RepoGraph:
    """Walk ``root`` and return the parsed code graph."""
    graph = RepoGraph()
    py_files: list[str] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in {".git", "__pycache__", ".venv", "venv", "node_modules"}]
        for fn in filenames:
            if fn.endswith(".py"):
                py_files.append(os.path.join(dirpath, fn))

    # module dotted name -> relpath, for import resolution
    module_to_file: dict[str, str] = {}
    for path in py_files:
        rel = os.path.relpath(path, root)
        mod = rel[: -len(".py")].replace(os.sep, ".")
        if mod.endswith(".__init__"):
            mod = mod[: -len(".__init__")]
        module_to_file[mod] = rel

    per_file: dict[str, _FunctionVisitor] = {}
    for path in py_files:
        rel = os.path.relpath(path, root)
        with open(path, encoding="utf-8", errors="replace") as fh:
            source = fh.read()
        try:
            tree = ast.parse(source, filename=rel)
        except SyntaxError:
            continue
        visitor = _FunctionVisitor(rel, source)
        visitor.visit(tree)
        per_file[rel] = visitor
        graph.nodes[f"file:{rel}"] = Node(
            key=f"file:{rel}", label="File", name=rel, file=rel
        )

    # DEFINES edges + Function/Class nodes
    for rel, visitor in per_file.items():
        file_key = f"file:{rel}"
        for qualname, lineno, _end in visitor.classes:
            key = f"class:{rel}:{qualname}"
            graph.nodes[key] = Node(key=key, label="Class", name=qualname, file=rel, lineno=lineno)
            graph.edges.append(Edge(src=file_key, dst=key, type="DEFINES"))
        for fn in visitor.functions:
            key = f"func:{rel}:{fn['qualname']}"
            graph.nodes[key] = Node(
                key=key,
                label="Function",
                name=fn["qualname"],
                file=rel,
                lineno=fn["lineno"],
                code=fn["code"],
            )
            graph.edges.append(Edge(src=file_key, dst=key, type="DEFINES"))

    # IMPORTS edges (local modules only)
    for rel, visitor in per_file.items():
        file_key = f"file:{rel}"
        seen: set[str] = set()
        for mod in visitor.imports:
            target = module_to_file.get(mod)
            if target and target != rel and target not in seen:
                seen.add(target)
                graph.edges.append(Edge(src=file_key, dst=f"file:{target}", type="IMPORTS"))

    # CALLS edges (name-based resolution, see module docstring)
    for rel, visitor in per_file.items():
        for fn in visitor.functions:
            src_key = f"func:{rel}:{fn['qualname']}"
            seen: set[str] = set()
            for callee in fn["calls"]:
                for target in graph.functions_named(callee):
                    if target.key != src_key and target.key not in seen:
                        seen.add(target.key)
                        graph.edges.append(Edge(src=src_key, dst=target.key, type="CALLS"))

    return graph
