"""
Maršál a Špión – Game Engine
Diamond hex-grid strategy board game with fog of war.
"""

import json
import uuid
from typing import Optional, List, Tuple, Dict, Set

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
    """Battlefield rows (7-9) shrink by 1 hex on each side."""
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


# Zone: (zone_type, owner_player, level)
# zone_type: "citadel" | "level" | "battlefield"
# owner_player: 1 | 2 | 0(neutral)
# level: 0-2 or -1

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
# UNIT DEFINITIONS
# ============================================================

UNIT_DEFS = {
    # Ground
    "engineer":    {"base_attack": 1, "movement": 1, "category": "ground", "is_special": False, "count": 1,  "name_cs": "Ženista",       "abbr": "Že"},
    "scout":       {"base_attack": 2, "movement": 2, "category": "ground", "is_special": False, "count": 2,  "name_cs": "Průzkumník",    "abbr": "Pr"},
    "private":     {"base_attack": 3, "movement": 1, "category": "ground", "is_special": False, "count": 3,  "name_cs": "Vojín",         "abbr": "Vo"},
    "paratrooper": {"base_attack": 4, "movement": 1, "category": "ground", "is_special": False, "count": 2,  "name_cs": "Výsadkář",      "abbr": "Vý"},
    "tank":        {"base_attack": 5, "movement": 1, "category": "ground", "is_special": False, "count": 2,  "name_cs": "Tank",          "abbr": "Ta"},
    "terminator":  {"base_attack": 6, "movement": 1, "category": "ground", "is_special": False, "count": 1,  "name_cs": "Terminátor",    "abbr": "Te"},
    "assassin":    {"base_attack": 8, "movement": 1, "category": "ground", "is_special": False, "count": 1,  "name_cs": "Asasín",        "abbr": "As"},
    # Air
    "helicopter":  {"base_attack": 3, "movement": 2, "category": "air",    "is_special": False, "count": 2,  "name_cs": "Vrtulník",      "abbr": "Vr"},
    "fighter":     {"base_attack": 5, "movement": 4, "category": "air",    "is_special": False, "count": 1,  "name_cs": "Stíhač",        "abbr": "St"},
    # Special
    "recon_drone": {"base_attack": 1, "movement": 1, "category": "special","is_special": True,  "count": 1,  "name_cs": "Prů. dron",     "abbr": "PD"},
    "attack_drone":{"base_attack": 4, "movement": 1, "category": "special","is_special": True,  "count": 1,  "name_cs": "Út. dron",      "abbr": "UD"},
    "trainer":     {"base_attack": 1, "movement": 1, "category": "special","is_special": True,  "count": 1,  "name_cs": "Trenér",        "abbr": "Tr"},
    "corruptor":   {"base_attack": 1, "movement": 1, "category": "special","is_special": True,  "count": 1,  "name_cs": "Koruptor",      "abbr": "Ko"},
    "jammer":      {"base_attack": 1, "movement": 1, "category": "special","is_special": True,  "count": 1,  "name_cs": "Rušička",       "abbr": "Ru"},
    "mine":        {"base_attack": 99,"movement": 0, "category": "special","is_special": True,  "count": 2,  "name_cs": "Mina",          "abbr": "Mi"},
    "artillery":   {"base_attack": 6, "movement": 0, "category": "special","is_special": True,  "count": 1,  "name_cs": "Děl. baterie",  "abbr": "DB"},
}

ZONE_LIMITS = {
    0: {"special": 5},   # 5 + 0
    1: {"special": 6},   # 5 + 1
    2: {"special": 7},   # 5 + 2
}

RANGED_UNITS = {
    "attack_drone": {"targets": ("ground", "air", "special")},
    "artillery":    {"targets": ("ground", "special")},
    "fighter":      {"targets": ("air",)},
}

JAMMER_RANGE = {0: 1, 1: 2, 2: 3}
ABILITY_CD   = {0: 3, 1: 2, 2: 1}


def unit_range(u) -> int:
    """All ranged/recon abilities: range = 2 + placement_level."""
    return 2 + max(0, u.placement_level)


# ============================================================
# UNIT
# ============================================================

class Unit:
    __slots__ = (
        "id", "type", "owner", "col", "row", "placed", "alive",
        "revealed", "base_attack", "level_bonus", "trainer_bonus",
        "attack", "movement", "category", "is_special",
        "placement_level", "cooldown", "has_acted", "name_cs", "abbr",
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
        self.attack = d["base_attack"]
        self.movement = d["movement"]
        self.category = d["category"]
        self.is_special = d["is_special"]
        self.placement_level = -1
        self.cooldown = 0
        self.has_acted = False
        self.name_cs = d["name_cs"]
        self.abbr = d["abbr"]

    def refresh_attack(self):
        if self.type == "mine":
            self.attack = 99
        else:
            self.attack = max(0, self.base_attack + self.level_bonus + self.trainer_bonus)

    def to_dict(self) -> dict:
        return {s: getattr(self, s) for s in self.__slots__}

    @classmethod
    def from_dict(cls, d: dict) -> "Unit":
        u = object.__new__(cls)
        for s in cls.__slots__:
            setattr(u, s, d[s])
        return u


# ============================================================
# GAME STATE
# ============================================================

class GameState:

    def __init__(self, game_id: str = None, mode: str = "online"):
        self.game_id = game_id or uuid.uuid4().hex[:8]
        self.phase = "placement"          # placement | battle | finished
        self.current_player = 1
        self.turn = 0
        self.winner: Optional[int] = None
        self.mode = mode                  # online | hotseat | ai
        self.ai_player: Optional[int] = None
        self.placement_confirmed = {1: False, 2: False}
        self.log: List[dict] = []
        self.board = build_board()
        self.units: List[Unit] = []
        self._create_armies()

    # ---------- helpers ----------

    def _create_armies(self):
        for player in (1, 2):
            for utype, d in UNIT_DEFS.items():
                for i in range(d["count"]):
                    self.units.append(Unit(f"{player}_{utype}_{i}", utype, player))

    def _log(self, msg: str, **kw):
        entry = {"turn": self.turn, "msg": msg}
        entry.update(kw)
        self.log.append(entry)

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

    def _jammer_protects(self, u: Unit) -> bool:
        for j in self.units:
            if (j.alive and j.placed and j.type == "jammer"
                    and j.owner == u.owner
                    and hex_distance(j.col, j.row, u.col, u.row) <= JAMMER_RANGE.get(j.placement_level, 1)):
                return True
        return False

    def _reveal(self, u: Unit):
        if not self._jammer_protects(u):
            u.revealed = True

    def _kill(self, u: Unit):
        u.alive = False
        u.placed = False

    # ---------- PLACEMENT PHASE ----------

    def place_unit(self, player: int, uid: str, col: int, row: int) -> dict:
        if self.phase != "placement":
            return {"ok": False, "error": "not_placement_phase"}
        u = self.unit(uid)
        if not u or u.owner != player or not u.alive:
            return {"ok": False, "error": "invalid_unit"}
        if not is_valid_hex(col, row):
            return {"ok": False, "error": "invalid_hex"}
        cell = self.board.get((col, row))
        if not cell:
            return {"ok": False, "error": "invalid_hex"}
        if cell["is_mountain"]:
            return {"ok": False, "error": "mountain"}
        if self.unit_at(col, row):
            return {"ok": False, "error": "hex_occupied"}

        zt, zp, zl = cell["zone_type"], cell["zone_player"], cell["zone_level"]

        # Must place in own level zones only (not citadel, not battlefield)
        if zp != player:
            return {"ok": False, "error": "not_your_zone"}
        if zt != "level":
            return {"ok": False, "error": "can_only_place_in_levels"}

        effective_level = zl

        # Artillery can only go at level 0
        if u.type == "artillery" and effective_level != 0:
            return {"ok": False, "error": "artillery_level0_only"}

        # Check zone limits: special units max = 5 + level
        units_in_level = [x for x in self._player_units(player, placed=True)
                          if x.id != u.id and self._unit_effective_level(x, player) == effective_level]
        lim = ZONE_LIMITS[effective_level]
        specials_in_level = [x for x in units_in_level if x.is_special]
        if u.is_special and len(specials_in_level) >= lim["special"]:
            return {"ok": False, "error": "zone_special_full"}

        # Max 1 assassin / terminator per level
        for restricted in ("assassin", "terminator"):
            if u.type == restricted:
                if any(x.type == restricted for x in units_in_level):
                    return {"ok": False, "error": f"one_{restricted}_per_level"}

        # Unplace if already placed elsewhere
        if u.placed:
            u.placed = False
            u.col = -1
            u.row = -1
            u.level_bonus = 0
            u.refresh_attack()

        u.col = col
        u.row = row
        u.placed = True
        u.placement_level = effective_level
        u.level_bonus = effective_level
        u.refresh_attack()
        return {"ok": True}

    def _unit_effective_level(self, u: Unit, player: int) -> int:
        cell = self.board.get((u.col, u.row))
        if not cell:
            return 0
        if cell["zone_type"] == "citadel":
            return 0
        return cell["zone_level"]

    def unplace_unit(self, player: int, uid: str) -> dict:
        if self.phase != "placement":
            return {"ok": False, "error": "not_placement_phase"}
        u = self.unit(uid)
        if not u or u.owner != player or not u.placed:
            return {"ok": False, "error": "invalid_unit"}
        u.placed = False
        u.col = -1
        u.row = -1
        u.level_bonus = 0
        u.placement_level = -1
        u.refresh_attack()
        return {"ok": True}

    def confirm_placement(self, player: int) -> dict:
        if self.phase != "placement":
            return {"ok": False, "error": "not_placement_phase"}
        unplaced = self._player_units(player, placed=False)
        # All units must be placed
        if unplaced:
            return {"ok": False, "error": "unplaced_units", "count": len(unplaced)}
        self.placement_confirmed[player] = True
        if self.placement_confirmed[1] and self.placement_confirmed[2]:
            self._start_battle()
        return {"ok": True, "battle_started": self.phase == "battle"}

    def _start_battle(self):
        self.phase = "battle"
        self.current_player = 1
        self.turn = 1
        for u in self.units:
            u.has_acted = False
            u.cooldown = 0
        self._log("Battle begins!")

    # ---------- MOVEMENT ----------

    def get_reachable(self, u: Unit) -> Set[Tuple[int, int]]:
        if u.movement == 0:
            return set()
        is_air = u.category == "air"
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
                    # Can't move through enemy units
                    if occ.owner != u.owner:
                        continue
                    # Can't move through friendly units either (but could path through?)
                    # Actually allow pathing through friendly but can't stop there
                    if (nc, nr) not in visited or visited[(nc, nr)] > new_cost:
                        visited[(nc, nr)] = new_cost
                        frontier.append((nc, nr, new_cost))
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

        old_col, old_row = u.col, u.row
        u.col = col
        u.row = row

        result = {"ok": True, "events": []}

        # Check mine trigger
        mine = self._check_mine(u, col, row)
        if mine:
            result["events"].append({
                "type": "mine_triggered",
                "unit": u.id, "mine": mine.id,
                "col": col, "row": row,
            })
            self._reveal(u)
            self._reveal(mine)
            self._kill(u)
            self._kill(mine)
            self._log(f"{u.name_cs} stepped on {mine.name_cs}!", player=player)
        else:
            # Check citadel capture
            enemy = 3 - player
            if (col, row) == CITADELS[enemy] and u.category == "ground":
                self.winner = player
                self.phase = "finished"
                result["events"].append({"type": "citadel_captured", "player": player})
                self._log(f"Player {player} captured the citadel!", player=player)

        self._end_turn()
        return result

    def _check_mine(self, u: Unit, col: int, row: int) -> Optional[Unit]:
        if u.category == "air":
            return None
        for m in self.units:
            if (m.alive and m.placed and m.type == "mine"
                    and m.owner != u.owner
                    and m.col == col and m.row == row):
                return m
        return None

    # ---------- COMBAT ----------

    def get_attack_targets(self, u: Unit) -> List[dict]:
        targets = []
        enemy = 3 - u.owner

        # Ranged attacks (range = 2 + placement_level)
        if u.type in RANGED_UNITS:
            rd = RANGED_UNITS[u.type]
            rng = unit_range(u)
            for e in self._player_units(enemy, placed=True):
                if e.category not in rd["targets"]:
                    continue
                dist = hex_distance(u.col, u.row, e.col, e.row)
                if 1 <= dist <= rng:
                    targets.append({"unit_id": e.id, "col": e.col, "row": e.row,
                                    "attack_type": "ranged"})
            return targets

        # Melee attacks
        adj = set(hex_neighbors(u.col, u.row))
        for e in self._player_units(enemy, placed=True):
            if (e.col, e.row) not in adj:
                continue
            # Check what can attack what
            if u.category == "ground":
                if e.category in ("ground", "special"):
                    targets.append({"unit_id": e.id, "col": e.col, "row": e.row,
                                    "attack_type": "melee"})
            elif u.category == "air" and u.type == "helicopter":
                # Helicopter attacks ground and air
                if e.category in ("ground", "air", "special"):
                    targets.append({"unit_id": e.id, "col": e.col, "row": e.row,
                                    "attack_type": "melee_air"})
            elif u.category == "air" and u.type == "fighter":
                # Fighter melee only air
                if e.category == "air":
                    targets.append({"unit_id": e.id, "col": e.col, "row": e.row,
                                    "attack_type": "melee"})
            elif u.category == "special":
                # Special units with attack 1 can do melee on adjacent ground
                if e.category in ("ground", "special") and u.type not in ("mine", "artillery"):
                    targets.append({"unit_id": e.id, "col": e.col, "row": e.row,
                                    "attack_type": "melee"})
        return targets

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

        targets = self.get_attack_targets(att)
        valid = [t for t in targets if t["unit_id"] == target_id]
        if not valid:
            return {"ok": False, "error": "target_not_in_range"}

        at = valid[0]["attack_type"]
        result = {"ok": True, "events": [], "attack_type": at}

        # Reveal units (unless jammer)
        self._reveal(att)
        self._reveal(tgt)

        if at == "ranged":
            result["events"] = self._resolve_ranged(att, tgt)
        elif at == "melee_air":
            result["events"] = self._resolve_air_vs_ground(att, tgt)
        else:
            result["events"] = self._resolve_melee(att, tgt)

        # Check win conditions
        self._check_elimination()
        if self.phase != "finished":
            self._end_turn()
        return result

    def _resolve_melee(self, att: Unit, tgt: Unit) -> list:
        events = []
        att_power = att.attack
        tgt_power = tgt.attack

        # Assassin defending: dies to any ground unit
        if tgt.type == "assassin" and att.category == "ground":
            self._kill(tgt)
            # Attacker moves to target position
            att.col, att.row = tgt.col, tgt.row
            events.append({"type": "assassin_killed", "attacker": att.id, "target": tgt.id})
            self._log(f"{att.name_cs} killed {tgt.name_cs} (assassin vulnerability)", player=att.owner)
            self._check_citadel_after_move(att)
            return events

        # Assassin attacking: uses attack 8 normally
        # Mine handling: melee on a mine = both die
        if tgt.type == "mine":
            self._kill(att)
            self._kill(tgt)
            events.append({"type": "mine_detonated", "attacker": att.id, "mine": tgt.id})
            self._log(f"{att.name_cs} attacked {tgt.name_cs} – both destroyed!", player=att.owner)
            return events

        if att_power > tgt_power:
            self._kill(tgt)
            old_c, old_r = tgt.col, tgt.row
            att.col, att.row = old_c, old_r
            events.append({"type": "attacker_wins", "attacker": att.id, "target": tgt.id})
            self._log(f"{att.name_cs}({att_power}) defeated {tgt.name_cs}({tgt_power})", player=att.owner)
            self._check_citadel_after_move(att)
        elif att_power == tgt_power:
            self._kill(att)
            self._kill(tgt)
            events.append({"type": "both_die", "attacker": att.id, "target": tgt.id})
            self._log(f"{att.name_cs}({att_power}) and {tgt.name_cs}({tgt_power}) destroyed each other", player=att.owner)
        else:
            self._kill(att)
            events.append({"type": "defender_wins", "attacker": att.id, "target": tgt.id})
            self._log(f"{att.name_cs}({att_power}) fell to {tgt.name_cs}({tgt_power})", player=att.owner)
        return events

    def _resolve_air_vs_ground(self, heli: Unit, ground: Unit) -> list:
        events = []

        # Mine: helicopter ignores mines
        if ground.type == "mine":
            events.append({"type": "air_ignores_mine", "attacker": heli.id, "target": ground.id})
            return events

        if heli.attack >= ground.attack:
            self._kill(ground)
            events.append({"type": "air_kills_ground", "attacker": heli.id, "target": ground.id})
            self._log(f"{heli.name_cs}({heli.attack}) destroyed {ground.name_cs}({ground.attack}) from air", player=heli.owner)
        else:
            events.append({"type": "air_attack_fails", "attacker": heli.id, "target": ground.id})
            self._log(f"{heli.name_cs}({heli.attack}) failed to destroy {ground.name_cs}({ground.attack})", player=heli.owner)
        return events

    def _resolve_ranged(self, att: Unit, tgt: Unit) -> list:
        events = []
        if att.attack >= tgt.attack:
            self._kill(tgt)
            events.append({"type": "ranged_kill", "attacker": att.id, "target": tgt.id})
            self._log(f"{att.name_cs}({att.attack}) destroyed {tgt.name_cs}({tgt.attack}) at range", player=att.owner)
        else:
            events.append({"type": "ranged_fail", "attacker": att.id, "target": tgt.id})
            self._log(f"{att.name_cs}({att.attack}) ranged attack on {tgt.name_cs}({tgt.attack}) failed", player=att.owner)
        return events

    def _check_citadel_after_move(self, u: Unit):
        if u.category != "ground" or not u.alive:
            return
        enemy = 3 - u.owner
        if (u.col, u.row) == CITADELS[enemy]:
            self.winner = u.owner
            self.phase = "finished"
            self._log(f"Player {u.owner} captured the citadel!", player=u.owner)

    def _check_elimination(self):
        for p in (1, 2):
            ground = [u for u in self.units if u.owner == p and u.alive and u.category == "ground"]
            if not ground:
                self.winner = 3 - p
                self.phase = "finished"
                self._log(f"Player {p} has no ground units – Player {3-p} wins!", player=3-p)
                break

    # ---------- SPECIAL ABILITIES ----------

    def get_special_actions(self, u: Unit) -> List[dict]:
        if u.type not in ("engineer", "recon_drone", "trainer", "corruptor"):
            return []
        if u.cooldown > 0:
            return []
        actions = []
        enemy = 3 - u.owner

        if u.type == "engineer":
            # Conceal adjacent friendly revealed unit
            for nc, nr in hex_neighbors(u.col, u.row):
                ally = self.unit_at(nc, nr)
                if ally and ally.owner == u.owner and ally.revealed and ally.id != u.id:
                    actions.append({"action": "conceal", "target_id": ally.id,
                                    "col": nc, "row": nr})
            # Defuse adjacent mine
            for nc, nr in hex_neighbors(u.col, u.row):
                m = self.unit_at(nc, nr)
                if m and m.owner != u.owner and m.type == "mine":
                    actions.append({"action": "defuse", "target_id": m.id,
                                    "col": nc, "row": nr})
                # Also allow defusing hidden mines (engineer can detect)
            for m in self.units:
                if (m.alive and m.placed and m.type == "mine" and m.owner == enemy
                        and (m.col, m.row) in set(hex_neighbors(u.col, u.row))):
                    if not any(a["target_id"] == m.id for a in actions):
                        actions.append({"action": "defuse", "target_id": m.id,
                                        "col": m.col, "row": m.row})

        elif u.type == "recon_drone":
            rng = unit_range(u)
            for e in self._player_units(enemy, placed=True):
                if not e.revealed and hex_distance(u.col, u.row, e.col, e.row) <= rng:
                    if not self._jammer_protects(e):
                        actions.append({"action": "reveal", "target_id": e.id,
                                        "col": e.col, "row": e.row})

        elif u.type == "trainer":
            for nc, nr in hex_neighbors(u.col, u.row):
                ally = self.unit_at(nc, nr)
                if (ally and ally.owner == u.owner and ally.id != u.id
                        and ally.type != "mine"):
                    actions.append({"action": "boost", "target_id": ally.id,
                                    "col": nc, "row": nr})

        elif u.type == "corruptor":
            for nc, nr in hex_neighbors(u.col, u.row):
                e = self.unit_at(nc, nr)
                if e and e.owner != u.owner and e.type != "mine":
                    actions.append({"action": "weaken", "target_id": e.id,
                                    "col": nc, "row": nr})
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
        cd = ABILITY_CD.get(u.placement_level, 3)

        if action == "conceal":
            tgt.revealed = False
            u.cooldown = cd
            result["events"].append({"type": "concealed", "unit": tgt.id})
            self._log(f"{u.name_cs} concealed {tgt.name_cs}", player=player)

        elif action == "defuse":
            self._kill(tgt)
            u.cooldown = cd
            result["events"].append({"type": "mine_defused", "mine": tgt.id})
            self._log(f"{u.name_cs} defused a mine", player=player)

        elif action == "reveal":
            tgt.revealed = True
            u.cooldown = cd
            result["events"].append({"type": "revealed", "unit": tgt.id,
                                     "unit_type": tgt.type, "attack": tgt.attack})
            self._log(f"{u.name_cs} revealed {tgt.name_cs}({tgt.attack})", player=player)

        elif action == "boost":
            tgt.trainer_bonus += 1
            tgt.refresh_attack()
            u.cooldown = cd
            result["events"].append({"type": "boosted", "unit": tgt.id,
                                     "new_attack": tgt.attack})
            self._log(f"{u.name_cs} boosted {tgt.name_cs} → {tgt.attack}", player=player)

        elif action == "weaken":
            tgt.trainer_bonus -= 1
            tgt.refresh_attack()
            u.cooldown = cd
            self._reveal(tgt)
            result["events"].append({"type": "weakened", "unit": tgt.id,
                                     "new_attack": tgt.attack})
            self._log(f"{u.name_cs} weakened {tgt.name_cs} → {tgt.attack}", player=player)

        self._end_turn()
        return result

    # ---------- TURN MANAGEMENT ----------

    def pass_turn(self, player: int) -> dict:
        if self.phase != "battle":
            return {"ok": False, "error": "not_battle_phase"}
        if self.current_player != player:
            return {"ok": False, "error": "not_your_turn"}
        self._log(f"Player {player} passes", player=player)
        self._end_turn()
        return {"ok": True}

    def _end_turn(self):
        if self.phase == "finished":
            return
        self.current_player = 3 - self.current_player
        # When it comes back to player 1, increment turn & tick cooldowns
        if self.current_player == 1:
            self.turn += 1
            for u in self.units:
                if u.alive and u.cooldown > 0:
                    u.cooldown -= 1

    # ---------- FOG OF WAR / PLAYER VIEW ----------

    def get_player_view(self, player: int) -> dict:
        enemy = 3 - player
        my_units = []
        enemy_units = []
        unplaced = []
        dead_own = []

        for u in self.units:
            if u.owner == player:
                d = u.to_dict()
                if not u.alive:
                    dead_own.append(d)
                elif u.placed:
                    my_units.append(d)
                else:
                    unplaced.append(d)
            elif u.alive and u.placed:
                if u.revealed:
                    enemy_units.append(u.to_dict())
                else:
                    enemy_units.append({
                        "id": u.id, "owner": u.owner,
                        "col": u.col, "row": u.row,
                        "revealed": False,
                        "type": "unknown", "attack": "?",
                        "category": "unknown", "name_cs": "???",
                        "abbr": "??", "placed": True, "alive": True,
                        "is_special": False,
                    })

        board_list = []
        for (c, r), cell in sorted(self.board.items()):
            board_list.append(cell)

        return {
            "game_id": self.game_id,
            "phase": self.phase,
            "current_player": self.current_player,
            "turn": self.turn,
            "winner": self.winner,
            "mode": self.mode,
            "my_player": player,
            "my_units": my_units,
            "enemy_units": enemy_units,
            "unplaced_units": unplaced,
            "dead_own": dead_own,
            "board": board_list,
            "mountains": MOUNTAINS,
            "citadels": CITADELS,
            "log": self.log[-30:],
            "placement_confirmed": self.placement_confirmed,
        }

    # ---------- SERIALIZATION ----------

    def to_dict(self) -> dict:
        return {
            "game_id": self.game_id,
            "phase": self.phase,
            "current_player": self.current_player,
            "turn": self.turn,
            "winner": self.winner,
            "mode": self.mode,
            "ai_player": self.ai_player,
            "placement_confirmed": {str(k): v for k, v in self.placement_confirmed.items()},
            "log": self.log,
            "units": [u.to_dict() for u in self.units],
        }

    @classmethod
    def from_dict(cls, d: dict) -> "GameState":
        gs = object.__new__(cls)
        gs.game_id = d["game_id"]
        gs.phase = d["phase"]
        gs.current_player = d["current_player"]
        gs.turn = d["turn"]
        gs.winner = d["winner"]
        gs.mode = d.get("mode", "online")
        gs.ai_player = d.get("ai_player")
        gs.placement_confirmed = {int(k): v for k, v in d["placement_confirmed"].items()}
        gs.log = d["log"]
        gs.board = build_board()
        gs.units = [Unit.from_dict(u) for u in d["units"]]
        return gs

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False)

    @classmethod
    def from_json(cls, s: str) -> "GameState":
        return cls.from_dict(json.loads(s))
