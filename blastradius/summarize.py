"""Change summaries from Gemma (open-weight) via Ollama.

The model is load-bearing but NOT authoritative: the prompt is grounded
in the graph results (affected functions, files, tests, hot spots) and
the model is told to discuss only those. It turns graph facts into a
readable summary. The graph results are the authority; the summary is a
draft — small models (1B) can still garble details, so verify before
acting. Use gemma3:4b for better fidelity.
"""

from __future__ import annotations

import json
import urllib.request


def summarize_change(func_name: str, func_code: str, impact: dict,
                     ollama_url: str = "http://localhost:11434",
                     model: str = "gemma3:4b") -> str:
    """Ask Gemma to summarize what a change to ``func_name`` would affect."""
    callers = "\n".join(f"- {c['name']} ({c['file']})" for c in impact["callers"]) or "- (none)"
    files = ", ".join(impact["files"]) or "(none)"
    tests = ", ".join(impact["tests"]) or "(none — no test covers this function)"
    hot = "\n".join(f"- {h['name']} ({h['file']}, degree {h['degree']})" for h in impact["hot_spots"])
    chain = " -> ".join(impact["chain"]) or "(no dependency chain)"

    prompt = f"""You are reviewing a proposed change to the Python function `{func_name}`.

Here is its current source code:
```
{func_code}
```

A code-graph analysis (Cypher queries over the repo's call graph) found these FACTS. Do not invent other functions, files, or tests:
- Functions that transitively call `{func_name}` (i.e. could break if it changes):
{callers}
- Files affected: {files}
- Test files that exercise it: {tests}
- Longest dependency chain (test -> ... -> changed function): {chain}
- Hot spots in this repo (highest call-graph degree):
{hot}

Write a short, concrete summary with exactly these sections:
1. What could break and why (refer to the callers above by name)
2. Suggested tests to run / add (only the test files above, or new tests for the callers)
3. Risk: Low / Medium / High, with one sentence of justification

CRITICAL: Every file name you mention MUST appear in the facts above. Never invent a file name. In particular, if the only test file listed above is `tests/test_cart.py`, do not mention any other test file.

Keep it under 250 words. If no callers were found, say the function appears unused and the risk is Low.
"""
    payload = json.dumps({
        "model": model,
        "prompt": prompt,
        "stream": False,
        "options": {"temperature": 0.2},
    }).encode()
    req = urllib.request.Request(
        ollama_url.rstrip("/") + "/api/generate",
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=300) as resp:
        return json.loads(resp.read())["response"].strip()
