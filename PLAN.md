# Battlecode 2026 × GEPA — Master Experimental Plan v3

Supersedes v2. Same core design (four-arm ablation of GEPA-style code evolution),
updated with verified engine facts and fixes to four problems that would have made
v2's headline result uninformative.

## Changes from v2

| # | Change | Why |
|---|--------|-----|
| 1 | Model is **GPT-5.6 Luna** (`gpt-5.6-luna`), not Sol | Cost decision. Same 1.05M context / 128K output, ~20× cheaper ($0.20/$1.20 per Mtok vs $4/$20). All arms use the same model so comparisons stay internally valid. Pilot explicitly measures Luna's patch-compile rate before committing. |
| 2 | Tiered deterministic trace is the **primary** replay representation, not a fallback | A fully decoded game is ~0.4–1.1M tokens (worst ~6M). "1–3 complete bundles" never fit. v2's fallback ladder ended in mid-game chunking, violating its own no-truncation rule. |
| 3 | **Continuous match scoring** (win + points margin), not bare 0/0.5/1 | Deterministic engine + coarse scores made v2's acceptance gate reject most genuinely good patches (any patch not exercised by the reflection games ties exactly). The replay's per-round team aggregates + `winType` give a deterministic margin for free. |
| 4 | Pareto instances are the **48 individual games**, not 12 aggregated keys; GEPA's dominance pruning added | 12 keys × 5 score levels ⇒ ties everywhere ⇒ Pareto selection degenerates to greedy/uniform and the headline comparison is a foregone null. |
| 5 | Acceptance gate: **disjoint minibatch** + margin tiebreak + capped neutral-drift accepts | v2 gated on the exact reflection scenarios — selection pressure *for* overfitting them. GEPA itself separates the reflection minibatch from selection. |
| 6 | **Paired (common-random-numbers) design**, bigger held-out grid, estimation-based reporting | 5 seeds/arm has a minimum detectable effect of ~17–25pp; the v2 held-out cell (~32 games) had ~10pp measurement noise alone. 5pp-with-CI-excluding-zero was unpowerable. |
| 7 | Mutations are **full-file component rewrites**, not unified diffs | Diff application is a brittle failure mode that burns model calls. The 250-line cap is enforced by diffing old vs new ourselves. |
| 8 | **5 components**, not 8 | The seed bot is 441 lines; 8 components would be mostly empty and waste round-robin slots. |
| 9 | Engine gotchas encoded as harness rules (see §5) | Verified against engine source at pinned commit. |
| 10 | Opponent list replaced with **verified, licensed 2026 repos** | See §10. `battlecode/battlecode-lectureplayer` does not exist; the real repo is `battlecode/battlecode26-lectureplayer`. |

## 1. Goal

Test whether GEPA-style optimization (trajectory reflection + Pareto candidate
selection + system-aware merge) helps a single LLM improve a Battlecode 2026 bot,
with each mechanism's contribution measured separately.

The loop per iteration: select parent bot → play matches → decode replays →
LLM reflects on trajectories + code → LLM rewrites one component → engine
evaluates → keep if better → repeat.

Exactly one LLM (reflection + mutation + compile repair). The evaluator is the
Battlecode engine — model-free.

## 2. Research questions

Primary: does GEPA's Pareto-based candidate selection produce stronger bots than
greedily improving the best bot, given identical replay reflection?
(GEPA-Pareto − Replay-Greedy)

Secondary: value of trajectory reflection (Replay-Greedy − Score-Greedy); value
of merge (GEPA-Full − GEPA-Pareto); generalization to unseen maps/opponents;
sample efficiency.

Framing: this is "GEPA-style selection applied to LLM code evolution", not a test
of GEPA itself — compilation failure, component coupling, and a rationed LLM-call
budget are confounds absent from the paper. Cite AlphaEvolve, FunSearch, ELM,
EoH/ReEvo (ReEvo ≈ our Replay-Greedy arm), Eureka, Voyager.

## 3. Experimental arms

| Arm | Parent selection | Model sees | Merge |
|-----|------------------|-----------|-------|
| A. Score-Greedy | best macro-average | outcomes only | no |
| B. Replay-Greedy | best macro-average | decoded traces | no |
| C. GEPA-Pareto | Pareto coverage sampling | decoded traces | no |
| D. GEPA-Full | Pareto coverage sampling | decoded traces | yes |

Everything else identical across arms: seed bot, model, maps, opponents, budgets,
patch rules, gate, and (paired design, §14) the per-iteration scenario schedules.

## 4. Model

```
gpt-5.6-luna          (freeze exact ID/snapshot in configs/model.lock.json)
OpenAI Responses API
structured outputs (JSON schema)
reasoning effort: high
tools: none
```

1.05M-token context, 128K max output, $0.20/$1.20 per Mtok. Prompt-cache the
stable prefix (rules + component interfaces). Budget note: at a 150–250K replay
budget per reflection call, main-experiment input is ~$60–130 — cost is no longer
a binding constraint; the 80-call budget is kept for sample-efficiency comparability.

Pilot gate for Luna specifically: if initial compile rate < 60% or grounded-citation
rate (§16) is poor, escalate reasoning effort or reconsider the model before the
main run. All arms always share whatever model is frozen.

## 5. Engine facts and harness rules (verified at pinned commit)

Engine: `battlecode/battlecode26@103abf6b67a2cf544e6344dddef9318af9ae9193`, Java 21.
74 official `.map26` maps. Headless runner:

```
./gradlew headless -PteamA=<pkg> -PteamB=<pkg> -Pmaps=<Map> \
    -PvalidateMaps=false -PalternateOrder=false -Preplay=<out.bc26>
```

Rules the harness must enforce (each is verified engine behavior, not speculation):

1. **One map per JVM invocation.** Cat-AI fallback RNG is a static
   `Random(1092)` seeded once per JVM; multi-map runs are only reproducible as a
   whole sequence.
2. **The match seed is baked into each `.map26`** (`GameMap.randomSeed`); no CLI
   override. (candidate, opponent, map, side) ⇒ exactly one outcome. Replaying a
   cell adds zero information. Side swap does change outcomes. Extra variance, if
   ever needed, comes from minting seed-variant maps by patching the map
   flatbuffer — a frozen, deterministic transform.
3. **Score by `MatchFooter.winType`, never the raw winner byte for ties.**
   The last-resort tiebreak (`WinType.COIN_FLIP`) uses uninstrumented
   `Math.random()` — the one nondeterministic bit in the engine. `TIE`/`COIN_FLIP`
   ⇒ 0.5.
4. **Indicator strings are disabled** (`-PshowIndicators=false`). They are a
   256-char/robot/turn free-text channel into the replay that evolved bots could
   use to leak notes to the reflector. (Revisit as a deliberate mechanism later;
   off for the primary experiment.)
5. **Comms contents are not in replays** (only RatSqueak locations). The decoder
   does not pretend otherwise.
6. **Bytecode limits make "cosmetic" refactors behaviorally real** (17.5K/turn
   baby rat, 20K rat king; overrun pauses code mid-turn). See §9.
7. Matches otherwise fully deterministic (verified byte-identical replays), so the
   match cache is exact: key = (engine commit, candidate hash, opponent hash, map,
   side, runner config). Cache is shared across arms and seeds.
8. Timing: ~4–5s warm per trivial-bot match, JVM startup included; real bots
   slower; hard cap 20 min/team. Persistent-daemon optimization conflicts with
   rule 1 — fork per match unless a reflection-reset is verified byte-identical.

## 6. Replay decoding: tiered deterministic trace

`.bc26` = gzipped FlatBuffers; decode with the engine's bundled Python bindings
(`schema/python`). The `Turn` table carries full per-robot state every round —
no game-logic reimplementation.

The model-facing trace is a deterministic, content-independent projection with
four tiers (all knobs frozen in `configs/experiment.yaml` before the pilot,
identical for every arm, candidate, and outcome):

```
T0 header    map name/size/symmetry, mines, cat waypoints, initial spawns
T1 aggregate every round: per-team cheese transferred, cat damage, alive
             kings/rats, trap/dirt counts   (~15 tok/round; whole game ≤ ~35K)
T2 events    always kept, every round: spawns, deaths (with EXCEPTION flag),
             king upgrades, trap placements/triggers, ratnaps/throws, cat
             feed/pounce, the cooperation→backstab flip, squeaks
T3 unit state strided full snapshots (every unit: pos/health/cheese) every
             N rounds (default N=20), plus dense windows of ±W rounds (default
             W=10) around T2 "decisive" events: king deaths, backstab flip,
             first inter-team contact, final 50 rounds
```

Run-length/delta encoding within tiers is allowed. No semantic judgment, no
labels, no LLM summarization anywhere in the pipeline. If a trace still exceeds
budget, degrade deterministically: N=40 → drop T3 except windows → shrink W.
Never truncate mid-game; never select events by "importance" beyond the frozen
T2 list.

§17 pilot measurement calibrates N and W from 50 representative matches, then
freezes them.

## 7. Context budget

`replay_token_budget = 250K tokens` (fixed, not a context fraction — Luna's 1.05M
context makes a fraction meaningless and expensive). Pack complete game traces in
sampler order until the next one would overflow; 1–4 complete games per call.
Remaining context: system prompt, frozen rules digest (`configs/game_rules.md`),
current component source, other components' interfaces, last 3 accepted patches.

## 8. Model I/O

Input as v2 §11 (no human diagnosis anywhere). Output via structured outputs:

```json
{
  "action": "patch" | "no_change",
  "reflection": {
    "observations": ["..."],
    "causal_hypothesis": "...",
    "general_lesson": "...",
    "evidence": [{"replay_id": "...", "rounds": [..], "explanation": "..."}]
  },
  "mutation": {
    "target_component": "...",
    "hypothesis": "...",
    "expected_improvement": "...",
    "regression_risks": ["..."],
    "component_source": "<complete new source for the ONE selected component>"
  }
}
```

Full-file rewrite of the selected component only. The harness computes the diff
and rejects patches > 250 changed lines or touching anything else. One compile-
repair attempt (compiler output + previous attempt, no new match info); counts
against the call budget. Cited rounds are checked against the trace for the §16
groundedness analysis.

## 9. Seed bot

`battlecode/battlecode26-lectureplayer` (AGPL-3.0), Java `lectureplayer`
(441-line `RobotPlayer.java`), copied to `bots/original_seed/`. Refactor into
**5 components**: `economy`, `combat`, `defense`, `navigation`, `strategy`
(cooperation/backstab + comms + parameters).

Behavioral equivalence check (replaces v2's impossible "100% action equivalence"):
- Verify the refactor's per-turn `bytecodesUsed` (recorded in every replay `Turn`)
  keeps ≥30% headroom under the limit on the 20-game check suite.
- Require identical decoded action streams on all 20 games **given** that
  headroom; if any diverge, fix until they don't. Divergence with headroom means
  a real semantic change, not bytecode drift.

## 10. Opponents (verified 2026, licensed)

Development pool (6 snapshots, ≥3 lineages, graded strength):

| Source | License | Notes |
|--------|---------|-------|
| engine `examplefuncsplayer` | engine license | floor-strength |
| `battlecode/battlecode26-lectureplayer` | AGPL-3.0 | baseline |
| `AlexT101/battlecode26` — `sprint1bot`, `sprint2bot`, `finalsbot` | AGPL-3.0 | top-12 finalist; 3 graded snapshots, one lineage |
| `spsquared/battlecode26` — `Delta` | AGPL-3.0 | TSPAARK final bot |

Held-out test pool (≥4 snapshots, ≥2 unseen lineages):
`uravt/Battlecode26` (2nd place finals), `AdamTan12/BattleCode2026` (finalist),
`awu7/battlecode-2026`, optionally `nzjt/battlecode26` (verify it compiles
against the stock pinned engine — repo bundles engine mods) and
`r3viviFY/battlecode26_released` (MIT; weak NN-generated bots — floor-strength
test opponent).

Excluded: unlicensed repos (`erikji`, `daannte` — ask permission if wanted) and
all **Cambridge** Battlecode 2026 repos (different competition, Python engine —
filter by engine scaffold, not by license).

Pipeline per opponent: pin commit → verify license → compile against pinned
engine → calibration round-robin → empirical strength tier. Graded strength is
load-bearing: the seed must be able to win against the weak tier or there is no
gradient for any arm. Lineage separation as v2 §17 (split whole lineages; check
source similarity for forks).

## 11. Match scoring

Per game, deterministic, computed from the replay footer + final-round aggregates:

```
outcome = 1 / 0.5 / 0 by winType (TIE and COIN_FLIP ⇒ 0.5)
margin  = normalized points differential from the official formulas
          (coop:     0.5·%catDamage + 0.3·%livingKings + 0.2·%cheeseTransferred
           backstab: 0.3·%catDamage + 0.5·%livingKings + 0.2·%cheeseTransferred)
score   = outcome + λ·margin,  λ = 0.1, score clamped to [outcome−0.05·…] —
          concretely: margin ∈ [−1,1], so score ∈ [outcome−0.1, outcome+0.1];
          outcome always dominates (a win beats any loss regardless of margins).
```

Win rate (outcome only) remains the *reported* metric everywhere. The margin term
exists so search-internal comparisons (gate, Pareto instance scores) are
continuous and strict `>` is meaningful under determinism.

## 12. Splits, Pareto set, reflection sampling

Maps: compute metadata for all 74 maps (from raw flatbuffers, no engine needed),
group rotated/reflected geometries, then ~60% feedback / ~20% Pareto / ~20% test.
Frozen before optimization.

Pareto validation set: 6 dev opponents × 4 Pareto maps × 2 sides =
**48 instances** — each individual game is one Pareto instance scored by §11.
The optimizer sees numbers only; the model never sees Pareto replays.

Reflection sampling per iteration (identical logic in all arms):
1. Weakest instances: rank Pareto instances by `best_pool_score − parent_score`
   (fallback when parent leads everywhere: parent's absolute-weakest instances).
2. Map each to an analogous feedback-set scenario (same opponent, feedback map).
3. Pack complete traces under the §7 budget: target 2 weak + 1 won scenario.

## 13. Acceptance gate and Pareto selection

Gate (all arms):
- Acceptance minibatch = the reflection scenarios **plus 4 disjoint feedback
  scenarios** drawn by seeded RNG from the paired schedule (§14).
- Accept if child's mean §11 score > parent's on the minibatch (margin term makes
  ties rare and real).
- Exact tie with changed source: accept up to 2 consecutive neutral-drift accepts
  per lineage, then require strict improvement.
- Reject on new exceptions/engine failures the parent didn't have (`DieAction`
  EXCEPTION flags and runtime errors are visible in replays).

Pareto selection (arms C/D), per GEPA Algorithm 2 including the step v2 dropped:
1. Per instance, find max score over pool; collect tied-best candidates.
2. **Prune dominated candidates** from that union (candidate weakly worse
   everywhere than some other member and strictly worse somewhere).
3. Sample parent ∝ number of instances led.

Arms A/B: parent = argmax macro-average over the 48 instances.

Merge (arm D) as v2 §29, unchanged, plus: merged children get the same one
compile-repair budget, and merge attempts are logged in the funnel.

## 14. Paired design (common random numbers)

Arms share, at equal iteration index and optimizer seed: the optimizer RNG
streams, reflection/minibatch scenario schedules, component round-robin order
(economy → combat → defense → navigation → strategy), opponent snapshots, map
splits, and the final held-out grid. Effects of lucky scenarios/opponents/maps
cancel in per-seed paired differences instead of inflating variance.

## 15. Budgets

Pilot: arms A, B, C × 2 seeds; 20 model calls/run; ≤750 matches/run.
Main: 4 arms × 5 seeds; 80 model calls/run (repairs included); ≤2,500 new
matches/run (cache hits free). Hard stop at first limit.

Sequential rule: after the pilot, compute between-seed SD and the funnel (§16);
if projected accepted-candidates/run < ~15, fix throughput (gate, budget split)
before the main run rather than running a mechanically-degenerate comparison.
Decision rule if compute allows only one change: drop arm D and run A/B/C ×
8 seeds.

Cost outlook (Luna): ~1,700 calls × ≤300K input ≈ ≤0.5B tok ≈ ≤$110 input,
output ≤48M tok ≈ ≤$60. Simulation dominates: ~55–60K matches ≈ 500–1,900
CPU-hours; days on a big multicore box. Mitigations: shared exact match cache,
reuse parent replays for gate comparisons, screening cascade (2 games gate the
full 48-instance eval).

## 16. Pilot measurements (freeze before main run)

Replay/token analysis on 50 representative matches: raw/decoded/trace sizes,
rounds, units, → freeze N, W, budget, packing. Reflection groundedness: % cited
rounds that exist, % reflections consistent with trace events, compile rates,
gate pass rate, no-change rate. Funnel per run: proposals → parsed → compiled →
gate-passed → Pareto-entered; pool and frontier sizes per iteration. These are
analysis, never reward.

## 17. Evaluation and statistics

Champion per run: highest macro-average over the 48 Pareto instances. Never
selected on test data.

Held-out grid per champion: all test-pool opponents (≥6 target; 4 floor) × all
test maps × 2 sides, plus the dev-opponent × test-map and test-opponent ×
Pareto-map quadrants — target **300–500 games** per champion; every game a new
(opponent, map, side) cell (determinism makes repeats worthless). Opponent count
binds hardest; prefer adding unseen snapshots over maps.

Reporting (pre-registered): per-seed paired differences (B−A, C−B, D−C) with
bootstrap-over-seeds CIs; lineage and map-family as fixed effects (per-cluster
tables — no hierarchical bootstrap over 2–3 clusters); the design's measured MDE
alongside any point estimate; the §16 funnel so a null is interpretable
(distinguish "Pareto didn't help" from "pool too small for selection to differ").
Success language: estimation ("C−B = X pp, CI [·,·], MDE Y") rather than a
pass/fail 5pp gate, which n=5 cannot power. Sample-efficiency curves as v2 §41.

## 18. Patch restrictions, sandboxing, storage, logging

As v2 §§24, 30, 37–39, with: mutations are full-component rewrites (§8); the
static validator additionally rejects references to `IndicatorString` APIs,
reflection, threads, and I/O; candidates content-addressed by normalized source
hash; every model call logs the exact packed trace hashes and token counts.

## 19. Repository structure

```
gepa-battlecode/
├── PLAN.md
├── configs/            engine.lock.json  model.lock.json  experiment.yaml
│                       game_rules.md  maps.lock.json  opponents.lock.json
├── bots/original_seed/ bots/modular_seed/
├── replay/             schema_loader.py  decoder.py  trace.py  tokens.py  measure.py
├── harness/            runner.py  cache.py  validate.py
├── model/              client.py  prompts.py  schemas.py  repair.py
├── optimizer/          candidate.py  scoring… see package
├── evaluation/         scoring.py  statistics.py
├── scripts/            setup_engine.sh  smoke_test.py
├── tests/
└── runs/               (gitignored)
```

Deliberately absent, with an automated check: no diagnostics module, no LLM
summarizer, no judge. The only model-facing gameplay evidence is
`official replay → frozen deterministic transform`.

## 20. Build order

1. ✅ Pin engine + seed-bot commits; verify toolchain (Java 21, Python 3.11,
   flatbuffers bindings).
2. Replay decoder + tiered trace + token measurement (highest risk — everything
   downstream depends on its real numbers).
3. Match runner + exact cache; smoke test end-to-end.
4. Model client (Responses API, structured outputs) + prompts + repair.
5. Optimizer core: candidate store, scoring, gate, Pareto (+pruning), greedy,
   loop; unit tests on synthetic data.
6. Modular seed refactor + bytecode-aware equivalence check.
7. Opponent pipeline: clone, pin, compile, calibrate.
8. §16 pilot; freeze knobs; main run.
