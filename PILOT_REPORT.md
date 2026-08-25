# Pilot Report — 2026-08-25

Three arms × two optimizer seeds, 20 Luna calls and ≤750 matches per run,
per PLAN.md §15. All six runs completed. Purpose: verify the machinery and
measure the funnel — **not** to rank arms (n=2 seeds cannot).

## Scores (macro-average over the 48 Pareto instances, seed = 0.1187)

| run | best macro | accepted children | patch attempts | no_change | compiled 1st try |
|-----|-----------|-------------------|----------------|-----------|------------------|
| score_greedy s0 | 0.1624 | 5 | 10 | 9 | 9/10 |
| score_greedy s1 | 0.1617 | 4 | 9 | 8 | 6/9 |
| replay_greedy s0 | **0.2704** | 6 | 15 | 2 | 12/15 |
| replay_greedy s1 | 0.1352 | 7 | 15 | 2 | 13/15 |
| gepa_pareto s0 | 0.1187 | 8 | 16 | 1 | 13/16 |
| gepa_pareto s1 | 0.2577 | 8 | 16 | 3 | 15/16 |

The margin term is 10% of the score scale, so these differences are mostly
real outcome changes, not margin noise.

## What the pilot establishes

1. **The machinery works end to end.** 120 real model calls, ~3,000 engine
   matches (heavy cache reuse: 58–75 hits/run), 38 gate-accepted children,
   exactly one compile-rejected patch after repair across all runs. Structured
   outputs parsed on every call. Total API cost: **$2.67** (8.1M in / 0.87M out
   tokens). Wall clock ~1.5–3h per run with six concurrent on 4 cores.
2. **Luna clears the pilot gate** (PLAN §4): ~70% first-try compile rate on
   441-line-bot component rewrites, well above the 60% bar.
3. **Arms behave differently in the predicted direction.** The score-only arm
   declined to patch on ~45% of calls (blind mutation is hard to justify from
   two bits); the replay arms proposed patches on ~87% of calls. Reflection
   evidently gives the model conviction. Paired per-seed differences
   (replay − score) went +0.108 (s0) and −0.027 (s1) — means: score 0.162,
   replay 0.203, pareto 0.188. Directionally encouraging, decides nothing.
4. **Seed variance is as large as predicted.** replay_greedy spans 0.135–0.270
   across two seeds; gepa_pareto spans 0.119–0.258. The main experiment cannot
   power a 5pp claim at n=5 (PLAN §17 stands); pairing and more seeds are
   mandatory.
5. **Pareto arm explores as designed.** It accepted the most children (8/8 per
   seed) but its macro-best lagged in one seed (s0 champion = the seed itself:
   children entered the pool as specialists without beating the seed's
   macro-average). This is the intended behavior at tiny budgets, and exactly
   why champion selection at 20 calls says nothing about GEPA vs greedy.

## Gaps found (fix before the main run)

1. **Reflection JSON is not persisted** — model_calls.jsonl stores only usage
   metadata, so the §16 groundedness analysis (% cited rounds that exist)
   cannot be computed post-hoc. Persist each call's parsed reflection +
   trace manifest per iteration.
2. **Loop resume re-spends model calls** (restart-based). Add mid-run
   checkpointing before the 80-call main runs.
3. **`wall_seconds` missing from some summaries** — minor logging bug.
4. A temp-replay filename race between concurrent runs was found and fixed
   during the pilot (per-process replay names, commit 285bab6).

## Recommendation for the main experiment

Per PLAN §15/§17: keep the paired CRN design; prefer **3 arms × 8 seeds**
(score_greedy / replay_greedy / gepa_pareto) over 4 arms × 5 if compute is
constrained, and evaluate champions on the enlarged held-out grid (≥300 games).
Given pilot throughput (~2h/run at 20 calls), an 80-call run is ~6–8h; 24 runs
on this 4-core box ≈ 5–7 days sequential-ish, or rent more cores.
