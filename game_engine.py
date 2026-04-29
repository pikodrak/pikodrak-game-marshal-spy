"""
Maršál a Špión – Game Engine v1.0

Rules overhaul vs v0.8:
- Assassin removed; Hacker added (ground, attack 1, kills Terminator on melee)
- Old Mine replaced by "mine_field" (attack 0, destroys only engineer-incapable attackers)
- Attack drone: threshold-kill (kills if target.attack < 4) + temp-reveal on miss
- Fighter: no ranged, only air targets (wasted turn on non-air)
- Artillery: no standard attack, only ranged special (range 2, ground+special)
- Helicopter: movement 1, attacks ground/air/special
- Ranged/recon range = 1 + level (except trainer fixed 1, artillery fixed 2)
- Unit counts become per-player maxima (player chooses)
- Level zone special cap = 2 + level (was 5 + level)
- Corruptor: can't reduce below 1 (wasted action), does NOT reveal itself or change reveal
- Trainer boost capped 2x per target; can convert own ground (attack>3) to Hacker 2x/game
- Engineer conceal uses its own action (same as standard attack alternative)
- First player in battle = whoever confirmed placement first
- Cooldowns removed for special abilities (v1.0)

Serialization: bump "engine_version" so v0.8 saves are rejected.
"""

import json
import uuid
import time
from typing import Optional, List, Tuple, Dict, Set

ENGINE_VERSION = "1.0"

# ============================================================
# HEX GRID – odd-r offset, pointy-top
# ============================================================

EVEN_R_DIRS = [(-1, -1), (0, -1), (-1, 0), (1, 0), (-1, 1), (0, 1)]
ODD_R_DIRS  = [(0, -1), (1, -1), (-1, 0), (1, 0), (0, 1), (1, 1)]

NUM_ROWS   = 17
CENTER_COL = 8


def hex_neighbors(col: int, row: int) -> List[Tuple[int, int]]:
    dirs = ODD_R_DIRS if row % 2 == 1 else EVEN_R_DIRS
    return [(col + dc, row + dr) for dc, dr in dirs]


def offset_to_cube(col: int, row: int):
    x = col - (row - (row & 1)) // 2
    z = row
    y = -x - z
    return x, y, z


def hex_distance(c1: int, r1: int, c2: int, r2: int) -> int:
    x1, y1, z1 = offset_to_cube(c1, r1)
    x2, y2, z2 = offset_to_cube(c2, r2)
    return max(abs(x1 - x2), abs(y1 - y2), abs(z1 - z2))


def _battlefield_shrink(row: int) -> int:
    zt, _, _ = get_zone(row)
    return 1 if zt == "battlefield" else 0


def row_width(row: int) -> int:
    half = min(row, NUM_ROWS - 1 - row)
    shrink = _battlefield_shrink(row)
    if row % 2 == 1:
        return 2 * half + 2 - 2 * shrink
    return 2 * half + 1 - 2 * shrink


def row_start(row: int) -> int:
    half = min(row, NUM_ROWS - 1 - row)
    shrink = _battlefield_shrink(row)
    if row % 2 == 1:
        return CENTER_COL - half - 1 + shrink
    return CENTER_COL - half + shrink


def is_valid_hex(col: int, row: int) -> bool:
    if row < 0 or row >= NUM_ROWS:
        return False
    s = row_start(row)
    return s <= col < s + row_width(row)


def get_zone(row: int):
    if row == 0:
        return "citadel", 1, 0
    if row in (1, 2):
        return "level", 1, 0
    if row in (3, 4):
        return "level", 1, 1
    if row in (5, 6):
        return "level", 1, 2
    if 7 <= row <= 9:
        return "battlefield", 0, -1
    if row in (10, 11):
        return "level", 2, 2
    if row in (12, 13):
        return "level", 2, 1
    if row in (14, 15):
        return "level", 2, 0
    if row == 16:
        return "citadel", 2, 0
    return "unknown", 0, -1


MOUNTAINS = [(4, 7), (12, 7), (4, 9), (12, 9)]

CITADELS = {1: (8, 0), 2: (8, 16)}


def level_hexes(player: int) -> List[Tuple[int, int]]:
    """All level hexes belonging to `player`, excluding mountains (none in levels anyway)."""
    out = []
    for row in range(NUM_ROWS):
        zt, zp, _ = get_zone(row)
        if zp != player or zt != "level":
            continue
        s = row_start(row)
        for i in range(row_width(row)):
            c = s + i
            if (c, row) in MOUNTAINS:
                continue
            out.append((c, row))
    return out


def build_board() -> Dict:
    board = {}
    for row in range(NUM_ROWS):
        s = row_start(row)
        w = row_width(row)
        zt, zp, zl = get_zone(row)
        for i in range(w):
            col = s + i
            board[(col, row)] = {
                "col": col, "row": row,
                "zone_type": zt, "zone_player": zp, "zone_level": zl,
                "is_mountain": (col, row) in MOUNTAINS,
            }
    return board


# ============================================================
# UNIT DEFINITIONS (v1.0)
#
# Fields:
#   base_attack          – attack at L0, no corruption, no boost
#   movement             – max hexes per move action
#   category             – "ground" | "air" | "special"
#   is_special           – counts against per-level special cap
#   level_scales_attack  – if True, attack bonus = placement_level
#   base_range           – range for ranged specials/attacks; None if not applicable
#   range_scales_level   – if True, actual range = base_range + placement_level
#   max_count            – default cap per player (admin-overridable)
#   std_attack_targets   – categories this unit can target via standard attack;
#                          empty tuple = no standard attack
#   can_be_std_target    – False for mine_field (can still be attacked but with
#                          special resolution), True for everything else
#   corruptor_effect     – "attack" | "range" | "none" (floor at 1)
#   trainer_effect       – "attack" | "range" | "none"
#   admin_field_overrides– keys admin UI may edit (informational)
# ============================================================

UNIT_DEFS = {
    "engineer": {
        "base_attack": 1, "movement": 1, "category": "ground", "is_special": False,
        "level_scales_attack": False, "base_range": None, "range_scales_level": False,
        "max_count": 8, "name_cs": "Ženista", "abbr": "Že",
        "std_attack_targets": ("ground", "special"), "can_be_std_target": True,
        "corruptor_effect": "attack", "trainer_effect": "attack",
    },
    "scout": {
        "base_attack": 2, "movement": 3, "category": "ground", "is_special": False,
        "level_scales_attack": True, "base_range": None, "range_scales_level": False,
        "max_count": 6, "name_cs": "Průzkumník", "abbr": "Pr",
        "std_attack_targets": ("ground", "special"), "can_be_std_target": True,
        "corruptor_effect": "attack", "trainer_effect": "attack",
    },
    "private": {
        "base_attack": 3, "movement": 1, "category": "ground", "is_special": False,
        "level_scales_attack": True, "base_range": None, "range_scales_level": False,
        "max_count": 10, "name_cs": "Vojín", "abbr": "Vo",
        "std_attack_targets": ("ground", "special"), "can_be_std_target": True,
        "corruptor_effect": "attack", "trainer_effect": "attack",
    },
    "paratrooper": {
        "base_attack": 4, "movement": 2, "category": "ground", "is_special": False,
        "level_scales_attack": True, "base_range": None, "range_scales_level": False,
        "max_count": 5, "name_cs": "Výsadkář", "abbr": "Vý",
        "std_attack_targets": ("ground", "special"), "can_be_std_target": True,
        "corruptor_effect": "attack", "trainer_effect": "attack",
    },
    "tank": {
        "base_attack": 5, "movement": 2, "category": "ground", "is_special": False,
        "level_scales_attack": True, "base_range": None, "range_scales_level": False,
        "max_count": 4, "name_cs": "Tank", "abbr": "Ta",
        "std_attack_targets": ("ground", "special"), "can_be_std_target": True,
        "corruptor_effect": "attack", "trainer_effect": "attack",
    },
    "cyborg": {
        "base_attack": 8, "movement": 1, "category": "ground", "is_special": False,
        "level_scales_attack": True, "base_range": None, "range_scales_level": False,
        "max_count": 1, "name_cs": "Kyborg", "abbr": "Ky",
        "std_attack_targets": ("ground", "special"), "can_be_std_target": True,
        "corruptor_effect": "attack", "trainer_effect": "attack",
    },
    "hacker": {
        "base_attack": 1, "movement": 1, "category": "ground", "is_special": False,
        "level_scales_attack": False, "base_range": None, "range_scales_level": False,
        "max_count": 1, "name_cs": "Hacker", "abbr": "Ha",
        "std_attack_targets": ("ground", "special"), "can_be_std_target": True,
        "corruptor_effect": "attack", "trainer_effect": "attack",
    },
    "helicopter": {
        "base_attack": 3, "movement": 1, "category": "air", "is_special": False,
        "level_scales_attack": True, "base_range": None, "range_scales_level": False,
        "max_count": 4, "name_cs": "Vrtulník", "abbr": "Vr",
        "std_attack_targets": ("ground", "air", "special"), "can_be_std_target": True,
        "corruptor_effect": "attack", "trainer_effect": "none",
    },
    "fighter": {
        "base_attack": 4, "movement": 2, "category": "air", "is_special": False,
        "level_scales_attack": True, "base_range": None, "range_scales_level": False,
        "max_count": 4, "name_cs": "Stíhač", "abbr": "St",
        "std_attack_targets": ("air",), "can_be_std_target": True,
        "corruptor_effect": "attack", "trainer_effect": "none",
    },
    "mine_field": {
        "base_attack": 0, "movement": 0, "category": "ground", "is_special": False,
        "level_scales_attack": False, "base_range": None, "range_scales_level": False,
        "max_count": 8, "name_cs": "Minové pole", "abbr": "Mi",
        "std_attack_targets": (), "can_be_std_target": True,  # can be targeted; resolution special
        "corruptor_effect": "none", "trainer_effect": "none",
    },
    "recon_drone": {
        "base_attack": 1, "movement": 1, "category": "special", "is_special": True,
        "level_scales_attack": False, "base_range": 1, "range_scales_level": True,
        "max_count": 4, "name_cs": "Prů. dron", "abbr": "PD",
        "std_attack_targets": (), "can_be_std_target": True,
        "corruptor_effect": "range", "trainer_effect": "range",
    },
    "attack_drone": {
        "base_attack": 1, "movement": 1, "category": "special", "is_special": True,
        "level_scales_attack": False, "base_range": 1, "range_scales_level": True,
        "max_count": 4, "name_cs": "Út. dron", "abbr": "UD",
        "std_attack_targets": (), "can_be_std_target": True,
        "corruptor_effect": "range", "trainer_effect": "range",
    },
    "trainer": {
        "base_attack": 1, "movement": 1, "category": "special", "is_special": True,
        "level_scales_attack": False, "base_range": 1, "range_scales_level": False,
        "max_count": 2, "name_cs": "Trenér", "abbr": "Tr",
        "std_attack_targets": (), "can_be_std_target": True,
        "corruptor_effect": "range", "trainer_effect": "range",
    },
    "corruptor": {
        "base_attack": 1, "movement": 1, "category": "special", "is_special": True,
        "level_scales_attack": False, "base_range": 1, "range_scales_level": True,
        "max_count": 2, "name_cs": "Koruptor", "abbr": "Ko",
        "std_attack_targets": (), "can_be_std_target": True,
        "corruptor_effect": "range", "trainer_effect": "range",
    },
    "jammer": {
        "base_attack": 1, "movement": 1, "category": "special", "is_special": True,
        "level_scales_attack": False, "base_range": 1, "range_scales_level": True,
        "max_count": 2, "name_cs": "Rušička", "abbr": "Ru",
        "std_attack_targets": (), "can_be_std_target": True,
        "corruptor_effect": "range", "trainer_effect": "range",
    },
    "artillery": {
        "base_attack": 0, "movement": 0, "category": "special", "is_special": True,
        "level_scales_attack": False, "base_range": 2, "range_scales_level": False,
        "max_count": 2, "name_cs": "Děl. baterie", "abbr": "DB",
        "std_attack_targets": (), "can_be_std_target": True,
        "corruptor_effect": "none", "trainer_effect": "none",
    },
}


# Per-level special cap (mine_field is now ground, does NOT count against special cap)
def special_cap(level: int) -> int:
    return 2 + level


# Per-level per-type caps for specific units that are restricted "max 1 per level"
# (assassin removed; keeping cyborg restriction)
PER_LEVEL_UNIQUE = {"cyborg"}

# Hacker conversions per game per player (trainer ability)
HACKER_CONVERSIONS_PER_GAME = 2

# Trainer boost per target cap
TRAINER_BOOST_MAX_PER_UNIT = 2


# ============================================================
# UNIT
# ============================================================

class Unit:
    __slots__ = (
        "id", "type", "owner", "col", "row", "placed", "alive",
        "revealed",
        "base_attack", "level_bonus", "trainer_bonus",
        "corrupt_attack", "corrupt_range",
        "attack", "movement", "category", "is_special",
        "placement_level",   # level the unit was placed in (body color) - never changes
        "boost_count",       # times trainer has boosted THIS unit (cap 2)
        "converted_from",    # if not None: original type id was converted by trainer
        "temp_revealed_ticks_left",  # >0: revert reveal after N half-turns
        "was_hidden_before_temp",    # remember prior reveal state when temp-revealed
        "name_cs", "abbr",
    )

    def __init__(self, uid: str, utype: str, owner: int):
        d = UNIT_DEFS[utype]
        self.id = uid
        self.type = utype
        self.owner = owner
        self.col = -1
        self.row = -1
        self.placed = False
        self.alive = True
        self.revealed = False
        self.base_attack = d["base_attack"]
        self.level_bonus = 0
        self.trainer_bonus = 0
        self.corrupt_attack = 0
        self.corrupt_range = 0
        self.attack = d["base_attack"]
        self.movement = d["movement"]
        self.category = d["category"]
        self.is_special = d["is_special"]
        self.placement_level = -1
        self.boost_count = 0
        self.converted_from = None
        self.temp_revealed_ticks_left = 0
        self.was_hidden_before_temp = False
        self.name_cs = d["name_cs"]
        self.abbr = d["abbr"]

    def refresh_attack(self):
        """Recompute effective attack from components. Floors at 0 for display."""
        if self.type == "mine_field":
            self.attack = 0
        else:
            base = self.base_attack
            if UNIT_DEFS[self.type]["level_scales_attack"]:
                base = base + max(0, self.placement_level)
            self.attack = max(0, base + self.trainer_bonus - self.corrupt_attack)

    def effective_range(self) -> int:
        d = UNIT_DEFS[self.type]
        if d["base_range"] is None:
            return 0
        r = d["base_range"]
        if d["range_scales_level"]:
            r += max(0, self.placement_level)
        r = r - self.corrupt_range
        return max(1, r)

    def is_corrupted(self) -> bool:
        return self.corrupt_attack > 0 or self.corrupt_range > 0

    def retype_to(self, new_type: str):
        """In-place convert this unit to a new type (used for Hacker conversion).
        Preserves id, owner, position, placement_level (body color stays)."""
        d = UNIT_DEFS[new_type]
        self.converted_from = self.type
        self.type = new_type
        self.base_attack = d["base_attack"]
        self.movement = d["movement"]
        self.category = d["category"]
        self.is_special = d["is_special"]
        self.name_cs = d["name_cs"]
        self.abbr = d["abbr"]
        # Reset combat modifiers; keep placement_level for color
        self.trainer_bonus = 0
        self.corrupt_attack = 0
        self.corrupt_range = 0
        self.boost_count = 0
        self.refresh_attack()

    def to_dict(self) -> dict:
        return {s: getattr(self, s) for s in self.__slots__}

    @classmethod
    def from_dict(cls, d: dict) -> "Unit":
        u = object.__new__(cls)
        for s in cls.__slots__:
            setattr(u, s, d.get(s, 0 if s not in ("type", "id", "name_cs", "abbr", "converted_from") else None))
        # Fill defaults for older format safety
        if u.converted_from == 0:
            u.converted_from = None
        return u


# ============================================================
# GAME STATE
# ============================================================

class GameState:

    def __init__(self, game_id: Optional[str] = None, mode: str = "online"):
        self.engine_version = ENGINE_VERSION
        self.game_id = game_id or uuid.uuid4().hex[:8]
        self.phase = "placement"          # placement | battle | finished
        self.current_player = 1
        self.turn = 0
        self.ply = 0                      # half-turn counter (each action)
        self.winner: Optional[int] = None
        self.mode = mode                  # online | hotseat | ai | bot
        self.ai_player: Optional[int] = None
        self.placement_confirmed = {1: False, 2: False}
        self.placement_confirmed_at = {1: 0.0, 2: 0.0}  # unix ts first confirm
        self.first_confirmed: Optional[int] = None       # player who confirmed first
        self.log: List[dict] = []
        self.board = build_board()
        # Initially: no units on roster; players add via place_unit
        self.units: List[Unit] = []
        # Per-player max-count overrides (admin-editable); empty = use UNIT_DEFS
        self.unit_overrides: Dict[str, Dict] = {}
        # Hacker-conversion counter per player (via trainer)
        self.hacker_conversions = {1: 0, 2: 0}
        # Action / state timestamps for idle timeout
        self.last_action_ts = time.time()
        self.speedup_requested_by: Optional[int] = None
        self.speedup_deadline_ts: Optional[float] = None
        # Replay log (raw action records) + snapshot at start of battle
        self.replay_actions: List[dict] = []
        self.battle_start_state: Optional[dict] = None

    # ---------- unit pool (player's reservoir) ----------

    def _next_uid(self, player: int, utype: str) -> str:
        # Unique suffix
        n = 0
        while True:
            uid = f"{player}_{utype}_{n}"
            if not self.unit(uid):
                return uid
            n += 1

    def max_count(self, utype: str) -> int:
        ov = self.unit_overrides.get(utype, {})
        return ov.get("max_count", UNIT_DEFS[utype]["max_count"])

    def count_placed_by_type(self, player: int, utype: str) -> int:
        return sum(1 for u in self.units
                   if u.owner == player and u.type == utype and u.alive and u.placed)

    def count_deployed_by_type(self, player: int, utype: str) -> int:
        """Counts units of this type for this player (placed or not) that are alive."""
        return sum(1 for u in self.units
                   if u.owner == player and u.type == utype and u.alive)

    # ---------- helpers ----------

    def _log(self, msg: str, **kw):
        entry = {"turn": self.turn, "ply": self.ply, "msg": msg}
        entry.update(kw)
        self.log.append(entry)

    def _record(self, action: dict):
        """Record raw action for replay. Called by each public mutator on success."""
        action = {**action, "turn": self.turn, "ply": self.ply,
                  "current_player": self.current_player, "ts": time.time()}
        self.replay_actions.append(action)

    def unit(self, uid: str) -> Optional[Unit]:
        for u in self.units:
            if u.id == uid:
                return u
        return None

    def unit_at(self, col: int, row: int) -> Optional[Unit]:
        for u in self.units:
            if u.alive and u.placed and u.col == col and u.row == row:
                return u
        return None

    def _player_units(self, p: int, alive=True, placed=None) -> List[Unit]:
        out = [u for u in self.units if u.owner == p and (not alive or u.alive)]
        if placed is not None:
            out = [u for u in out if u.placed == placed]
        return out

    def _jammer_blocks_recon(self, target: Unit) -> bool:
        """Does a friendly (to target) jammer prevent recon_drone from revealing this?"""
        for j in self.units:
            if (j.alive and j.placed and j.type == "jammer"
                    and j.owner == target.owner
                    and hex_distance(j.col, j.row, target.col, target.row) <= j.effective_range()):
                return True
        return False

    def _reveal(self, u: Unit):
        """Unconditional reveal (combat). Jammer does NOT protect vs combat reveal in v1.0."""
        u.revealed = True
        u.temp_revealed_ticks_left = 0

    def _temp_reveal(self, u: Unit):
        """Mark unit as temporarily revealed for 2 half-turns (attack_drone miss)."""
        if not u.revealed:
            u.was_hidden_before_temp = True
            u.revealed = True
        u.temp_revealed_ticks_left = 2

    def _kill(self, u: Unit):
        u.alive = False
        u.placed = False

    # ============================================================
    # PLACEMENT PHASE
    # ============================================================

    def add_unit(self, player: int, utype: str) -> Optional[Unit]:
        """Create a new unit instance for this player if under max_count."""
        if utype not in UNIT_DEFS:
            return None
        if self.count_deployed_by_type(player, utype) >= self.max_count(utype):
            return None
        u = Unit(self._next_uid(player, utype), utype, player)
        self.units.append(u)
        return u

    def place_new_unit(self, player: int, utype: str, col: int, row: int) -> dict:
        """Create-and-place (or reuse a spare) a unit of the given type on hex."""
        if self.phase != "placement":
            return {"ok": False, "error": "not_placement_phase"}
        if self.placement_confirmed.get(player):
            return {"ok": False, "error": "already_confirmed"}
        if utype not in UNIT_DEFS:
            return {"ok": False, "error": "unknown_type"}

        # Validate hex + zone
        if not is_valid_hex(col, row):
            return {"ok": False, "error": "invalid_hex"}
        cell = self.board.get((col, row))
        if not cell or cell["is_mountain"]:
            return {"ok": False, "error": "invalid_hex"}
        if cell["zone_player"] != player or cell["zone_type"] != "level":
            return {"ok": False, "error": "not_your_zone"}
        if self.unit_at(col, row):
            return {"ok": False, "error": "hex_occupied"}

        level = cell["zone_level"]

        # Unit-specific placement rules
        if utype == "artillery" and level != 0:
            return {"ok": False, "error": "artillery_level0_only"}
        if utype in PER_LEVEL_UNIQUE:
            if any(x for x in self._player_units(player, placed=True)
                   if x.type == utype and x.placement_level == level):
                return {"ok": False, "error": f"one_{utype}_per_level"}

        # Special cap per level
        if UNIT_DEFS[utype]["is_special"]:
            specials_in_level = sum(1 for x in self._player_units(player, placed=True)
                                    if x.is_special and x.placement_level == level)
            if specials_in_level >= special_cap(level):
                return {"ok": False, "error": "special_cap_reached", "level": level}

        # Max count per type
        if self.count_deployed_by_type(player, utype) >= self.max_count(utype):
            return {"ok": False, "error": "max_count_reached"}

        # Instantiate and place
        u = self.add_unit(player, utype)
        if not u:
            return {"ok": False, "error": "cannot_create_unit"}
        u.col, u.row = col, row
        u.placed = True
        u.placement_level = level
        u.level_bonus = level if UNIT_DEFS[utype]["level_scales_attack"] else 0
        u.refresh_attack()

        self._record({"type": "place_new", "player": player, "unit_id": u.id,
                      "utype": utype, "col": col, "row": row})
        return {"ok": True, "unit_id": u.id}

    def unplace_unit(self, player: int, uid: str) -> dict:
        """Remove a placed unit from the board AND from the roster (restore max count)."""
        if self.phase != "placement":
            return {"ok": False, "error": "not_placement_phase"}
        if self.placement_confirmed.get(player):
            return {"ok": False, "error": "already_confirmed"}
        u = self.unit(uid)
        if not u or u.owner != player or not u.placed:
            return {"ok": False, "error": "invalid_unit"}
        # Remove from roster entirely (v1.0: unit pool is elastic)
        self.units = [x for x in self.units if x.id != uid]
        self._record({"type": "unplace", "player": player, "unit_id": uid})
        return {"ok": True}

    def clear_placement(self, player: int) -> dict:
        """Remove all placed (and unplaced) units of this player. Used before loading a preset."""
        if self.phase != "placement":
            return {"ok": False, "error": "not_placement_phase"}
        if self.placement_confirmed.get(player):
            return {"ok": False, "error": "already_confirmed"}
        self.units = [x for x in self.units if x.owner != player]
        self._record({"type": "clear_placement", "player": player})
        return {"ok": True}

    def apply_preset(self, player: int, preset: List[dict]) -> dict:
        """Clear player's board and apply a named preset (list of {utype, col, row}).

        Silently skips entries that violate rules. Returns list of errors.
        """
        if self.phase != "placement":
            return {"ok": False, "error": "not_placement_phase"}
        if self.placement_confirmed.get(player):
            return {"ok": False, "error": "already_confirmed"}
        self.clear_placement(player)
        errors = []
        for entry in preset:
            res = self.place_new_unit(player, entry["utype"],
                                       int(entry["col"]), int(entry["row"]))
            if not res["ok"]:
                errors.append({**entry, "error": res["error"]})
        return {"ok": True, "errors": errors}

    def all_hexes_filled(self, player: int) -> bool:
        target = set(level_hexes(player))
        placed = {(u.col, u.row) for u in self._player_units(player, placed=True)}
        return target.issubset(placed)

    def confirm_placement(self, player: int, force: bool = False) -> dict:
        if self.phase != "placement":
            return {"ok": False, "error": "not_placement_phase"}
        if self.placement_confirmed.get(player):
            return {"ok": False, "error": "already_confirmed"}
        if not force and not self.all_hexes_filled(player):
            return {"ok": False, "error": "hexes_not_filled"}

        self.placement_confirmed[player] = True
        self.placement_confirmed_at[player] = time.time()
        if self.first_confirmed is None:
            self.first_confirmed = player

        self._record({"type": "confirm_placement", "player": player, "force": force})

        if self.placement_confirmed[1] and self.placement_confirmed[2]:
            self._start_battle()
        return {"ok": True, "battle_started": self.phase == "battle",
                "first_confirmed": self.first_confirmed}

    def force_end_placement(self) -> dict:
        """Server calls this after the second 60s countdown expires."""
        if self.phase != "placement":
            return {"ok": False, "error": "not_placement_phase"}
        for p in (1, 2):
            if not self.placement_confirmed[p]:
                self.placement_confirmed[p] = True
                self.placement_confirmed_at[p] = time.time()
                if self.first_confirmed is None:
                    self.first_confirmed = p
        self._record({"type": "force_end_placement"})
        self._start_battle()
        return {"ok": True}

    def _start_battle(self):
        self.phase = "battle"
        # First player = whoever confirmed first
        self.current_player = self.first_confirmed or 1
        self.turn = 1
        self.ply = 1
        self.last_action_ts = time.time()
        # Snapshot for replay: everything except the action log itself
        snap = self.to_dict()
        snap["replay_actions"] = []
        self.battle_start_state = snap
        self._log("Boj začíná!", event="battle_start")

    # ============================================================
    # MOVEMENT
    # ============================================================

    def get_reachable(self, u: Unit) -> Set[Tuple[int, int]]:
        """Hexes this unit could legally stop on this turn.
        Fighter and trainer may path through own units; others cannot cross any occupied hex."""
        if u.movement == 0:
            return set()
        is_air = u.category == "air"
        can_path_through_allies = u.type in ("fighter", "trainer")
        visited = {(u.col, u.row): 0}
        frontier = [(u.col, u.row, 0)]
        reachable = set()
        while frontier:
            c, r, cost = frontier.pop(0)
            for nc, nr in hex_neighbors(c, r):
                if not is_valid_hex(nc, nr):
                    continue
                cell = self.board.get((nc, nr))
                if not cell:
                    continue
                if cell["is_mountain"] and not is_air:
                    continue
                new_cost = cost + 1
                if new_cost > u.movement:
                    continue
                occ = self.unit_at(nc, nr)
                if occ:
                    # Enemy blocks everyone
                    if occ.owner != u.owner:
                        continue
                    # Friendly: fighter and trainer may path through, but cannot stop on friendly
                    if can_path_through_allies:
                        if (nc, nr) not in visited or visited[(nc, nr)] > new_cost:
                            visited[(nc, nr)] = new_cost
                            frontier.append((nc, nr, new_cost))
                        continue
                    else:
                        continue
                if (nc, nr) not in visited or visited[(nc, nr)] > new_cost:
                    visited[(nc, nr)] = new_cost
                    frontier.append((nc, nr, new_cost))
                    reachable.add((nc, nr))
        return reachable

    def move_unit(self, player: int, uid: str, col: int, row: int) -> dict:
        if self.phase != "battle":
            return {"ok": False, "error": "not_battle_phase"}
        if self.current_player != player:
            return {"ok": False, "error": "not_your_turn"}
        u = self.unit(uid)
        if not u or u.owner != player or not u.alive or not u.placed:
            return {"ok": False, "error": "invalid_unit"}
        if u.movement == 0:
            return {"ok": False, "error": "unit_cannot_move"}

        reachable = self.get_reachable(u)
        if (col, row) not in reachable:
            return {"ok": False, "error": "hex_not_reachable"}

        result = {"ok": True, "events": []}

        # Step onto a mine_field hex? Only possible if mine is there (unit_at would block).
        # unit_at returns mine_field too, so occupied. Actually mines ARE placed, so this path
        # wouldn't be reachable. For ground walking adjacent, mines appear in reachable check
        # only if unit_at returned nothing — which won't happen since mine is placed.
        # So "stepping on mine" can only happen via movement into its hex if unit_at filters
        # out mines. In v1.0, mine_field BLOCKS movement like any unit (must attack it).
        # Ground units that want to clear it must standard-attack the hex (ground dies, mine stays)
        # or engineer attacks it (mine dies, engineer stays).
        # Therefore: no mine-trigger during movement in v1.0 — only via attack resolution.

        u.col, u.row = col, row

        # Check citadel capture (ground+special only; air cannot win)
        enemy = 3 - player
        if (col, row) == CITADELS[enemy] and u.category in ("ground", "special"):
            self.winner = player
            self.phase = "finished"
            result["events"].append({"type": "citadel_captured", "player": player})
            self._log(f"Hráč {player} dobyl citadelu!", player=player, event="win_citadel")

        self._record({"type": "move", "player": player, "unit_id": uid,
                      "to": [col, row]})
        self._end_turn()
        return result

    # ============================================================
    # COMBAT – STANDARD ATTACK
    # ============================================================

    def get_attack_targets(self, u: Unit) -> List[dict]:
        """Legal standard-attack targets for u (melee-only in v1.0).
        Ranged attacks belong to special actions (artillery) now."""
        if not UNIT_DEFS[u.type]["std_attack_targets"]:
            return []
        targets = []
        enemy = 3 - u.owner
        adj = set(hex_neighbors(u.col, u.row))
        allowed_cats = UNIT_DEFS[u.type]["std_attack_targets"]
        for e in self._player_units(enemy, placed=True):
            if (e.col, e.row) not in adj:
                continue
            if e.category not in allowed_cats:
                # Fighter: any non-air would be wasted. We still return it NOT as target
                # (UI may offer a wasted-turn warning). Engine rejects non-air attacks.
                continue
            targets.append({"unit_id": e.id, "col": e.col, "row": e.row,
                            "attack_type": "melee"})
        return targets

    def _resolve_mine_attack(self, att: Unit, tgt: Unit) -> list:
        events = []
        if att.type == "engineer":
            self._kill(tgt)
            self._reveal(att)
            events.append({"type": "mine_defused_by_attack", "attacker": att.id, "mine": tgt.id})
            self._log(f"{att.name_cs} zničil minové pole", player=att.owner, event="mine_defused")
        elif att.category == "ground":
            self._reveal(tgt)
            self._kill(att)
            events.append({"type": "mine_kills_ground", "attacker": att.id, "mine": tgt.id})
            self._log(f"{att.name_cs} zaútočil na minu a byl zničen", player=att.owner,
                      event="mine_kills")
        elif att.category == "air":
            self._reveal(att)
            self._reveal(tgt)
            events.append({"type": "mine_reveals_air", "attacker": att.id, "mine": tgt.id})
            self._log(f"{att.name_cs} byl odhalen minou", player=att.owner, event="mine_reveals_air")
        else:
            events.append({"type": "attack_noop"})
        return events

    def _resolve_hacker_cyborg(self, att: Unit, tgt: Unit) -> list:
        self._reveal(att)
        self._reveal(tgt)
        self._kill(tgt)
        att.col, att.row = tgt.col, tgt.row
        self._log(f"{att.name_cs} hacknul {tgt.name_cs}!", player=att.owner, event="hacker_kill")
        self._check_citadel_after_move(att)
        return [{"type": "hacker_kills_terminator", "attacker": att.id, "target": tgt.id}]

    def attack_unit(self, player: int, attacker_id: str, target_id: str) -> dict:
        if self.phase != "battle":
            return {"ok": False, "error": "not_battle_phase"}
        if self.current_player != player:
            return {"ok": False, "error": "not_your_turn"}
        att = self.unit(attacker_id)
        tgt = self.unit(target_id)
        if not att or att.owner != player or not att.alive or not att.placed:
            return {"ok": False, "error": "invalid_attacker"}
        if not tgt or tgt.owner == player or not tgt.alive or not tgt.placed:
            return {"ok": False, "error": "invalid_target"}

        if (tgt.col, tgt.row) not in set(hex_neighbors(att.col, att.row)):
            return {"ok": False, "error": "not_adjacent"}

        allowed = UNIT_DEFS[att.type]["std_attack_targets"]
        if not allowed:
            return {"ok": False, "error": "cannot_std_attack"}

        if tgt.category not in allowed:
            self._log(f"{att.name_cs}: zmařený tah (nemůže útočit na {tgt.name_cs})",
                      player=player, event="wasted_turn")
            self._record({"type": "attack_wasted", "player": player,
                          "attacker": attacker_id, "target": target_id})
            self._end_turn()
            return {"ok": True, "wasted": True,
                    "events": [{"type": "wasted_turn", "reason": "invalid_category"}]}

        if tgt.type == "mine_field":
            events = self._resolve_mine_attack(att, tgt)
        elif att.type == "hacker" and tgt.type == "cyborg":
            events = self._resolve_hacker_cyborg(att, tgt)
        else:
            events = self._resolve_melee(att, tgt)

        self._record({"type": "attack", "player": player,
                      "attacker": attacker_id, "target": target_id,
                      "events": events})
        self._check_elimination()
        if self.phase != "finished":
            self._end_turn()
        return {"ok": True, "events": events}

    def _resolve_melee(self, att: Unit, tgt: Unit) -> list:
        events = []
        # Both sides revealed by combat (jammer does NOT block combat reveal)
        self._reveal(att)
        self._reveal(tgt)

        ap, tp = att.attack, tgt.attack
        if ap > tp:
            self._kill(tgt)
            att.col, att.row = tgt.col, tgt.row
            events.append({"type": "attacker_wins", "attacker": att.id,
                           "target": tgt.id, "ap": ap, "tp": tp})
            self._log(f"{att.name_cs}({ap}) porazil {tgt.name_cs}({tp})",
                      player=att.owner, event="attacker_wins")
            self._check_citadel_after_move(att)
        elif ap == tp:
            self._kill(att)
            self._kill(tgt)
            events.append({"type": "both_die", "attacker": att.id, "target": tgt.id,
                           "ap": ap, "tp": tp})
            self._log(f"{att.name_cs}({ap}) a {tgt.name_cs}({tp}) se zničili navzájem",
                      player=att.owner, event="both_die")
        else:
            self._kill(att)
            events.append({"type": "defender_wins", "attacker": att.id,
                           "target": tgt.id, "ap": ap, "tp": tp})
            self._log(f"{att.name_cs}({ap}) padl před {tgt.name_cs}({tp})",
                      player=att.owner, event="defender_wins")
        return events

    def _check_citadel_after_move(self, u: Unit):
        if u.category not in ("ground", "special") or not u.alive:
            return
        enemy = 3 - u.owner
        if (u.col, u.row) == CITADELS[enemy]:
            self.winner = u.owner
            self.phase = "finished"
            self._log(f"Hráč {u.owner} dobyl citadelu!", player=u.owner,
                      event="win_citadel")

    def _check_elimination(self):
        # Check: if one side has no ground/special units capable of capturing citadel.
        # Actually spec says ground/special can win. So only ground+special needed.
        for p in (1, 2):
            capturers = [u for u in self.units
                         if u.owner == p and u.alive and u.category in ("ground", "special")
                         and u.movement > 0]
            if not capturers:
                self.winner = 3 - p
                self.phase = "finished"
                self._log(f"Hráč {p} nemá jednotky schopné dobýt citadelu – vítězí {3-p}",
                          player=3-p, event="win_elimination")
                break

    # ============================================================
    # SPECIAL ABILITIES
    # ============================================================

    def get_special_actions(self, u: Unit) -> List[dict]:
        """What special actions can this unit perform? (no cooldowns in v1.0)"""
        actions = []
        enemy = 3 - u.owner

        if u.type == "engineer":
            # Conceal own revealed adjacent ally
            for nc, nr in hex_neighbors(u.col, u.row):
                ally = self.unit_at(nc, nr)
                if ally and ally.owner == u.owner and ally.revealed and ally.id != u.id:
                    actions.append({"action": "conceal", "target_id": ally.id,
                                    "col": nc, "row": nr})
            # Note: destroying mine via standard attack is handled in attack_unit

        elif u.type == "recon_drone":
            rng = u.effective_range()
            for e in self._player_units(enemy, placed=True):
                if e.revealed:
                    continue
                if hex_distance(u.col, u.row, e.col, e.row) > rng:
                    continue
                if self._jammer_blocks_recon(e):
                    continue
                actions.append({"action": "reveal", "target_id": e.id,
                                "col": e.col, "row": e.row})

        elif u.type == "attack_drone":
            rng = u.effective_range()
            for e in self._player_units(enemy, placed=True):
                if e.type == "mine_field":
                    continue  # mine_field only killable by engineer
                if hex_distance(u.col, u.row, e.col, e.row) > rng:
                    continue
                actions.append({"action": "strike", "target_id": e.id,
                                "col": e.col, "row": e.row})

        elif u.type == "trainer":
            rng = u.effective_range()  # range 1, fixed
            # Boost: own non-mine, non-trainer adjacent ally with boost_count<2
            for ally in self._player_units(u.owner, placed=True):
                if ally.id == u.id:
                    continue
                if ally.type in ("mine_field", "trainer"):
                    continue
                if hex_distance(u.col, u.row, ally.col, ally.row) > rng:
                    continue
                effect = UNIT_DEFS[ally.type].get("trainer_effect", "none")
                if effect == "none":
                    continue
                if ally.boost_count >= TRAINER_BOOST_MAX_PER_UNIT:
                    continue
                actions.append({"action": "boost", "target_id": ally.id,
                                "col": ally.col, "row": ally.row})
            # Convert to Hacker: own ground with effective attack > 3
            if self.hacker_conversions[u.owner] < HACKER_CONVERSIONS_PER_GAME:
                for ally in self._player_units(u.owner, placed=True):
                    if ally.id == u.id:
                        continue
                    if ally.category != "ground":
                        continue
                    if ally.type in ("engineer", "hacker"):
                        continue
                    if ally.attack <= 3:
                        continue
                    if hex_distance(u.col, u.row, ally.col, ally.row) > rng:
                        continue
                    actions.append({"action": "convert_hacker", "target_id": ally.id,
                                    "col": ally.col, "row": ally.row})

        elif u.type == "corruptor":
            rng = u.effective_range()
            for e in self._player_units(enemy, placed=True):
                if e.type in ("mine_field", "artillery"):
                    continue
                effect = UNIT_DEFS[e.type].get("corruptor_effect", "none")
                if effect == "none":
                    continue
                if hex_distance(u.col, u.row, e.col, e.row) > rng:
                    continue
                # Check floor: don't offer if would hit min
                if effect == "attack":
                    if e.attack <= 1:
                        continue
                elif effect == "range":
                    if e.effective_range() <= 1:
                        continue
                actions.append({"action": "weaken", "target_id": e.id,
                                "col": e.col, "row": e.row})

        elif u.type == "artillery":
            rng = u.effective_range()  # fixed 2
            for e in self._player_units(enemy, placed=True):
                if e.type == "mine_field":
                    continue
                if e.category not in ("ground", "special"):
                    continue
                if hex_distance(u.col, u.row, e.col, e.row) > rng:
                    continue
                actions.append({"action": "artillery_fire", "target_id": e.id,
                                "col": e.col, "row": e.row})

        # Jammer has no active action (passive only)
        return actions

    def do_special(self, player: int, uid: str, action: str, target_id: str) -> dict:
        if self.phase != "battle":
            return {"ok": False, "error": "not_battle_phase"}
        if self.current_player != player:
            return {"ok": False, "error": "not_your_turn"}
        u = self.unit(uid)
        if not u or u.owner != player or not u.alive or not u.placed:
            return {"ok": False, "error": "invalid_unit"}

        avail = self.get_special_actions(u)
        match = [a for a in avail if a["action"] == action and a["target_id"] == target_id]
        if not match:
            return {"ok": False, "error": "invalid_special_action"}

        tgt = self.unit(target_id)
        if not tgt or not tgt.alive:
            return {"ok": False, "error": "invalid_target"}

        result = {"ok": True, "events": []}

        if action == "conceal":
            tgt.revealed = False
            tgt.temp_revealed_ticks_left = 0
            result["events"].append({"type": "concealed", "unit": tgt.id})
            self._log(f"{u.name_cs} zahalil {tgt.name_cs}", player=player,
                      event="conceal")

        elif action == "reveal":
            # Recon drone
            self._reveal(tgt)
            result["events"].append({"type": "revealed", "unit": tgt.id,
                                     "unit_type": tgt.type, "attack": tgt.attack})
            self._log(f"{u.name_cs} odhalil {tgt.name_cs}({tgt.attack})",
                      player=player, event="reveal")

        elif action == "strike":
            # Attack drone: threshold-kill
            if tgt.attack < 4:
                self._reveal(u)  # Drone reveals itself? Spec doesn't explicitly say for kill.
                self._reveal(tgt)
                self._kill(tgt)
                result["events"].append({"type": "drone_kill",
                                         "attacker": u.id, "target": tgt.id})
                self._log(f"{u.name_cs} zničil {tgt.name_cs} ({tgt.attack}<4)",
                          player=player, event="drone_kill")
            else:
                # Miss: drone gets revealed permanently; target temp-revealed 1 opp turn
                self._reveal(u)
                self._temp_reveal(tgt)
                result["events"].append({"type": "drone_miss",
                                         "attacker": u.id, "target": tgt.id,
                                         "target_attack": tgt.attack})
                self._log(f"{u.name_cs} selhal na {tgt.name_cs}({tgt.attack}≥4)",
                          player=player, event="drone_miss")

        elif action == "boost":
            tgt.trainer_bonus += 1
            tgt.boost_count += 1
            tgt.refresh_attack()
            result["events"].append({"type": "boosted", "unit": tgt.id,
                                     "new_attack": tgt.attack})
            self._log(f"{u.name_cs} boostnul {tgt.name_cs} → {tgt.attack}",
                      player=player, event="boost", hidden_to_opponent=True)

        elif action == "convert_hacker":
            # Retype target in place; resets combat modifiers. Counts a conversion.
            old_type = tgt.type
            tgt.retype_to("hacker")
            self.hacker_conversions[u.owner] += 1
            result["events"].append({"type": "converted_to_hacker",
                                     "unit": tgt.id, "from": old_type})
            self._log(f"{u.name_cs} přeměnil {old_type} na Hackera",
                      player=player, event="convert_hacker",
                      hidden_to_opponent=True)

        elif action == "weaken":
            effect = UNIT_DEFS[tgt.type].get("corruptor_effect", "none")
            if effect == "attack":
                if tgt.attack <= 1:
                    result["events"].append({"type": "weaken_wasted", "unit": tgt.id})
                    self._log(f"{u.name_cs}: zmařený tah – {tgt.name_cs} na minimu síly",
                              player=player, event="weaken_wasted")
                else:
                    tgt.corrupt_attack += 1
                    tgt.refresh_attack()
                    result["events"].append({"type": "weakened_attack",
                                             "unit": tgt.id, "new_attack": tgt.attack})
                    self._log(f"{u.name_cs} oslabil jednotku → síla {tgt.attack}",
                              player=player, event="weaken_attack",
                              hidden_attacker=True)
            elif effect == "range":
                if tgt.effective_range() <= 1:
                    result["events"].append({"type": "weaken_wasted", "unit": tgt.id})
                    self._log(f"{u.name_cs}: zmařený tah – {tgt.name_cs} na minimu dosahu",
                              player=player, event="weaken_wasted")
                else:
                    tgt.corrupt_range += 1
                    result["events"].append({"type": "weakened_range",
                                             "unit": tgt.id,
                                             "new_range": tgt.effective_range()})
                    self._log(f"{u.name_cs} snížil dosah jednotky → {tgt.effective_range()}",
                              player=player, event="weaken_range",
                              hidden_attacker=True)
            else:
                return {"ok": False, "error": "cannot_weaken"}
            # Corruptor does NOT reveal itself, does NOT change target's reveal state
            # (explicit spec requirement)

        elif action == "artillery_fire":
            # Pure kill regardless of attack comparison (spec: "ničit všechny pozemní a speciální")
            self._reveal(u)
            self._reveal(tgt)
            self._kill(tgt)
            result["events"].append({"type": "artillery_kill",
                                     "attacker": u.id, "target": tgt.id})
            self._log(f"{u.name_cs} zničil {tgt.name_cs} palbou",
                      player=player, event="artillery_fire")

        self._record({"type": "special", "player": player, "unit_id": uid,
                      "action": action, "target_id": target_id,
                      "events": result["events"]})
        self._check_elimination()
        if self.phase != "finished":
            self._end_turn()
        return result

    # ============================================================
    # TURN MANAGEMENT
    # ============================================================

    def pass_turn(self, player: int) -> dict:
        if self.phase != "battle":
            return {"ok": False, "error": "not_battle_phase"}
        if self.current_player != player:
            return {"ok": False, "error": "not_your_turn"}
        self._log(f"Hráč {player} pasuje", player=player, event="pass")
        self._record({"type": "pass", "player": player})
        self._end_turn()
        return {"ok": True}

    def request_speedup(self, player: int) -> dict:
        """Opponent of current player requests speedup after 2 min of inactivity."""
        if self.phase != "battle":
            return {"ok": False, "error": "not_battle_phase"}
        if self.current_player == player:
            return {"ok": False, "error": "you_are_on_turn"}
        if self.speedup_requested_by is not None:
            return {"ok": False, "error": "already_requested"}
        if time.time() - self.last_action_ts < 120:
            return {"ok": False, "error": "too_early"}
        self.speedup_requested_by = player
        self.speedup_deadline_ts = time.time() + 60
        self._log(f"Zrychlení hry požádáno hráčem {player}",
                  event="speedup_requested")
        return {"ok": True, "deadline": self.speedup_deadline_ts}

    def check_speedup_expired(self) -> dict:
        """Server calls periodically; if deadline passed, force-pass current player."""
        if (self.speedup_deadline_ts is None
                or time.time() < self.speedup_deadline_ts
                or self.phase != "battle"):
            return {"ok": True, "expired": False}
        victim = self.current_player
        self._log(f"Hráč {victim} nestihl tah – promarněn",
                  player=victim, event="turn_wasted_timeout")
        self._record({"type": "turn_timeout", "player": victim})
        self._end_turn()
        return {"ok": True, "expired": True, "victim": victim}

    def _end_turn(self):
        if self.phase == "finished":
            return
        # Decrement temp-reveals (attack_drone miss aftermath)
        for u in self.units:
            if u.alive and u.temp_revealed_ticks_left > 0:
                u.temp_revealed_ticks_left -= 1
                if u.temp_revealed_ticks_left == 0 and u.was_hidden_before_temp:
                    u.revealed = False
                    u.was_hidden_before_temp = False

        self.ply += 1
        self.current_player = 3 - self.current_player
        if self.current_player == (self.first_confirmed or 1):
            self.turn += 1
        self.last_action_ts = time.time()
        self.speedup_requested_by = None
        self.speedup_deadline_ts = None

    # ============================================================
    # PLAYER VIEW (fog of war)
    # ============================================================

    def get_player_view(self, player: int) -> dict:
        my_units = []
        enemy_units = []
        dead_own = []

        for u in self.units:
            if u.owner == player:
                d = u.to_dict()
                d["effective_range"] = u.effective_range()
                d["is_corrupted"] = u.is_corrupted()
                if not u.alive:
                    dead_own.append(d)
                elif u.placed:
                    my_units.append(d)
            elif u.alive and u.placed:
                if u.revealed:
                    d = u.to_dict()
                    d["effective_range"] = u.effective_range()
                    d["is_corrupted"] = u.is_corrupted()
                    enemy_units.append(d)
                else:
                    # Hidden stub
                    enemy_units.append({
                        "id": u.id, "owner": u.owner,
                        "col": u.col, "row": u.row,
                        "revealed": False,
                        "type": "unknown", "attack": "?",
                        "category": "unknown", "name_cs": "???",
                        "abbr": "??", "placed": True, "alive": True,
                        "is_special": False,
                        "placement_level": u.placement_level,  # body color still visible
                        "is_corrupted": False,
                        "effective_range": 0,
                    })

        board_list = [cell for _, cell in sorted(self.board.items())]

        return {
            "engine_version": self.engine_version,
            "game_id": self.game_id,
            "phase": self.phase,
            "current_player": self.current_player,
            "turn": self.turn,
            "ply": self.ply,
            "winner": self.winner,
            "mode": self.mode,
            "my_player": player,
            "my_units": my_units,
            "enemy_units": enemy_units,
            "dead_own": dead_own,
            "board": board_list,
            "mountains": MOUNTAINS,
            "citadels": CITADELS,
            "level_hexes": {1: level_hexes(1), 2: level_hexes(2)},
            "log": self.log[-30:],
            "placement_confirmed": self.placement_confirmed,
            "first_confirmed": self.first_confirmed,
            "hacker_conversions": self.hacker_conversions,
            "last_action_ts": self.last_action_ts,
            "speedup_requested_by": self.speedup_requested_by,
            "speedup_deadline_ts": self.speedup_deadline_ts,
        }

    # ============================================================
    # SERIALIZATION
    # ============================================================

    def to_dict(self) -> dict:
        return {
            "engine_version": self.engine_version,
            "game_id": self.game_id,
            "phase": self.phase,
            "current_player": self.current_player,
            "turn": self.turn,
            "ply": self.ply,
            "winner": self.winner,
            "mode": self.mode,
            "ai_player": self.ai_player,
            "placement_confirmed": {str(k): v for k, v in self.placement_confirmed.items()},
            "placement_confirmed_at": {str(k): v for k, v in self.placement_confirmed_at.items()},
            "first_confirmed": self.first_confirmed,
            "log": self.log,
            "units": [u.to_dict() for u in self.units],
            "unit_overrides": self.unit_overrides,
            "hacker_conversions": {str(k): v for k, v in self.hacker_conversions.items()},
            "last_action_ts": self.last_action_ts,
            "speedup_requested_by": self.speedup_requested_by,
            "speedup_deadline_ts": self.speedup_deadline_ts,
            "replay_actions": self.replay_actions,
            "battle_start_state": self.battle_start_state,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "GameState":
        if d.get("engine_version") != ENGINE_VERSION:
            raise ValueError(f"incompatible_engine_version: {d.get('engine_version')}")
        gs = object.__new__(cls)
        gs.engine_version = d["engine_version"]
        gs.game_id = d["game_id"]
        gs.phase = d["phase"]
        gs.current_player = d["current_player"]
        gs.turn = d["turn"]
        gs.ply = d.get("ply", 0)
        gs.winner = d["winner"]
        gs.mode = d.get("mode", "online")
        gs.ai_player = d.get("ai_player")
        gs.placement_confirmed = {int(k): v for k, v in d["placement_confirmed"].items()}
        gs.placement_confirmed_at = {int(k): v for k, v in d.get("placement_confirmed_at", {"1": 0, "2": 0}).items()}
        gs.first_confirmed = d.get("first_confirmed")
        gs.log = d["log"]
        gs.board = build_board()
        gs.units = [Unit.from_dict(u) for u in d["units"]]
        gs.unit_overrides = d.get("unit_overrides", {})
        gs.hacker_conversions = {int(k): v for k, v in d.get("hacker_conversions", {"1": 0, "2": 0}).items()}
        gs.last_action_ts = d.get("last_action_ts", time.time())
        gs.speedup_requested_by = d.get("speedup_requested_by")
        gs.speedup_deadline_ts = d.get("speedup_deadline_ts")
        gs.replay_actions = d.get("replay_actions", [])
        gs.battle_start_state = d.get("battle_start_state")
        return gs

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False)

    @classmethod
    def from_json(cls, s: str) -> "GameState":
        return cls.from_dict(json.loads(s))
