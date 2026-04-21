# Game Rules – Marshal & Spy

## Objective
Capture the enemy **citadel** with a ground unit. First player to do so wins.

## Board
- Diamond-shaped hexagonal grid: 17 rows, 153 hexes
- Symmetric layout with zones for each player:
  ```
  Citadel (1 hex) → Level 0 (2 rows) → Level 1 (2 rows) → Level 2 (2 rows) → Battlefield (3 rows)
  ```
- 4 fixed mountains in the battlefield — block ground movement, air flies over
- Each player's territory mirrors the other

## Setup (Deployment Phase)
- Each player has **23 units** to deploy
- Units can only be placed in **Level 0, 1, or 2 zones** (not citadel, not battlefield)
- Placement is simultaneous — neither player sees the other's deployment
- **Level bonus**: units gain permanent attack bonus = level number (L0: +0, L1: +1, L2: +2)
- **Special unit limits per level**: max 5 + level number (L0: max 5, L1: max 6, L2: max 7)
- **Max 1 Assassin** per level
- **Max 1 Terminator** per level
- **Artillery** can only be placed at Level 0
- Once all units are placed, confirm to start battle

## Battle Phase
- Players alternate turns (Player 1 first)
- Each turn: exactly **one action** — Move, Attack, Special, or Pass

### Movement
| Unit | Hexes per turn |
|------|---------------|
| Most ground/special | 1 |
| Scout | 3 |
| Tank, Paratrooper | 2 |
| Fighter | 2 (may path through own units) |
| Helicopter | 1 |
| Mine field, Artillery | 0 (cannot move) |

- Ground units **cannot** cross mountains
- Air units **ignore** mountains
- Cannot move through enemy units
- Cannot stop on occupied hexes

### Combat

#### Melee (adjacent hexes)
- Compare attack values: **higher wins**
- Equal attack: **both die**
- Winner moves to the defeated unit's hex
- Both units are **revealed** (unless jammer protects)

#### Ranged (within range)
- Range = **2 + placement level**
- If attacker's attack ≥ target's attack: target destroyed
- If attacker's attack < target's attack: miss (target survives)
- Attacker always survives ranged attacks

#### Who can attack whom
| Attacker | Can target |
|----------|-----------|
| Ground | Ground, Special |
| Helicopter | Ground (safe), Air |
| Fighter | Air only (melee + ranged) |
| Attack Drone | Ground, Air, Special (ranged) |
| Artillery | Ground, Special (ranged) |

#### Special Combat Rules
- **Helicopter vs Ground**: helicopter attacks without risk. If attack ≥ target: kill. If less: miss, helicopter survives.
- **Assassin**: attack 8 — devastates in offense. But when **defending** (being attacked), dies to ANY ground unit regardless of attack values.
- **Mine**: when a ground unit moves onto a mine's hex, both are destroyed. Air units ignore mines. Engineer can defuse adjacent mines.

## Special Abilities
Each special ability costs your turn. Cooldown depends on placement level:
- Level 2: every turn
- Level 1: every 2 turns
- Level 0: every 3 turns

| Unit | Ability | Effect |
|------|---------|--------|
| Engineer | Conceal | Makes adjacent revealed ally hidden again |
| Engineer | Defuse | Destroys adjacent mine safely |
| Recon Drone | Reveal | Reveals enemy unit within range (unless jammer protects) |
| Trainer | Boost | Adjacent ally gets +1 attack (permanent) |
| Corruptor | Weaken | Adjacent enemy gets -1 attack (permanent) |
| Jammer | Passive | Nearby friendly units cannot be revealed (range = 1/2/3 by level) |

## Fog of War
- All units start **hidden** — opponent sees unit positions but not types
- Units are **revealed** during combat (both attacker and defender)
- **Jammer** prevents revelation within its range
- **Recon Drone** reveals enemies at range
- **Engineer** can conceal revealed allies
- Once revealed, a unit stays visible until concealed by engineer

## Victory Conditions
1. **Citadel Capture**: move a ground unit onto enemy citadel — immediate win
2. **Elimination**: if a player has no ground units left, they cannot capture citadel — opponent wins

## Strategy Tips
- **Level 2 placement** gives +2 attack bonus but limits how many specials you can bring
- **Assassin** (attack 8+2=10 at L2) kills anything on offense but dies to anything on defense — protect it
- **Jammer** near your attack force prevents enemy from scouting your units
- **Mines** on likely approach paths can stop rushes
- **Artillery** at Level 0 with range 2 covers your defensive zones
- **Recon → Attack** combo: scout with drone, then strike with known advantage
- **Rush strategy**: put combat units at Level 2 for max bonus, attack before opponent sets up
- **Turtle strategy**: mines, artillery, jammer at Level 0, strong defenders near citadel
