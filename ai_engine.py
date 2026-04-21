"""
AI Engine v1.0 – scoring-based AI for Maršál a Špión.

Rewritten for v1.0 ruleset:
- All hexes must be filled (placement strategy)
- New unit set (hacker added, assassin + old mine removed)
- New mechanics: attack drone threshold, artillery special, etc.

Strategy tiers (kept simple; can be extended later):
  Placement:
    - L0 (9 hexes): defenders – mostly engineers + 2 mine_fields + arty at L0
    - L1 (17 hexes): mid-field – privates, scouts, supports
    - L2 (25 hexes): strike force – tanks, paratroopers, helicopters, terminator
    - Specials distributed per-level cap (2+L)
  Battle:
    - Score each legal action for current player, pick highest
    - Ground rushers advance toward enemy citadel
    - Specials act when a clear win is available (drone<4, arty, corruptor, reveal)
    - Fighters only engage when air targets available
    - Fallback: pass turn
"""

import random
from typing import List, Dict, Tuple, Optional
from game_engine import (
    GameState, Unit, CITADELS,
    hex_distance, hex_neighbors, level_hexes,
)


def _low_defensive_reserve(gs, player: int) -> bool:
    """True if player has fewer than 2 privates/engineers within 3 hexes of own citadel.
    Used to prevent CPU from pushing ALL defenders forward and leaving citadel open."""
    my_cit = CITADELS[player]
    nearby = sum(1 for u in gs._player_units(player, placed=True)
                 if u.type in ("private", "engineer")
                 and hex_distance(u.col, u.row, my_cit[0], my_cit[1]) <= 3)
    return nearby < 2


def _surplus_defensive_reserve(gs, player: int) -> bool:
    """True if we have 4+ defenders sitting on citadel — they should move out.
    Fixes Game 4 paralysis: 6 engineers frozen at back row watching enemy approach."""
    my_cit = CITADELS[player]
    nearby = sum(1 for u in gs._player_units(player, placed=True)
                 if u.type in ("private", "engineer")
                 and hex_distance(u.col, u.row, my_cit[0], my_cit[1]) <= 3)
    return nearby >= 4


class AI:
    def __init__(self, player: int = 2):
        self.player = player
        self.enemy = 3 - player
        self.decision_log: List[dict] = []
        # PATCH (game 6): track last-acted turn per unit to prevent "stuck" units.
        # Games 4-6: helicopters and recon drone had 0 actions across 100+ turns.
        self.last_acted_turn: dict = {}   # unit_id -> gs.turn of last action

    def _idle_turns(self, gs: GameState, uid: str) -> int:
        """How many turns this unit has been idle. Units never used have idle=turn."""
        last = self.last_acted_turn.get(uid, 0)
        return gs.turn - last

    # ============================================================
    # PLACEMENT
    # ============================================================

    def do_placement(self, gs: GameState):
        """Fill all of this player's level hexes with a balanced composition."""
        if gs.phase != "placement":
            return
        # Start from scratch (idempotent if called multiple times)
        gs.clear_placement(self.player)

        hexes_by_level: Dict[int, List[Tuple[int, int]]] = {0: [], 1: [], 2: []}
        for h in level_hexes(self.player):
            hexes_by_level[gs.board[h]["zone_level"]].append(h)

        # P1 front rows are high numbers (closer to battlefield); P2 opposite
        front_sign = 1 if self.player == 1 else -1
        for lvl in (0, 1, 2):
            hexes_by_level[lvl].sort(key=lambda h: (front_sign * h[1], h[0]))

        # Composition plan: (utype, target_level, count)
        # Total must fit 9+17+25 = 51 hexes across L0/L1/L2
        # Specials per level: 2+L = 2/3/4 (max 9)
        plan = [
            # L0 – 9 hexes: 6 engineers + 1 arty + 2 mines
            ("engineer",    0, 6),
            ("artillery",   0, 1),
            ("mine_field",  0, 2),
            # L1 – 17 hexes: 10 privates + 4 scouts + 3 specials
            ("private",     1, 10),
            ("scout",       1, 4),
            ("recon_drone", 1, 1),
            ("jammer",      1, 1),
            ("mine_field",  1, 1),
            # L2 – 25 hexes: 5 para + 4 tanks + 1 term + 1 hacker + 4 heli + 4 fighters + 2 engineer + 4 specials
            ("paratrooper", 2, 5),
            ("tank",        2, 4),
            ("terminator",  2, 1),
            ("hacker",      2, 1),
            ("helicopter",  2, 4),
            ("fighter",     2, 4),
            ("engineer",    2, 2),
            ("attack_drone", 2, 1),
            ("trainer",     2, 1),
            ("corruptor",   2, 1),
            ("mine_field",  2, 1),
        ]

        occupied: Dict[int, int] = {0: 0, 1: 0, 2: 0}

        for utype, lvl, count in plan:
            placed = 0
            while placed < count and occupied[lvl] < len(hexes_by_level[lvl]):
                h = hexes_by_level[lvl][occupied[lvl]]
                if gs.unit_at(h[0], h[1]):
                    occupied[lvl] += 1
                    continue
                res = gs.place_new_unit(self.player, utype, h[0], h[1])
                if res["ok"]:
                    occupied[lvl] += 1
                    placed += 1
                else:
                    # Try next hex in this level
                    occupied[lvl] += 1

        # Fallback: any remaining hexes get an engineer or private (respecting caps)
        for lvl in (0, 1, 2):
            for h in hexes_by_level[lvl]:
                if gs.unit_at(h[0], h[1]):
                    continue
                for fallback_type in ("engineer", "private", "scout", "paratrooper", "tank"):
                    if gs.place_new_unit(self.player, fallback_type, h[0], h[1])["ok"]:
                        break

    # ============================================================
    # BATTLE
    # ============================================================

    def do_turn(self, gs: GameState) -> dict:
        if gs.phase != "battle" or gs.current_player != self.player:
            return {"action": "none"}

        turn_log = {
            "turn": gs.turn, "player": self.player,
            "options_evaluated": 0, "top_options": [], "chosen": None,
        }

        my_units = gs._player_units(self.player, placed=True)
        enemy_citadel = CITADELS[self.enemy]

        best_score = -999.0
        best_action: Optional[dict] = None
        all_options: List[dict] = []

        for u in my_units:
            # Moves
            if u.movement > 0:
                for (c, r) in gs.get_reachable(u):
                    s = self._score_move(gs, u, c, r, enemy_citadel)
                    opt = {"kind": "move", "unit_id": u.id, "utype": u.type,
                           "col": c, "row": r, "score": round(s, 2)}
                    all_options.append(opt)
                    if s > best_score:
                        best_score = s
                        best_action = {"kind": "move", "unit_id": u.id, "col": c, "row": r}

            # Standard attacks
            for t in gs.get_attack_targets(u):
                s = self._score_attack(gs, u, t)
                opt = {"kind": "attack", "unit_id": u.id, "utype": u.type,
                       "target_id": t["unit_id"], "score": round(s, 2)}
                all_options.append(opt)
                if s > best_score:
                    best_score = s
                    best_action = {"kind": "attack", "unit_id": u.id,
                                   "target_id": t["unit_id"]}

            # Specials
            for a in gs.get_special_actions(u):
                s = self._score_special(gs, u, a)
                opt = {"kind": "special", "unit_id": u.id, "utype": u.type,
                       "action": a["action"], "target_id": a["target_id"],
                       "score": round(s, 2)}
                all_options.append(opt)
                if s > best_score:
                    best_score = s
                    best_action = {"kind": "special", "unit_id": u.id,
                                   "action": a["action"],
                                   "target_id": a["target_id"]}

        turn_log["options_evaluated"] = len(all_options)
        all_options.sort(key=lambda x: x["score"], reverse=True)
        turn_log["top_options"] = all_options[:5]

        if best_action is None or best_score < -10:
            gs.pass_turn(self.player)
            turn_log["chosen"] = {"kind": "pass"}
            self.decision_log.append(turn_log)
            return {"action": "pass", "ai_log": turn_log}

        turn_log["chosen"] = {**best_action, "score": round(best_score, 2)}

        if best_action["kind"] == "move":
            gs.move_unit(self.player, best_action["unit_id"],
                         best_action["col"], best_action["row"])
            self.last_acted_turn[best_action["unit_id"]] = gs.turn
        elif best_action["kind"] == "attack":
            gs.attack_unit(self.player, best_action["unit_id"],
                           best_action["target_id"])
            self.last_acted_turn[best_action["unit_id"]] = gs.turn
        elif best_action["kind"] == "special":
            gs.do_special(self.player, best_action["unit_id"],
                          best_action["action"], best_action["target_id"])
            self.last_acted_turn[best_action["unit_id"]] = gs.turn

        self.decision_log.append(turn_log)
        return {**best_action, "ai_log": turn_log}

    # ---------- SCORING ----------

    def _score_move(self, gs: GameState, u: Unit, col: int, row: int,
                    enemy_citadel: Tuple[int, int]) -> float:
        score = 0.0
        cur_dist = hex_distance(u.col, u.row, enemy_citadel[0], enemy_citadel[1])
        new_dist = hex_distance(col, row, enemy_citadel[0], enemy_citadel[1])
        advance = cur_dist - new_dist

        # Late-game urgency: after turn 10, ground rushers get a big push
        late_game = max(0, gs.turn - 10) * 0.6  # grows with stalemate length
        # Big bonus for landing adjacent to an enemy unit (sets up attack next turn)
        adj_enemy_bonus = 0
        for nc, nr in hex_neighbors(col, row):
            occ = gs.unit_at(nc, nr)
            if occ and occ.owner == self.enemy:
                adj_enemy_bonus = 3
                break
        # Defense: strong pull-back when an enemy is near our citadel
        my_cit = CITADELS[self.player]
        nearest_enemy_to_cit = min(
            (hex_distance(e.col, e.row, my_cit[0], my_cit[1])
             for e in gs._player_units(self.enemy, placed=True)),
            default=99)
        # If enemy within 5 hexes of our citadel, bias movement TOWARD citadel
        defense_pull = 0
        if nearest_enemy_to_cit <= 5:
            dist_to_own_cit = hex_distance(col, row, my_cit[0], my_cit[1])
            # Reward being close to our citadel
            defense_pull = max(0, 5 - dist_to_own_cit) * 2.0

        if u.category == "ground":
            if u.type in ("terminator", "tank"):
                # Moderate Terminator bonus (too high → predictable rush into Hacker)
                term_push = 2 if u.type == "terminator" else 0
                score += advance * (12 + late_game) + u.attack * 0.6 + adj_enemy_bonus + term_push
                # Tank can also defend citadel
                if u.type == "tank":
                    score += defense_pull * 0.5
                # PATCH (game 1+2): avoid revealed stronger enemies within distance 2.
                # Game 2 showed adjacency check bypassed by attacking next turn from distance 2.
                for e in gs._player_units(self.enemy, placed=True):
                    if not e.revealed: continue
                    if e.attack <= u.attack + 1: continue
                    d = hex_distance(col, row, e.col, e.row)
                    if d <= 1:
                        score -= (e.attack - u.attack) * 6
                    elif d == 2:
                        score -= (e.attack - u.attack) * 2
            elif u.type in ("paratrooper", "scout"):
                score += advance * (8 + late_game) + adj_enemy_bonus
            elif u.type == "private":
                score += advance * (6 + late_game * 0.6) + adj_enemy_bonus
                score += defense_pull
                # PATCH (game 3+4): defensive reserve with SURPLUS RELEASE.
                # - If < 2 defenders near citadel: glue others there.
                # - If >= 4: release the excess to push forward (fixes G4 paralysis).
                dist_to_own = hex_distance(u.col, u.row, my_cit[0], my_cit[1])
                new_dist_to_own = hex_distance(col, row, my_cit[0], my_cit[1])
                if _low_defensive_reserve(gs, self.player):
                    if dist_to_own <= 4 and new_dist_to_own > dist_to_own:
                        score -= 20
                elif _surplus_defensive_reserve(gs, self.player):
                    # Reward moving AWAY from home
                    if dist_to_own <= 3 and new_dist_to_own > dist_to_own:
                        score += 8
            elif u.type == "engineer":
                score += advance * 2
                score += defense_pull * 0.8
                dist_to_own = hex_distance(u.col, u.row, my_cit[0], my_cit[1])
                new_dist_to_own = hex_distance(col, row, my_cit[0], my_cit[1])
                if _low_defensive_reserve(gs, self.player):
                    if dist_to_own <= 4 and new_dist_to_own > dist_to_own:
                        score -= 15
                elif _surplus_defensive_reserve(gs, self.player):
                    if dist_to_own <= 3 and new_dist_to_own > dist_to_own:
                        score += 5
                # PATCH (game 7): engineers had 0 movement all game and formed predictable
                # wall. Add idle-based movement bonus.
                idle = self._idle_turns(gs, u.id)
                if idle > 10 and new_dist_to_own > dist_to_own:
                    score += min(15, (idle - 10) * 1.5)
            elif u.type == "hacker":
                # Hunt terminator if revealed
                terms = [e for e in gs._player_units(self.enemy, placed=True)
                         if e.type == "terminator" and e.revealed]
                if terms:
                    d = min(hex_distance(col, row, t.col, t.row) for t in terms)
                    score += max(0, 8 - d) * 2
                score += advance * 3
            if (col, row) == enemy_citadel:
                score += 1000

        elif u.category == "air":
            # PATCH (game 6): air units were getting 0-1 actions per game — completely
            # drowned out by ground unit scores. Bump BASE weights 2-3x and add strong
            # idle bonus so stuck helis eventually win over cheaper ground moves.
            enemies = [e for e in gs._player_units(self.enemy, placed=True) if e.revealed]
            if enemies:
                d = min(hex_distance(col, row, e.col, e.row) for e in enemies)
                score += max(0, 4 - d) * 3  # was 1.5
            score += advance * 4  # was 1.5
            # Idle bonus: grows 2 per turn idle, capped at +30
            idle = self._idle_turns(gs, u.id)
            score += min(30, idle * 2)
            # Hunt enemy Recon Drone with air (AI sees types directly)
            enemy_recons = [e for e in gs._player_units(self.enemy, placed=True)
                            if e.type == "recon_drone"]
            if enemy_recons and u.type == "helicopter":
                d = min(hex_distance(col, row, e.col, e.row) for e in enemy_recons)
                score += max(0, 6 - d) * 3
            if u.type == "helicopter":
                my_cit = CITADELS[self.player]
                current_d_home = hex_distance(u.col, u.row, my_cit[0], my_cit[1])
                new_d_home = hex_distance(col, row, my_cit[0], my_cit[1])
                if current_d_home <= 4 and new_d_home > current_d_home:
                    score += 15  # big reward for leaving hangar
                center_dist = abs(col - 8)
                score += max(0, 4 - center_dist) * 0.8

        elif u.category == "special":
            if u.type == "recon_drone":
                # PATCH (game 6): recon was under-used. Bump weights and add idle bonus.
                unknown = [e for e in gs._player_units(self.enemy, placed=True)
                           if not e.revealed]
                if unknown:
                    d = min(hex_distance(col, row, e.col, e.row) for e in unknown)
                    score += max(0, 4 - d) * 3   # was 1.5
                score += advance * 2  # was 0.8
                center_dist = abs(col - 8)
                score += max(0, 4 - center_dist) * 2   # stronger center pull
                # Idle bonus — force recon to move eventually
                idle = self._idle_turns(gs, u.id)
                score += min(25, idle * 1.5)
            elif u.type == "trainer":
                allies = [a for a in gs._player_units(self.player, placed=True)
                          if a.category in ("ground", "air") and a.id != u.id]
                if allies:
                    d = min(hex_distance(col, row, a.col, a.row) for a in allies)
                    score += max(0, 3 - d) * 1.2
                score += advance * 0.5
            elif u.type == "corruptor":
                # PATCH (game 1+5): position near battlefield center column (col 7-9).
                # Game 5 showed corruptor stayed too far left/right to see central rushers.
                enemies = [e for e in gs._player_units(self.enemy, placed=True)]
                if enemies:
                    d = min(hex_distance(col, row, e.col, e.row) for e in enemies)
                    score += max(0, u.effective_range() + 2 - d) * 1.5
                score += advance * 1.2
                # Center-column pull so action range covers col 8 rushers
                center_dist = abs(col - 8)
                score += max(0, 3 - center_dist) * 2
            elif u.type == "jammer":
                allies = [a for a in gs._player_units(self.player, placed=True)
                          if not a.revealed and a.id != u.id]
                if allies:
                    d = min(hex_distance(col, row, a.col, a.row) for a in allies)
                    score += max(0, 3 - d) * 1.0
            elif u.type == "attack_drone":
                targets = [e for e in gs._player_units(self.enemy, placed=True)
                           if e.revealed and e.attack < 4]
                if targets:
                    d = min(hex_distance(col, row, e.col, e.row) for e in targets)
                    score += max(0, u.effective_range() + 1 - d) * 2

        score += random.uniform(-0.3, 0.3)
        return score

    def _score_attack(self, gs: GameState, u: Unit, tgt_info: dict) -> float:
        tgt = gs.unit(tgt_info["unit_id"])
        if not tgt:
            return -100
        my_cit = CITADELS[self.player]
        threat_to_cit_d = hex_distance(tgt.col, tgt.row, my_cit[0], my_cit[1])
        threat_to_cit = threat_to_cit_d <= 3
        # MUCH stronger urgency for anything actually near our citadel
        citadel_defense_bonus = max(0, 7 - threat_to_cit_d) * 3
        # PATCH (game 1+4): hunt enemy reconnaissance. Recon drone feeds enemy's kill chain.
        # Game 4 showed heli still didn't engage — bump extra hard, especially for heli.
        recon_bonus = 0
        if tgt.type == "recon_drone":
            recon_bonus = 30 if u.type == "helicopter" else 20

        if u.type == "hacker" and tgt.type == "terminator":
            return 50
        if u.type == "fighter" and tgt.category != "air":
            return -30
        if tgt.type == "mine_field":
            return 18 if u.type == "engineer" else -30

        # Late-game eagerness – encourages attrition when stalling
        late = max(0, gs.turn - 10) * 0.5

        if tgt.revealed:
            if u.attack > tgt.attack:
                # Balanced between under-attack (game 7) and over-attack (game 8)
                s = 25 + tgt.attack * 2 + late + citadel_defense_bonus + recon_bonus
                if threat_to_cit: s += 10
                return s
            elif u.attack == tgt.attack:
                return (12 if u.attack <= 3 else 4) + late * 0.4
            else:
                return -6 + late * 0.2
        # Unknown enemy
        base = 6 if u.attack >= 6 else (3 if u.attack >= 4 else -2)
        return base + late * 0.3

    def _score_special(self, gs: GameState, _u: Unit, action: dict) -> float:
        my_cit = CITADELS[self.player]
        act = action["action"]

        if act == "reveal":
            # PATCH (game 6): recon drone needs to reveal more. Base bumped across board.
            tgt = gs.unit(action["target_id"])
            if not tgt:
                return 0
            d = hex_distance(tgt.col, tgt.row, my_cit[0], my_cit[1])
            base = 25 if d <= 3 else (18 if d <= 6 else 12)  # was 18/10/5
            # Extra points if target is in central column (likely rusher)
            center_dist = abs(tgt.col - 8)
            if center_dist <= 2: base += 8
            return base

        if act == "strike":
            # Attack drone
            tgt = gs.unit(action["target_id"])
            if not tgt:
                return 0
            if tgt.revealed:
                return 25 if tgt.attack < 4 else -5  # miss = drone revealed
            return 8  # gamble

        if act == "boost":
            tgt = gs.unit(action["target_id"])
            if not tgt:
                return 0
            enemy_cit = CITADELS[self.enemy]
            d = hex_distance(tgt.col, tgt.row, enemy_cit[0], enemy_cit[1])
            base = 8 if d <= 4 else (4 if d <= 7 else 1)
            # Fade faster – AI ended game 1 with 22 specials vs 100 moves; re-balance
            base -= max(0, gs.turn - 12) * 0.3
            return base

        if act == "convert_hacker":
            # PATCH (game 2+7): only convert when Terminator revealed near OUR citadel.
            # Game 7: CPU wasted T1 conversion on nothing — we shouldn't gamble on T1.
            my_cit = CITADELS[self.player]
            terms = [e for e in gs._player_units(self.enemy, placed=True)
                     if e.type == "terminator" and e.revealed]
            for t in terms:
                d = hex_distance(t.col, t.row, my_cit[0], my_cit[1])
                if d <= 6:
                    return 22   # urgent — terminator near home
            if terms:
                return 10   # revealed but far — modest value
            return 0   # don't waste conversions blindly

        if act == "weaken":
            # PATCH (game 2): weaken bumped.
            # PATCH (game 3): diminishing returns on same target.
            # PATCH (game 4): bigger base vs strong targets + bonus for boosted enemies.
            tgt = gs.unit(action["target_id"])
            if not tgt:
                return 0
            d = hex_distance(tgt.col, tgt.row, my_cit[0], my_cit[1])
            # Higher base; much higher if enemy ATK >= 6
            if tgt.attack >= 6:
                base = 35 if d <= 3 else 30
            else:
                base = 25 if d <= 3 else 18
            base += max(0, tgt.attack - 3) * 2
            # Extra value: weakening a boosted enemy (boost_count>0 = trainer-enhanced)
            if getattr(tgt, "boost_count", 0) > 0:
                base += 8
            # Diminishing returns
            already_weakened = getattr(tgt, "corrupt_attack", 0)
            if already_weakened >= 2:
                base -= already_weakened * 10
            return base

        if act == "conceal":
            tgt = gs.unit(action["target_id"])
            if tgt and tgt.type in ("terminator", "tank", "hacker"):
                return 14
            return 6

        if act == "artillery_fire":
            tgt = gs.unit(action["target_id"])
            if not tgt:
                return 0
            return 22 + (tgt.attack * 0.5)

        return 0
