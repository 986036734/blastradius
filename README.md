# BlastRadius

**If I change this function, what breaks?**

BlastRadius parses a Python repo into a code graph in **FalkorDB** and
answers impact questions with real Cypher queries: multi-hop blast
radius, shortest dependency chains, and degree-centrality hot spots.
A local open-weight model (**Gemma** via Ollama) then turns the graph
facts into a readable change summary, grounded in the query results.
The graph results are authoritative; the summary is a draft — small
local models (1B) can still garble details, so verify before acting.
Use `gemma3:4b` for better fidelity.

Built Oct 16–17, 2026 for a dual submission:

- **Graph Hacks** (WeMakeDevs × FalkorDB) — **Track 1: Agents That Act
  on Connected Data**. Submissions close Mon Oct 19, 02:29 Beijing.
- **DEV Hacktoberfest Week 2: Side Hustle** — deadline Mon Oct 19,
  14:59 Beijing.

## The side hustle

Every developer knows the fear: change one function, break something
three files away, find out in production. BlastRadius is the
pre-change check: point it at a function, get the full transitive
impact set, the files, the tests that cover it, and a summary of what
could break.

Pricing story: free local use for one repo; **per-repo scan** or a
**small team plan** for shared impact reports across a codebase.
Local-first keeps the business honest — no per-token API bill, so a
small subscription actually covers costs.

## Why open innovation matters

Two reasons. **Privacy:** a call graph plus change summaries are a
map of your proprietary codebase — sending that to a closed API means
shipping your architecture to someone else's server. **Cost:** a local
open-weight model costs $0 per token, which is what makes the
side-hustle pricing work at all. The open pieces (Gemma + FalkorDB +
Ollama) are what make the product possible, not garnish.

## Quickstart

```bash
# 1. FalkorDB (Docker)
docker run -d --name falkordb -p 6380:6379 falkordb/falkordb:latest

# 2. Ollama + Gemma (open-weight model for summaries)
curl -fsSL https://ollama.com/install.sh | sh
ollama pull gemma3:4b   # 1b works too on low-RAM machines

# 3. BlastRadius
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
python -m blastradius demo
```

## Usage

```bash
python -m blastradius parse <repo_path>        # load a repo into FalkorDB
python -m blastradius impact <function_name>   # blast radius + files + tests
python -m blastradius summarize <function_name> # impact + Gemma summary
python -m blastradius demo                     # scripted demo on bundled sample-app
```

Options: `--db-host`, `--db-port` (default `localhost:6380`),
`--ollama` (default `http://localhost:11434`), `--model`
(default `gemma3:4b`).

## Graph data model

Nodes:

| Label      | Properties                          |
|------------|-------------------------------------|
| `File`     | `key`, `name` (relpath), `file`    |
| `Function` | `key`, `name` (qualname), `file`, `lineno`, `code` |
| `Class`    | `key`, `name` (qualname), `file`, `lineno` |

Edges:

| Type      | Meaning                              |
|-----------|--------------------------------------|
| `DEFINES` | File defines a Function / Class      |
| `IMPORTS` | File imports a local File            |
| `CALLS`   | Function calls a Function            |

`CALLS` resolution is **name-based**: a call `foo()` links to every
defined function named `foo`. This is a deliberate *may-call*
over-approximation — the blast radius never misses a real caller, at the
cost of occasionally including a false one when names collide.

## Example Cypher queries

Blast radius — everything that transitively calls `send_email`:

```cypher
MATCH (start:Function {name: 'send_email'})<-[:CALLS*1..5]-(caller:Function)
RETURN DISTINCT caller.name, caller.file, caller.lineno
```

Shortest dependency chain from a test back to the changed function:

```cypher
MATCH (a:Function {name: 'test_checkout'}), (b:Function {name: 'send_email'}),
      p = shortestPath((a)-[:CALLS*]->(b))
RETURN [n IN nodes(p) | n.name] AS path
```

Hot spots (highest call-graph degree):

```cypher
MATCH (f:Function)-[r:CALLS]-()
RETURN f.name, f.file, count(r) AS degree
ORDER BY degree DESC LIMIT 5
```

## Architecture

```
blastradius/
  parser.py    stdlib ast -> nodes/edges (File/Function/Class, DEFINES/IMPORTS/CALLS)
  graph.py     FalkorDB backend (real Cypher) + MemoryGraph (tests only)
  summarize.py Gemma via Ollama, prompt grounded in graph results
  __main__.py  CLI: parse / impact / summarize / demo
  sample-app/  bundled 4-file shop app used by the demo
tests/         parser + impact-shaping tests; FalkorDB integration test
```

The impact set is **always** computed by Cypher in FalkorDB. The model
only summarizes facts the graph already produced.

## Tests

```bash
python -m unittest discover -s tests -v
```

The three `TestFalkorGraph` integration tests run only when a FalkorDB
server is reachable on `localhost:6380`; otherwise they skip.

## Demo video

[Demo video](https://github.com/986036734/blastradius/releases/download/v1.0.0/blastradius-demo.mp4) — the scripted demo on the bundled sample-app: parse, impact analysis of `send_email`, and a Gemma-written change summary.

## AI disclosure

This project was built with **Muse** (Meta's AI coding assistant) and
delegated AI subagents, under the direction of the human author (Steve),
who designed the tool, set the requirements, and verified the work.
Every functional claim was verified by running the real test suite
(15/15) and the live demo against a real FalkorDB instance and a
locally running Gemma model. The human author reviewed the docs and
corrected overstrong claims found during testing (e.g. the 1B model's
occasional invented filenames are disclosed, not hidden).

## License

MIT.
