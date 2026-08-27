# Main Experiment Results — 2026-08-27

3 arms × 8 optimizer seeds, 80 Luna calls per run (PLAN.md v3, paired CRN
design). All 24 runs completed; every champion plus the seed baseline was then
evaluated on 276 held-out games (three quadrants, exact match cache, optimizer
never saw these). Scores are win rates (win=1, tie/coin-flip=0.5, loss=0).

## Headline

**Replay reflection works; GEPA's Pareto selection adds nothing detectable at
this budget.** On the primary joint quadrant (unseen opponent lineages ×
unseen maps), paired per-seed differences with bootstrap-over-seeds 95% CIs
(10,000 resamples):

| comparison | mean Δ (joint) | 95% CI | seeds +/− |
|---|---|---|---|
| Replay-Greedy − Score-Greedy | **+3.3pp** | [+1.5, +5.7] | 7/0 |
| GEPA-Pareto − Replay-Greedy | −0.7pp | [−3.0, +1.2] | 3/3 |
| GEPA-Pareto − Score-Greedy | +2.5pp | [−0.3, +5.5] | 5/2 |

The reflection effect is consistent (never negative in 8 paired seeds) though
below the plan's pre-registered 5pp bar. The GEPA−Replay contrast is a clean
null.

## Arm means (8 seeds each; seed bot baseline in last row)

| arm | train macro | joint (primary) | map-gen | opp-gen |
|---|---|---|---|---|
| Score-Greedy | 0.171 | 0.247 | 0.163 | 0.307 |
| Replay-Greedy | **0.248** | **0.280** | **0.208** | **0.323** |
| GEPA-Pareto | 0.207 | 0.272 | 0.190 | 0.302 |
| seed (lectureplayer) | 0.119* | 0.262 | 0.173 | 0.333 |

*train macro for the seed is its score on the 48-game Pareto grid.

Three structural findings behind the numbers:

1. **Greedy's training lead was substantially overfitting.** Replay-Greedy
   out-trained GEPA by 4.1pp (0.248 vs 0.207) but leads by only 0.7pp on the
   joint exam — GEPA's diverse pools carried their training gains
   out-of-distribution almost fully; greedy's deep single-lineage champions
   did not (e.g. its 0.398 champion fell to 0.286).
2. **A hard opponent ceiling caps everything.** Across all 24 champions ×
   56 games vs the two strong unseen lineages (uravt, awu7): zero wins. Every
   joint-quadrant point comes from the weak unseen lineage (win rate vs
   r3vivify: replay 0.84, gepa 0.82, score 0.74, ~0.72 seed). 80 mutations of
   a lecture bot do not bridge a finalist-tier gap.
3. **Blind mutation transfers nothing.** Score-Greedy's champions score at or
   slightly below the seed on the joint grid (0.247 vs 0.262) despite +5pp of
   training gain — its wins were memorized, not learned.

## Per-run joint scores

| seed | Score | Replay | GEPA |
|---|---|---|---|
| 0 | 0.238 | 0.262 | 0.286 |
| 1 | 0.286 | 0.286 | 0.250 |
| 2 | 0.274 | 0.286 | 0.310 |
| 3 | 0.226 | 0.250 | 0.226 |
| 4 | 0.202 | 0.310 | 0.310 |
| 5 | 0.286 | 0.333 | 0.262 |
| 6 | 0.238 | 0.262 | 0.286 |
| 7 | 0.226 | 0.250 | 0.250 |

## Funnel (per arm, totals over 8 runs; 1,920 model calls, $41 API,
127M input / 13M output tokens)

| arm | patch proposals | compiled 1st try | gate-accepted | no_change calls |
|---|---|---|---|---|
| GEPA-Pareto | 470 | 362 (77%) | 245 (52%) | 55 |
| Replay-Greedy | 420 | 319 (76%) | 193 (46%) | 109 |
| Score-Greedy | 307 | 258 (84%) | 141 (46%) | 279 |

Selection behavior matched design: GEPA mutated 20–21 distinct parents per
run (~4 mutations/lineage, pools of 25–33); greedy arms concentrated 21–63
mutations on their top lineage.

## Groundedness

24 sampled reflections across 12 runs: 68/68 evidence entries referenced a
real provided replay; 315/315 cited rounds fall within the actual game
length. The model's claimed evidence is not fabricated at the round level.

## Interpretation vs the plan's questions (PLAN.md §2)

- **Does trajectory reflection help?** Yes — +3.3pp joint held-out, positive
  in 7/8 paired seeds, CI excluding zero (below the 5pp pre-registered bar,
  so: real but modest at this budget).
- **Does Pareto selection help beyond reflection?** No detectable effect
  (−0.7pp, CI spans zero). Its diversity did close greedy's overfitting gap,
  so the mechanism behaves as theorized — it just doesn't pay net at 80
  calls with champion-by-macro selection.
- **Do improvements generalize?** Partially: to unseen maps (replay +3.5pp
  over seed on map-gen) and against a weak unseen lineage; not at all
  against strong unseen lineages (hard ceiling).

## Caveats

- One usable weak test lineage (r3vivify) dominates the joint signal;
  adamtan's lineage was excluded (build failure), leaving 3 test lineages of
  which 2 are unbeatable for this seed. Claims are scoped to these lineages
  (fixed effects), per PLAN §17.
- Champion selection by macro-average structurally favors greedy; GEPA's
  pool diversity is not credited unless a specialist overtakes on average.
- Budget: 80 calls/run is far below the regime where the GEPA paper's
  selection gains accrue; this is a small-budget test of the mechanism.

## What would move the needle next

1. A stronger seed bot (the ceiling, not the optimizer, binds vs finalists).
2. Longer runs (300+ calls) before re-testing the GEPA contrast.
3. Crediting pools rather than single champions (e.g. best-of-pool per
   opponent), which is what GEPA's diversity actually optimizes.
