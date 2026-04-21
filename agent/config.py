"""Tunable configuration for the built-in heuristic agent.

All strategic parameters live here. Tweak these values to change the agent's
behavior without touching player.py. Run the agent, observe behavior, tune,
repeat.
"""

CONFIG = {
    # ============================================================
    # DEFENSE
    # ============================================================

    # Any revealed enemy within this many hexes of MY citadel is treated as an
    # urgent threat. Higher = more paranoid defender, fewer rush resources.
    "defense_radius": 5,

    # Hidden enemies within this radius get recon-drone priority.
    "recon_radius": 6,

    # When an enemy's type is unknown but position is in the defense zone,
    # assume their attack is this value for "can I kill it?" checks.
    "unknown_threat_assumed_atk": 4,

    # Minimum blocker ATK used when moving a unit to intercept an unknown threat.
    # Prevents sending weak units to their death against unknown-but-probably-strong.
    "min_blocker_atk_vs_unknown": 4,

    # ============================================================
    # OFFENSE — attack filtering
    # ============================================================

    # Only attack if MY_ATK > ENEMY_ATK (strict) — prevents wasteful trades.
    # Set to True for safer play, False to also allow ties (attacker + defender both die).
    "require_strict_kill": True,

    # Prefer cheaper units for kills (save expensive units for citadel push).
    # Scoring: (10 - my_atk + margin * 0.5) — higher margin still wins, but
    # ties break toward lower my_atk.
    "prefer_cheap_killer": True,

    # ============================================================
    # TRAINER / CONVERTER
    # ============================================================

    # Priority order for trainer boosts — higher rank = boosted first.
    "boost_priority": {
        "terminator": 100,
        "tank": 90,
        "paratrooper": 70,
        "hacker": 60,
        "scout": 40,
        "private": 30,
    },

    # Max times to boost the same unit (engine-enforced cap is 2).
    "max_boost_per_unit": 2,

    # Max hacker conversions per game (engine cap is 2).
    "max_hacker_conversions": 2,

    # ============================================================
    # RUSHER / ADVANCE
    # ============================================================

    # Unit types that should push toward enemy citadel, in priority order.
    # Lower index = pushed first.
    "advance_priority_types": [
        "tank", "terminator", "paratrooper", "scout", "hacker",
        "private", "engineer",
    ],

    # Unit types that never advance (stay at their placement).
    "static_unit_types": ["mine_field", "jammer", "artillery"],

    # Air-only movement policy: advance helicopter, fighter hunts only air.
    "helicopter_advances": True,
    "fighter_hunts_air_only": True,

    # ============================================================
    # SPECIALS
    # ============================================================

    # Attack drone only "strikes" if target ATK < this value.
    "attack_drone_atk_threshold": 4,

    # Artillery range is fixed by engine (2). Kept here for documentation.
    "artillery_range": 2,

    # ============================================================
    # STALL DETECTION
    # ============================================================

    # If a unit has moved backward or stayed put this many consecutive turns,
    # deprioritize advancing it (force variety).
    "stall_threshold_turns": 3,

    # Stall penalty per turn the unit has been stalled (added negatively to
    # advance score).
    "stall_penalty_per_turn": 5,

    # ============================================================
    # SCORING WEIGHTS
    # ============================================================

    # Advance-delta weight — points per hex of progress.
    "advance_delta_weight": 10,

    # Type-rank penalty — lower-priority rushers get less score.
    "advance_type_rank_weight": 2,

    # New-distance penalty — units further from citadel get small bonus for
    # "still making progress" but we slightly penalize absolute distance.
    "advance_new_dist_weight": 0.5,

    # Block-target scoring: points per hex closer to threat.
    "block_progress_weight": 5,

    # Type priority for blockers (highest ATK-capable ground).
    "block_priority": {
        "terminator": 100,
        "tank": 90,
        "hacker": 85,
        "paratrooper": 70,
        "private": 50,
        "engineer": 40,
    },

    # ============================================================
    # LOGGING
    # ============================================================

    # Print a progress line every N turns.
    "progress_every": 10,

    # Max events kept in state file (prevents unbounded growth).
    "events_keep_last": 200,
}
