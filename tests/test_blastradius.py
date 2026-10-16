"""Tests for the BlastRadius parser and impact shaping.

The parser and MemoryGraph tests run anywhere (no database). The
FalkorDB integration test runs only when a FalkorDB server is reachable
on localhost:6380; otherwise it is skipped, honestly.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from blastradius.graph import MemoryGraph, impact_report
from blastradius.parser import is_test_file, parse_repo

SAMPLE_APP = os.path.join(
    os.path.dirname(__file__), "..", "blastradius", "sample-app"
)


class TestParser(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.repo = parse_repo(SAMPLE_APP)

    def test_node_counts(self):
        labels = [n.label for n in self.repo.nodes.values()]
        self.assertEqual(labels.count("File"), 4)
        self.assertEqual(labels.count("Function"), 8)  # 2 + 2 + 2 + 2
        self.assertEqual(labels.count("Class"), 0)

    def test_calls_edges(self):
        edges = {(e.src, e.dst) for e in self.repo.edges if e.type == "CALLS"}
        self.assertIn(("func:cart.py:checkout", "func:notify.py:send_receipt"), edges)
        self.assertIn(("func:notify.py:send_receipt", "func:notify.py:send_email"), edges)
        self.assertIn(("func:cart.py:checkout", "func:pricing.py:calc_discount"), edges)
        self.assertIn(("func:cart.py:checkout", "func:pricing.py:apply_coupon"), edges)
        self.assertIn(("func:pricing.py:apply_coupon", "func:pricing.py:calc_discount"), edges)
        self.assertIn(("func:tests/test_cart.py:test_checkout", "func:cart.py:checkout"), edges)
        self.assertIn(("func:tests/test_cart.py:test_discount", "func:pricing.py:calc_discount"), edges)

    def test_imports_edges(self):
        edges = {(e.src, e.dst) for e in self.repo.edges if e.type == "IMPORTS"}
        self.assertIn(("file:cart.py", "file:notify.py"), edges)
        self.assertIn(("file:cart.py", "file:pricing.py"), edges)

    def test_defines_edges(self):
        edges = {(e.src, e.dst) for e in self.repo.edges if e.type == "DEFINES"}
        self.assertIn(("file:notify.py", "func:notify.py:send_email"), edges)

    def test_is_test_file(self):
        self.assertTrue(is_test_file("tests/test_cart.py"))
        self.assertTrue(is_test_file("foo_test.py"))
        self.assertFalse(is_test_file("cart.py"))


class TestMemoryGraph(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.graph = MemoryGraph(parse_repo(SAMPLE_APP))

    def test_blast_radius_send_email(self):
        got = {c["name"] for c in self.graph.blast_radius("send_email")}
        self.assertEqual(got, {"send_receipt", "checkout", "test_checkout"})

    def test_blast_radius_calc_discount(self):
        got = {c["name"] for c in self.graph.blast_radius("calc_discount")}
        self.assertEqual(
            got, {"apply_coupon", "checkout", "test_checkout", "test_discount"}
        )

    def test_shortest_path(self):
        self.assertEqual(
            self.graph.shortest_path("test_checkout", "send_email"),
            ["test_checkout", "checkout", "send_receipt", "send_email"],
        )

    def test_hot_spots(self):
        hot = self.graph.hot_spots()
        self.assertGreaterEqual(len(hot), 1)
        # checkout has the most edges (3 out + 1 in = 4)
        self.assertEqual(hot[0]["name"], "checkout")
        self.assertEqual(hot[0]["degree"], 4)

    def test_impact_report(self):
        report = impact_report(self.graph, "send_email")
        self.assertEqual(report["files"], ["cart.py", "notify.py", "tests/test_cart.py"])
        self.assertEqual(report["tests"], ["tests/test_cart.py"])
        self.assertEqual(
            report["chain"],
            ["test_checkout", "checkout", "send_receipt", "send_email"],
        )

    def test_impact_unknown_function(self):
        with self.assertRaises(ValueError):
            impact_report(self.graph, "no_such_function")


class TestSummarizePrompt(unittest.TestCase):
    """Stub-based test of the summarize path (no live model needed).

    Proves the prompt is grounded in the graph results: the impact facts
    are passed into the model prompt, and the model's text is returned
    verbatim. This does NOT prove a live Gemma run — that requires
    Ollama with a pulled model.
    """

    def test_prompt_contains_graph_facts(self):
        import json
        from unittest import mock

        from blastradius.summarize import summarize_change

        impact = {
            "callers": [{"name": "checkout", "file": "cart.py"}],
            "files": ["cart.py", "notify.py"],
            "tests": ["tests/test_cart.py"],
            "hot_spots": [{"name": "checkout", "file": "cart.py", "degree": 4}],
            "chain": ["test_checkout", "checkout", "send_receipt", "send_email"],
        }
        captured: dict = {}

        class FakeResp:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def read(self):
                return json.dumps({"response": "stub summary"}).encode()

        def fake_urlopen(req, timeout=None):
            captured["body"] = json.loads(req.data)
            captured["url"] = req.full_url
            return FakeResp()

        with mock.patch("blastradius.summarize.urllib.request.urlopen", fake_urlopen):
            out = summarize_change(
                "send_email", "def send_email(): ...", impact,
                ollama_url="http://localhost:11434", model="gemma3:1b",
            )
        self.assertEqual(out, "stub summary")
        prompt = captured["body"]["prompt"]
        for fact in ("checkout", "cart.py", "tests/test_cart.py",
                     "test_checkout -> checkout -> send_receipt -> send_email"):
            self.assertIn(fact, prompt)
        self.assertEqual(captured["body"]["model"], "gemma3:1b")
        self.assertTrue(captured["url"].endswith("/api/generate"))


def _falkor_reachable() -> bool:
    try:
        import socket

        with socket.create_connection(("localhost", 6380), timeout=1):
            return True
    except OSError:
        return False


@unittest.skipUnless(_falkor_reachable(), "FalkorDB not reachable on localhost:6380")
class TestFalkorGraph(unittest.TestCase):
    """Integration test: same expectations, real FalkorDB + real Cypher."""

    @classmethod
    def setUpClass(cls):
        from blastradius.graph import FalkorGraph

        cls.graph = FalkorGraph()
        cls.graph.clear()
        cls.graph.load(parse_repo(SAMPLE_APP))

    def test_blast_radius_matches_memory_backend(self):
        got = {c["name"] for c in self.graph.blast_radius("send_email")}
        self.assertEqual(got, {"send_receipt", "checkout", "test_checkout"})

    def test_impact_report(self):
        report = impact_report(self.graph, "send_email")
        self.assertEqual(report["tests"], ["tests/test_cart.py"])
        self.assertEqual(
            report["chain"],
            ["test_checkout", "checkout", "send_receipt", "send_email"],
        )

    def test_shortest_path(self):
        self.assertEqual(
            self.graph.shortest_path("test_checkout", "send_email"),
            ["test_checkout", "checkout", "send_receipt", "send_email"],
        )


if __name__ == "__main__":
    unittest.main()
