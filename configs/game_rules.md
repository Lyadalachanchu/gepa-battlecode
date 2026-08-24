# Battlecode 2026 — Rules Digest (frozen, model-facing)

This digest is a fixed, outcome-independent summary of the official specs. It is
the ONLY hand-written gameplay text the model ever sees; it never describes any
particular match.

## Units
- **BABY_RAT**: 1x1, 100 HP, 90-degree vision cone (r^2=20), bytecode limit 17500/turn.
- **RAT_KING**: 3x3, 600 HP, 360-degree vision (r^2=25), bytecode limit 20000/turn.
- **CAT**: 2x2, 4000 HP, 180-degree cone (r^2=17). Engine-controlled NPC (not
  playable): patrols map waypoints, scratches (20 dmg), pounces (r^2<=13), digs.
  A sacrificed rat can be fed to a cat to make it sleep 2 rounds.

## Economy (cheese)
- Each team starts with 2500 global cheese.
- Cheese spawns stochastically near map-defined cheese mines (20 at a time,
  p=0.01/round per mine, within a 9x9 area).
- Baby rats pick up raw cheese (1% cooldown penalty per unit carried) and must
  transfer it to a rat king to make it spendable ("global").
- Rat kings consume 2 cheese/round; if the team has none, the king loses 10 HP/round.
- Spawning a baby rat costs 10 + 10*floor(allies/4) cheese (cost grows with army size).
- Upgrading a 3x3 block of 9 baby rats into a new rat king costs 50 cheese.
  Max 5 kings; at most 2 created after round 1200.

## Terrain
- Walls are immutable. Dirt can be dug (costs 5 cheese, cooldown 25) into the
  team stash and placed for free from the stash.

## Traps
- Rat traps and cat traps. Cat trap: 10 cheese, 100 dmg + 2-turn stun, max
  10/team, invisible to the enemy team.

## Cooperation and backstab
- Every game STARTS COOPERATIVE: both rat teams vs the cats.
- The game flips PERMANENTLY to backstab state when either team bites an enemy
  rat, triggers an enemy trap, or ratnaps (picks up) an enemy rat.
- Rats can carry and throw other rats (including enemy rats — "ratnapping").

## Movement and actions
- Cooldown system: 10 cooldown units regenerate per turn; forward move costs 10,
  strafe 18, turning 10. A robot acts nearly every round when unencumbered.
- Communication: squeaks are audible within r^2=16 (location only); a shared
  team array exists in-game but its contents are NOT visible in replays.
- Exceeding the per-turn bytecode limit PAUSES the robot's code mid-execution;
  it resumes next turn. Thrown exceptions cost a 500-bytecode penalty.

## Winning
- A team that loses all its rat kings loses instantly.
- If all cats die while still cooperative, the game ends and points decide:
  points = 0.5*%catDamage + 0.3*%livingKings + 0.2*%cheeseTransferred.
- At round 2000 the game ends and points decide, with the backstab formula
  0.3*%catDamage + 0.5*%livingKings + 0.2*%cheeseTransferred if a backstab
  occurred. Tiebreakers in order: more points, more global cheese, more rats
  alive, then a coin flip.

## Maps
- 20x20 to 60x60. Each map defines walls, dirt, cheese, mines, cat waypoints,
  starting units, and symmetry (rotational/horizontal/vertical).
