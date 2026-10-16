"""BlastRadius CLI.

Usage:
    python -m blastradius parse <repo_path>
    python -m blastradius impact <function_name>
    python -m blastradius summarize <function_name>
    python -m blastradius demo
"""

from __future__ import annotations

import argparse
import os
import sys

from .graph import FalkorGraph, impact_report
from .parser import parse_repo
from .summarize import summarize_change

SAMPLE_APP = os.path.join(os.path.dirname(__file__), "sample-app")


def _connect(args) -> FalkorGraph:
    return FalkorGraph(host=args.db_host, port=args.db_port)


def cmd_parse(args) -> int:
    graph = _connect(args)
    repo = parse_repo(args.repo)
    graph.clear()
    nodes, edges = graph.load(repo)
    print(f"Parsed {args.repo}: {nodes} nodes, {edges} edges loaded into FalkorDB.")
    return 0


def _print_impact(report: dict) -> None:
    fn = report["function"]
    print(f"\nChange impact for `{fn['name']}` ({fn['file']}:{fn['lineno']})")
    print(f"\nAffected functions ({len(report['callers'])} callers, transitive):")
    for c in report["callers"]:
        print(f"  - {c['name']}  ({c['file']}:{c['lineno']})")
    if not report["callers"]:
        print("  (none — nothing in this repo calls it)")
    print(f"\nAffected files ({len(report['files'])}):")
    for f in report["files"]:
        print(f"  - {f}")
    print(f"\nTest files covering it ({len(report['tests'])}):")
    for t in report["tests"]:
        print(f"  - {t}")
    if not report["tests"]:
        print("  (none — no test covers this function)")
    print("\nDependency chain (test -> ... -> changed function):")
    print("  " + (" -> ".join(report["chain"]) if report["chain"] else "(none)"))
    print("\nHot spots (highest call-graph degree):")
    for h in report["hot_spots"]:
        print(f"  - {h['name']} ({h['file']}, degree {h['degree']})")


def cmd_impact(args) -> int:
    graph = _connect(args)
    try:
        report = impact_report(graph, args.function)
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    _print_impact(report)
    return 0


def cmd_summarize(args) -> int:
    graph = _connect(args)
    try:
        report = impact_report(graph, args.function)
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    _print_impact(report)
    print("\n--- Gemma summary ---\n")
    try:
        print(summarize_change(
            args.function,
            report["function"]["code"],
            report,
            ollama_url=args.ollama,
            model=args.model,
        ))
    except Exception as exc:
        print(
            f"Gemma unavailable ({exc}). Is Ollama running with the model "
            f"pulled? Try: ollama pull {args.model}",
            file=sys.stderr,
        )
        return 1
    return 0


def cmd_demo(args) -> int:
    """Scripted end-to-end demo on the bundled sample app."""
    print("== BlastRadius demo: bundled sample-app ==")
    print(f"Sample repo: {SAMPLE_APP}\n")
    graph = _connect(args)
    repo = parse_repo(SAMPLE_APP)
    graph.clear()
    nodes, edges = graph.load(repo)
    print(f"[1] Parsed sample-app: {nodes} nodes, {edges} edges -> FalkorDB\n")

    target = "send_email"
    print(f"[2] Proposed change: modify `{target}` (notify.py)\n")
    report = impact_report(graph, target)
    _print_impact(report)

    print("\n[3] Gemma summary (grounded in the graph results above):\n")
    try:
        print(summarize_change(
            target, report["function"]["code"], report,
            ollama_url=args.ollama, model=args.model,
        ))
        summary_ok = True
    except Exception as exc:
        print(
            f"    [GEMMA UNAVAILABLE] {exc}\n"
            f"    The graph analysis above is real; the model summary could\n"
            f"    not run because no Gemma model is available. Start Ollama\n"
            f"    and run: ollama pull {args.model}"
        )
        summary_ok = False

    print("\n[4] Assertions (demo correctness checks):")
    expected = {"send_receipt", "checkout", "test_checkout"}
    got = {c["name"] for c in report["callers"]}
    missing = expected - got
    print(f"    expected callers {sorted(expected)}")
    print(f"    actual callers   {sorted(got)}")
    assert not missing, f"demo FAILED: missing expected callers {missing}"
    assert report["chain"] == ["test_checkout", "checkout", "send_receipt", "send_email"], (
        f"demo FAILED: unexpected chain {report['chain']}"
    )
    assert "tests/test_cart.py" in report["tests"], "demo FAILED: test file not detected"
    print("    ALL DEMO ASSERTIONS PASSED")
    if not summary_ok:
        print("\nDEMO PARTIAL: graph evidence complete, Gemma summary blocked.")
        return 2
    return 0


def main(argv: list[str] | None = None) -> int:
    # Common options are accepted both before and after the subcommand:
    # `blastradius --model X demo` and `blastradius demo --model X` both work.
    # SUPPRESS defaults on the shared parser + set_defaults on the main
    # parser avoids the subparser default clobbering a top-level value.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--db-host", default=argparse.SUPPRESS)
    common.add_argument("--db-port", type=int, default=argparse.SUPPRESS)
    common.add_argument("--ollama", default=argparse.SUPPRESS)
    common.add_argument("--model", default=argparse.SUPPRESS)
    parser = argparse.ArgumentParser(
        prog="blastradius",
        description="BlastRadius: if I change this function, what breaks?",
        parents=[common],
    )
    parser.set_defaults(
        db_host="localhost",
        db_port=6380,
        ollama="http://localhost:11434",
        model="gemma3:4b",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_parse = sub.add_parser("parse", help="parse a repo into FalkorDB", parents=[common])
    p_parse.add_argument("repo")
    p_parse.set_defaults(func=cmd_parse)

    p_impact = sub.add_parser("impact", help="blast-radius impact of a function", parents=[common])
    p_impact.add_argument("function")
    p_impact.set_defaults(func=cmd_impact)

    p_sum = sub.add_parser("summarize", help="impact + Gemma summary", parents=[common])
    p_sum.add_argument("function")
    p_sum.set_defaults(func=cmd_summarize)

    p_demo = sub.add_parser("demo", help="scripted demo on the bundled sample app", parents=[common])
    p_demo.set_defaults(func=cmd_demo)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
