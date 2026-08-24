# gepa-battlecode

Does GEPA-style optimization (trajectory reflection + Pareto candidate selection +
system-aware merge) help an LLM evolve a stronger MIT Battlecode 2026 bot?
Full design: [PLAN.md](PLAN.md).

One LLM (`gpt-5.6-luna`) reflects on decoded match replays and rewrites one bot
component at a time; the Battlecode engine is the only evaluator. Four arms
ablate the mechanisms: outcomes-only → +replay reflection → +Pareto selection
→ +merge.

## Setup

```bash
./scripts/setup_engine.sh        # clone + pin battlecode26 engine and seed bot
pip install flatbuffers tiktoken openai pyyaml pytest
python scripts/smoke_test.py     # run one match end-to-end and decode it
```

Requires Java 21 (engine) and Python 3.11+. Set `OPENAI_API_KEY` for optimizer
runs (never needed for tests).

## Layout

| Path | Purpose |
|------|---------|
| `configs/` | Frozen pins and knobs: engine/model locks, experiment.yaml, model-facing rules digest |
| `replay/` | `.bc26` → decoded match → tiered deterministic trace → token-budgeted packing |
| `harness/` | Headless match runner (one map per JVM), exact match cache, static patch validator |
| `model/` | Responses API client, structured-output schemas, prompt builders, compile repair |
| `optimizer/` | Candidate store, Pareto selection (+dominance pruning), gate, sampler, merge, loop |
| `evaluation/` | Win/margin scoring, statistics |
| `bots/` | `original_seed/` (lectureplayer) and the modular refactor |
| `tests/` | Unit + integration tests (`pytest -q`; `-m slow` runs a real match) |

## Invariants

- No hand-written diagnostics, no LLM summarizer, no judge: the only model-facing
  gameplay evidence is `official replay → frozen deterministic transform`.
- All arms share the model, seeds, scenario schedules, and budgets; only the
  intended mechanism differs.
- Matches are deterministic and content-addressed; the cache is exact.
