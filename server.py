"""
Maršál a Špión – FastAPI Server
"""

import os
import json
import time
from datetime import datetime
from typing import Optional
from fastapi import FastAPI, HTTPException, Request, Depends
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel
import uvicorn

from game_engine import (
    GameState, UNIT_DEFS, NUM_ROWS, MOUNTAINS, CITADELS,
    PER_LEVEL_UNIQUE, HACKER_CONVERSIONS_PER_GAME, TRAINER_BOOST_MAX_PER_UNIT, special_cap,
)
from ai_engine import AI
import auth

app = FastAPI(title="Maršál a Špión")

# In-memory game store (backed by SQLite for persistence)
GAMES: dict[str, GameState] = {}
# Map game_id -> {1: user_id, 2: user_id}  (user_id may be "AI" for built-in AI)
GAME_PLAYERS: dict[str, dict] = {}
# Map game_id -> {1: bot_id, 2: bot_id}  (only for mode='bot'; bot_id is int from bot_accounts)
BOT_PLAYERS: dict[str, dict] = {}
# Queue of bots waiting for a human or bot opponent: [(bot_id, game_id, queued_at)]
BOT_QUEUE: list = []


# ============================================================
# AUTH HELPERS
# ============================================================

def get_user(request: Request) -> dict:
    token = request.headers.get("Authorization", "").replace("Bearer ", "")
    if not token:
        token = request.query_params.get("token", "")
    if not token:
        raise HTTPException(401, "missing_token")
    payload = auth.verify_token(token)
    if not payload:
        raise HTTPException(401, "invalid_token")
    return payload


# ============================================================
# AUTH ENDPOINTS
# ============================================================

class AuthReq(BaseModel):
    username: str
    password: str


@app.post("/api/auth/register")
def api_register(req: AuthReq):
    return auth.register(req.username, req.password)


@app.post("/api/auth/login")
def api_login(req: AuthReq):
    return auth.login(req.username, req.password)


@app.get("/api/auth/me")
def api_me(user=Depends(get_user)):
    auth.ensure_stats(user["user_id"], user["username"])
    stats = auth.get_player_stats(user["user_id"])
    email = auth.get_email(user["user_id"])
    return {"ok": True, "user_id": user["user_id"], "username": user["username"],
            "stats": stats, "email": email}


class EmailReq(BaseModel):
    email: str


@app.post("/api/auth/email")
def api_update_email(req: EmailReq, user=Depends(get_user)):
    return auth.update_email(user["user_id"], req.email)


@app.get("/api/leaderboard")
def api_leaderboard():
    return {"ok": True, "leaderboard": auth.get_leaderboard(50)}


@app.get("/api/stats/{user_id}")
def api_player_stats(user_id: int):
    stats = auth.get_player_stats(user_id)
    if not stats:
        raise HTTPException(404, "user_not_found")
    return {"ok": True, "stats": stats}


class ChangePassReq(BaseModel):
    old_password: str
    new_password: str


@app.post("/api/auth/change_password")
def api_change_password(req: ChangePassReq, user=Depends(get_user)):
    return auth.change_password(user["user_id"], req.old_password, req.new_password)


class SaveGameReq(BaseModel):
    name: str


@app.post("/api/game/{game_id}/save")
def api_save_game(game_id: str, req: SaveGameReq, user=Depends(get_user)):
    gs = _load_game(game_id)
    if not gs:
        raise HTTPException(404, "game_not_found")
    auth.save_game(user["user_id"], req.name, game_id, gs.to_json())
    return {"ok": True}


@app.get("/api/saves")
def api_list_saves(user=Depends(get_user)):
    return {"ok": True, "saves": auth.get_saves(user["user_id"])}


@app.post("/api/saves/{save_id}/load")
def api_load_save(save_id: int, user=Depends(get_user)):
    data = auth.load_save(save_id, user["user_id"])
    if not data:
        raise HTTPException(404, "save_not_found")
    try:
        gs = GameState.from_json(data)
    except ValueError as e:
        if "incompatible_engine_version" in str(e):
            raise HTTPException(400, "save_incompatible_v1_engine")
        raise
    GAMES[gs.game_id] = gs
    GAME_PLAYERS[gs.game_id] = {1: user["user_id"]}
    if gs.ai_player:
        GAME_PLAYERS[gs.game_id][2] = "AI"
    _save_game(gs)
    return {"ok": True, "game_id": gs.game_id}


@app.delete("/api/saves/{save_id}")
def api_delete_save(save_id: int, user=Depends(get_user)):
    if auth.delete_save(save_id, user["user_id"]):
        return {"ok": True}
    raise HTTPException(404, "save_not_found")


# ============================================================
# GAME MANAGEMENT
# ============================================================

class NewGameReq(BaseModel):
    mode: str = "ai"  # online | hotseat | ai


class JoinGameReq(BaseModel):
    game_id: str


@app.post("/api/game/new")
def api_new_game(req: NewGameReq, user=Depends(get_user)):
    auth.ensure_stats(user["user_id"], user["username"])
    gs = GameState(mode=req.mode)
    GAMES[gs.game_id] = gs
    GAME_PLAYERS[gs.game_id] = {1: user["user_id"]}

    if req.mode == "ai":
        gs.ai_player = 2
        GAME_PLAYERS[gs.game_id][2] = "AI"
        ai = AI(player=2)
        ai.do_placement(gs)
        gs.confirm_placement(2)
    elif req.mode == "hotseat":
        GAME_PLAYERS[gs.game_id][2] = user["user_id"]

    _save_game(gs)
    return {"ok": True, "game_id": gs.game_id, "player": 1}


@app.post("/api/game/join")
def api_join_game(req: JoinGameReq, user=Depends(get_user)):
    auth.ensure_stats(user["user_id"], user["username"])
    gs = _load_game(req.game_id)
    if not gs:
        raise HTTPException(404, "game_not_found")
    players = GAME_PLAYERS.get(gs.game_id, {})
    if 2 in players and players[2] != user["user_id"]:
        raise HTTPException(400, "game_full")
    players[2] = user["user_id"]
    GAME_PLAYERS[gs.game_id] = players
    return {"ok": True, "game_id": gs.game_id, "player": 2}


@app.get("/api/game/list")
def api_list_games(user=Depends(get_user)):
    games = []
    conn = auth.get_db()
    rows = conn.execute(
        "SELECT game_id, mode, created_at FROM active_games ORDER BY created_at DESC LIMIT 50"
    ).fetchall()
    conn.close()
    for r in rows:
        gid = r["game_id"]
        players = GAME_PLAYERS.get(gid, {})
        gs = GAMES.get(gid)
        games.append({
            "game_id": gid,
            "mode": r["mode"],
            "phase": gs.phase if gs else "unknown",
            "players": len(players),
            "joinable": 2 not in players and r["mode"] == "online",
        })
    return {"ok": True, "games": games}


# ============================================================
# GAME ACTIONS
# ============================================================

def _get_game_and_player(game_id: str, user: dict) -> tuple:
    gs = _load_game(game_id)
    if not gs:
        raise HTTPException(404, "game_not_found")
    players = GAME_PLAYERS.get(game_id, {})
    player = None
    for p, uid in players.items():
        if uid == user["user_id"]:
            player = p
            break
    if player is None:
        raise HTTPException(403, "not_in_game")
    return gs, player


class PlaceReq(BaseModel):
    # v1.0: create-and-place by unit type
    utype: Optional[str] = None
    col: int
    row: int
    # Legacy: some clients may still send unit_id (ignored in v1.0)
    unit_id: Optional[str] = None


class UnplaceReq(BaseModel):
    unit_id: str


class ClearPlacementReq(BaseModel):
    pass


class ApplyPresetReq(BaseModel):
    preset: list  # [{utype, col, row}, ...]


class ConfirmReq(BaseModel):
    force: bool = False


class MoveReq(BaseModel):
    unit_id: str
    col: int
    row: int


class AttackReq(BaseModel):
    attacker_id: str
    target_id: str


class SpecialReq(BaseModel):
    unit_id: str
    action: str
    target_id: str


@app.get("/api/game/{game_id}/state")
def api_game_state(game_id: str, user=Depends(get_user)):
    gs, player = _get_game_and_player(game_id, user)
    view = gs.get_player_view(player)

    # Add available actions for current player
    if gs.phase == "battle" and gs.current_player == player:
        view["available_actions"] = _get_available_actions(gs, player)

    return {"ok": True, **view}


@app.post("/api/game/{game_id}/place")
def api_place(game_id: str, req: PlaceReq, user=Depends(get_user)):
    gs, player = _get_game_and_player(game_id, user)
    if not req.utype:
        return {"ok": False, "error": "missing_utype"}
    result = gs.place_new_unit(player, req.utype, req.col, req.row)
    if result["ok"]:
        _save_game(gs)
    return result


@app.post("/api/game/{game_id}/clear_placement")
def api_clear_placement(game_id: str, user=Depends(get_user)):
    gs, player = _get_game_and_player(game_id, user)
    result = gs.clear_placement(player)
    if result["ok"]:
        _save_game(gs)
    return result


@app.post("/api/game/{game_id}/apply_preset")
def api_apply_preset(game_id: str, req: ApplyPresetReq, user=Depends(get_user)):
    gs, player = _get_game_and_player(game_id, user)
    result = gs.apply_preset(player, req.preset)
    if result["ok"]:
        _save_game(gs)
    return result


@app.post("/api/game/{game_id}/unplace")
def api_unplace(game_id: str, req: UnplaceReq, user=Depends(get_user)):
    gs, player = _get_game_and_player(game_id, user)
    result = gs.unplace_unit(player, req.unit_id)
    if result["ok"]:
        _save_game(gs)
    return result


@app.post("/api/game/{game_id}/random_place")
def api_random_place(game_id: str, user=Depends(get_user)):
    gs, player = _get_game_and_player(game_id, user)
    if gs.phase != "placement":
        return {"ok": False, "error": "not_placement_phase"}
    ai = AI(player=player)
    ai.do_placement(gs)
    placed = len([u for u in gs.units if u.owner == player and u.alive and u.placed])
    _save_game(gs)
    return {"ok": True, "placed": placed}


@app.post("/api/game/{game_id}/confirm")
def api_confirm(game_id: str, req: ConfirmReq = ConfirmReq(), user=Depends(get_user)):
    gs, player = _get_game_and_player(game_id, user)
    result = gs.confirm_placement(player, force=req.force)
    if result["ok"]:
        # If this started the battle and AI goes first, let AI play immediately
        if gs.phase == "battle":
            _maybe_ai_turn(gs)
        _save_game(gs)
    return result


@app.post("/api/game/{game_id}/speedup")
def api_speedup(game_id: str, user=Depends(get_user)):
    gs, player = _get_game_and_player(game_id, user)
    result = gs.request_speedup(player)
    if result["ok"]:
        _save_game(gs)
    return result


@app.post("/api/game/{game_id}/move")
def api_move(game_id: str, req: MoveReq, user=Depends(get_user)):
    gs, player = _get_game_and_player(game_id, user)
    result = gs.move_unit(player, req.unit_id, req.col, req.row)
    if result["ok"]:
        if gs.phase == "finished":
            _record_result(gs)
        else:
            _maybe_ai_turn(gs)
        _save_game(gs)
    return result


@app.post("/api/game/{game_id}/attack")
def api_attack(game_id: str, req: AttackReq, user=Depends(get_user)):
    gs, player = _get_game_and_player(game_id, user)
    result = gs.attack_unit(player, req.attacker_id, req.target_id)
    if result["ok"]:
        if gs.phase == "finished":
            _record_result(gs)
        else:
            _maybe_ai_turn(gs)
        _save_game(gs)
    return result


@app.post("/api/game/{game_id}/special")
def api_special(game_id: str, req: SpecialReq, user=Depends(get_user)):
    gs, player = _get_game_and_player(game_id, user)
    result = gs.do_special(player, req.unit_id, req.action, req.target_id)
    if result["ok"]:
        if gs.phase == "finished":
            _record_result(gs)
        else:
            _maybe_ai_turn(gs)
        _save_game(gs)
    return result


@app.post("/api/game/{game_id}/pass")
def api_pass(game_id: str, user=Depends(get_user)):
    gs, player = _get_game_and_player(game_id, user)
    result = gs.pass_turn(player)
    if result["ok"]:
        _maybe_ai_turn(gs)
        _save_game(gs)
    return result


# ============================================================
# HOTSEAT – player 2 view
# ============================================================

@app.get("/api/game/{game_id}/state/{player_num}")
def api_game_state_hotseat(game_id: str, player_num: int, user=Depends(get_user)):
    gs = _load_game(game_id)
    if not gs or gs.mode != "hotseat":
        raise HTTPException(404, "game_not_found")
    if player_num not in (1, 2):
        raise HTTPException(400, "invalid_player")
    view = gs.get_player_view(player_num)
    if gs.phase == "battle" and gs.current_player == player_num:
        view["available_actions"] = _get_available_actions(gs, player_num)
    return {"ok": True, **view}


class HotseatActionReq(BaseModel):
    player: int
    action: str  # place, unplace, confirm, move, attack, special, pass_turn, clear, preset
    utype: Optional[str] = None
    unit_id: Optional[str] = None
    target_id: Optional[str] = None
    col: Optional[int] = None
    row: Optional[int] = None
    special_action: Optional[str] = None
    force: bool = False
    preset: Optional[list] = None


def _req_str(v: Optional[str], name: str) -> str:
    if v is None:
        raise HTTPException(400, f"missing_{name}")
    return v


def _req_int(v: Optional[int], name: str) -> int:
    if v is None:
        raise HTTPException(400, f"missing_{name}")
    return v


@app.post("/api/game/{game_id}/hotseat")
def api_hotseat_action(game_id: str, req: HotseatActionReq, user=Depends(get_user)):
    gs = _load_game(game_id)
    if not gs or gs.mode != "hotseat":
        raise HTTPException(404, "game_not_found")
    p = req.player

    if req.action == "place":
        result = gs.place_new_unit(p, _req_str(req.utype, "utype"),
                                    _req_int(req.col, "col"), _req_int(req.row, "row"))
    elif req.action == "unplace":
        result = gs.unplace_unit(p, _req_str(req.unit_id, "unit_id"))
    elif req.action == "random_place":
        ai = AI(player=p)
        ai.do_placement(gs)
        placed = len([u for u in gs.units if u.owner == p and u.alive and u.placed])
        result = {"ok": True, "placed": placed}
    elif req.action == "clear":
        result = gs.clear_placement(p)
    elif req.action == "preset":
        result = gs.apply_preset(p, req.preset or [])
    elif req.action == "confirm":
        result = gs.confirm_placement(p, force=req.force)
    elif req.action == "move":
        result = gs.move_unit(p, _req_str(req.unit_id, "unit_id"),
                               _req_int(req.col, "col"), _req_int(req.row, "row"))
    elif req.action == "attack":
        result = gs.attack_unit(p, _req_str(req.unit_id, "unit_id"),
                                 _req_str(req.target_id, "target_id"))
    elif req.action == "special":
        result = gs.do_special(p, _req_str(req.unit_id, "unit_id"),
                                _req_str(req.special_action, "special_action"),
                                _req_str(req.target_id, "target_id"))
    elif req.action == "pass_turn":
        result = gs.pass_turn(p)
    else:
        result = {"ok": False, "error": "unknown_action"}

    if result.get("ok"):
        _save_game(gs)
    return result


# ============================================================
# AI HELPERS
# ============================================================

def _maybe_ai_turn(gs: GameState):
    if gs.phase == "finished":
        _record_result(gs)
        return
    if gs.ai_player and gs.current_player == gs.ai_player:
        ai = AI(player=gs.ai_player)
        ai.do_turn(gs)
        if gs.phase == "finished":
            _record_result(gs)


def _record_result(gs: GameState):
    # Save full game log for analysis
    _save_game_log(gs)
    # Save replay for the viewer (all finished games, regardless of mode)
    _save_replay(gs)
    if not gs.winner:
        return
    players = GAME_PLAYERS.get(gs.game_id, {})
    if gs.mode == "online":
        winner_pid = players.get(gs.winner)
        loser_pid = players.get(3 - gs.winner)
        if winner_pid and loser_pid and winner_pid != "AI" and loser_pid != "AI":
            auth.update_elo(winner_pid, loser_pid)
    elif gs.mode == "bot":
        bots = BOT_PLAYERS.get(gs.game_id, {})
        if gs.winner and bots.get(gs.winner) and bots.get(3 - gs.winner):
            auth.update_bot_elo(bots[gs.winner], bots[3 - gs.winner])


def _label_for_player(gs: GameState, player: int) -> str:
    players = GAME_PLAYERS.get(gs.game_id, {})
    bots = BOT_PLAYERS.get(gs.game_id, {})
    if bots.get(player):
        conn = auth.get_db()
        row = conn.execute("SELECT bot_name FROM bot_accounts WHERE id=?",
                           (bots[player],)).fetchone()
        conn.close()
        return f"🤖 {row['bot_name']}" if row else "🤖 bot"
    uid = players.get(player)
    if uid == "AI":
        return "AI"
    if uid:
        conn = auth.get_db()
        row = conn.execute("SELECT username FROM users WHERE id=?", (uid,)).fetchone()
        conn.close()
        return row["username"] if row else f"user#{uid}"
    return f"player {player}"


BATTLE_ACTION_TYPES = {"move", "attack", "attack_wasted", "special", "pass", "turn_timeout"}


def _save_replay(gs: GameState):
    players = GAME_PLAYERS.get(gs.game_id, {})
    initial = json.dumps(gs.battle_start_state, ensure_ascii=False) if gs.battle_start_state else "{}"
    # Only battle-phase actions go into the replay; placement is captured in initial snapshot
    battle_actions = [a for a in gs.replay_actions if a.get("type") in BATTLE_ACTION_TYPES]
    auth.save_replay(
        game_id=gs.game_id,
        mode=gs.mode,
        player1_id=players.get(1) if players.get(1) != "AI" else None,
        player2_id=players.get(2) if players.get(2) != "AI" else None,
        player1_label=_label_for_player(gs, 1),
        player2_label=_label_for_player(gs, 2),
        winner=gs.winner,
        turns=gs.turn,
        actions=battle_actions,
        initial_state_json=initial,
    )


def _save_game_log(gs: GameState):
    """Rich per-game log for offline analysis.
    Includes: final state, full engine event log, raw replay action stream
    (with bot rationales if any), per-turn built-in AI decision traces."""
    os.makedirs("game_logs", exist_ok=True)
    players = GAME_PLAYERS.get(gs.game_id, {})
    bots = BOT_PLAYERS.get(gs.game_id, {})

    # Reconstruct per-turn AI decision traces from any AI() instances that played
    # (built-in AI logs via ai.decision_log, but we don't keep refs — we only
    # have the mutations on gs. External bot rationales live inside replay_actions.)
    log = {
        "game_id": gs.game_id,
        "engine_version": gs.engine_version,
        "mode": gs.mode,
        "finished_at": datetime.now().isoformat(),
        "winner": gs.winner,
        "turns": gs.turn,
        "ply": gs.ply,
        "players": {str(k): v for k, v in players.items()},
        "bots": {str(k): v for k, v in bots.items()},
        "first_confirmed": gs.first_confirmed,
        "hacker_conversions": gs.hacker_conversions,
        "p1_alive": len([u for u in gs.units if u.owner == 1 and u.alive]),
        "p2_alive": len([u for u in gs.units if u.owner == 2 and u.alive]),
        "p1_alive_by_type": _count_by_type(gs, 1),
        "p2_alive_by_type": _count_by_type(gs, 2),
        "placement": {
            str(p): [
                {"unit_id": u.id, "type": u.type, "col": u.col, "row": u.row,
                 "placement_level": u.placement_level, "attack_at_placement": u.base_attack + u.level_bonus}
                for u in gs.units if u.owner == p
            ]
            for p in (1, 2)
        },
        "events": gs.log,                    # engine's human-readable event messages
        "replay_actions": gs.replay_actions, # raw action stream incl. rationales for bot moves
        "final_units": [u.to_dict() for u in gs.units],
        "battle_start_state": gs.battle_start_state,
    }
    fname = f"game_logs/{gs.game_id}.json"
    with open(fname, "w", encoding="utf-8") as f:
        json.dump(log, f, ensure_ascii=False, indent=1)


def _count_by_type(gs: GameState, player: int) -> dict:
    out = {}
    for u in gs.units:
        if u.owner == player and u.alive:
            out[u.type] = out.get(u.type, 0) + 1
    return out


def _get_available_actions(gs: GameState, player: int) -> dict:
    actions = {"moves": [], "attacks": [], "specials": []}
    for u in gs._player_units(player, placed=True):
        uid = u.id
        # Moves
        if u.movement > 0:
            reachable = gs.get_reachable(u)
            if reachable:
                actions["moves"].append({
                    "unit_id": uid,
                    "targets": [{"col": c, "row": r} for c, r in reachable]
                })
        # Attacks
        targets = gs.get_attack_targets(u)
        if targets:
            actions["attacks"].append({"unit_id": uid, "targets": targets})
        # Specials
        specials = gs.get_special_actions(u)
        if specials:
            actions["specials"].append({"unit_id": uid, "actions": specials})
    return actions


# ============================================================
# PERSISTENCE
# ============================================================

def _save_game(gs: GameState):
    GAMES[gs.game_id] = gs
    data = gs.to_json()
    players = GAME_PLAYERS.get(gs.game_id, {})
    conn = auth.get_db()
    conn.execute("""
        INSERT INTO active_games (game_id, player1_id, player2_id, mode, state_json, updated_at)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(game_id) DO UPDATE SET
            state_json = excluded.state_json,
            updated_at = excluded.updated_at
    """, (gs.game_id, players.get(1), players.get(2), gs.mode, data, time.time()))
    conn.commit()
    conn.close()
    # Autosave for player 1
    p1_id = players.get(1)
    if p1_id and p1_id != "AI" and gs.phase in ("battle", "placement"):
        auth.save_game(p1_id, f"Autosave T{gs.turn}", gs.game_id, data, is_autosave=True)


def _load_game(game_id: str) -> Optional[GameState]:
    if game_id in GAMES:
        return GAMES[game_id]
    conn = auth.get_db()
    row = conn.execute("SELECT * FROM active_games WHERE game_id = ?", (game_id,)).fetchone()
    conn.close()
    if not row:
        return None
    try:
        gs = GameState.from_json(row["state_json"])
    except ValueError as e:
        # v0.x saves are incompatible with v1.0 engine – mark the row
        # so the client is shown a helpful error rather than crashing.
        if "incompatible_engine_version" in str(e):
            return None
        raise
    GAMES[game_id] = gs
    GAME_PLAYERS[game_id] = {}
    if row["player1_id"]:
        GAME_PLAYERS[game_id][1] = row["player1_id"]
    if row["player2_id"]:
        GAME_PLAYERS[game_id][2] = row["player2_id"]
    return gs


# ============================================================
# PRESETS (saved formations)
# ============================================================

class PresetSaveReq(BaseModel):
    slot: int
    name: str
    units: list  # [{utype, col, row}, ...]


@app.get("/api/presets")
def api_list_presets(user=Depends(get_user)):
    return {"ok": True, "presets": auth.list_presets(user["user_id"])}


@app.post("/api/presets")
def api_save_preset(req: PresetSaveReq, user=Depends(get_user)):
    return auth.save_preset(user["user_id"], req.slot, req.name, req.units)


@app.delete("/api/presets/{slot}")
def api_delete_preset(slot: int, user=Depends(get_user)):
    return auth.delete_preset(user["user_id"], slot)


# ============================================================
# ADMIN – password login + stat overrides
# ============================================================

class AdminLoginReq(BaseModel):
    password: str


class AdminPasswordReq(BaseModel):
    new_password: str


class AdminUnitOverrideReq(BaseModel):
    utype: str
    base_attack: Optional[int] = None
    movement: Optional[int] = None
    base_range: Optional[int] = None
    max_count: Optional[int] = None
    category: Optional[str] = None


def _make_admin_token() -> str:
    return auth._make_jwt({"admin": True, "exp": time.time() + 3600})


def require_admin(request: Request):
    token = request.headers.get("Authorization", "").replace("Bearer ", "")
    if not token:
        token = request.headers.get("X-Admin-Token", "")
    payload = auth._decode_jwt(token) if token else None
    if not payload or not payload.get("admin"):
        raise HTTPException(401, "admin_required")
    return payload


@app.post("/api/admin/login")
def api_admin_login(req: AdminLoginReq):
    if not auth.verify_admin_password(req.password):
        raise HTTPException(401, "invalid_admin_password")
    return {"ok": True, "admin_token": _make_admin_token()}


@app.post("/api/admin/change_password")
def api_admin_change_password(req: AdminPasswordReq, _=Depends(require_admin)):
    return auth.change_admin_password(req.new_password)


@app.get("/api/admin/unit_defs")
def api_admin_unit_defs(_=Depends(require_admin)):
    """Current UNIT_DEFS + active overrides, so the admin UI can show both."""
    settings = auth.get_admin_settings()
    return {"ok": True, "unit_defs": UNIT_DEFS,
            "overrides": settings.get("unit_overrides", {})}


@app.post("/api/admin/unit_override")
def api_admin_unit_override(req: AdminUnitOverrideReq, _=Depends(require_admin)):
    """Set or clear an override for a specific unit type. None fields are unchanged."""
    if req.utype not in UNIT_DEFS:
        raise HTTPException(400, "unknown_utype")
    settings = auth.get_admin_settings()
    overrides = settings.setdefault("unit_overrides", {})
    current = overrides.get(req.utype, {})
    for field in ("base_attack", "movement", "base_range", "max_count", "category"):
        v = getattr(req, field)
        if v is not None:
            current[field] = v
    overrides[req.utype] = current
    auth.set_admin_settings(settings)
    return {"ok": True, "utype": req.utype, "override": current}


@app.delete("/api/admin/unit_override/{utype}")
def api_admin_unit_override_clear(utype: str, _=Depends(require_admin)):
    settings = auth.get_admin_settings()
    overrides = settings.get("unit_overrides", {})
    overrides.pop(utype, None)
    settings["unit_overrides"] = overrides
    auth.set_admin_settings(settings)
    return {"ok": True}


# ============================================================
# REPLAYS
# ============================================================

@app.get("/api/replays")
def api_list_replays(mine: bool = False, user=Depends(get_user)):
    return {"ok": True,
            "replays": auth.list_replays(user_id=user["user_id"] if mine else None)}


@app.get("/api/replays/{replay_id}")
def api_load_replay(replay_id: int, _user=Depends(get_user)):
    data = auth.load_replay(replay_id)
    if not data:
        raise HTTPException(404, "replay_not_found")
    return {"ok": True, "replay": data}


def _apply_replay_action(gs: GameState, a: dict):
    """Apply a single recorded battle action to a GameState instance (for replay rebuild)."""
    t = a.get("type")
    p = a.get("player") or a.get("current_player") or gs.current_player
    if t == "move":
        c, r = a["to"]
        gs.move_unit(p, a["unit_id"], c, r)
    elif t in ("attack", "attack_wasted"):
        gs.attack_unit(p, a["attacker"], a["target"])
    elif t == "special":
        gs.do_special(p, a["unit_id"], a["action"], a["target_id"])
    elif t == "pass":
        gs.pass_turn(p)
    elif t == "turn_timeout":
        # Engine didn't expose a direct "force turn end" for current player; approximate via pass
        try:
            gs.pass_turn(gs.current_player)
        except Exception:
            pass


@app.get("/api/replays/{replay_id}/snapshot")
def api_replay_snapshot(replay_id: int, at: int = 0, as_player: int = 1,
                         _user=Depends(get_user)):
    """Return player view of replay state after applying `at` actions from battle start.
    `as_player` decides whose fog-of-war view is produced (1 or 2)."""
    data = auth.load_replay(replay_id)
    if not data:
        raise HTTPException(404, "replay_not_found")
    try:
        gs = GameState.from_json(data["initial_state_json"])
    except Exception:
        raise HTTPException(500, "initial_state_corrupt")
    actions = data.get("actions") or []
    at = max(0, min(at, len(actions)))
    for a in actions[:at]:
        try:
            _apply_replay_action(gs, a)
        except Exception:
            # If a replay action fails to apply (schema mismatch), stop but return current state
            break
    view = gs.get_player_view(as_player)
    # For replay viewer, reveal everything (no fog of war – the user is reviewing a finished game)
    for u in gs.units:
        if u.alive and u.placed and u.owner != as_player:
            d = u.to_dict()
            d["effective_range"] = u.effective_range()
            d["is_corrupted"] = u.is_corrupted()
            # Replace the stub entry in enemy_units
            for i, eu in enumerate(view["enemy_units"]):
                if eu["id"] == u.id:
                    view["enemy_units"][i] = d
                    break
    return {"ok": True, "at": at, "total": len(actions),
            "state": view,
            "action": actions[at-1] if at > 0 else None}


# ============================================================
# BOT ACCOUNTS (management by the user who owns them)
# ============================================================

class CreateBotReq(BaseModel):
    bot_name: str
    can_play_humans: bool = True


@app.get("/api/bot-accounts")
def api_list_bot_accounts(user=Depends(get_user)):
    return {"ok": True, "bots": auth.list_bots_for_user(user["user_id"])}


@app.post("/api/bot-accounts")
def api_create_bot_account(req: CreateBotReq, user=Depends(get_user)):
    return auth.create_bot_account(user["user_id"], req.bot_name, req.can_play_humans)


@app.delete("/api/bot-accounts/{bot_id}")
def api_delete_bot_account(bot_id: int, user=Depends(get_user)):
    return auth.delete_bot_account(user["user_id"], bot_id)


@app.get("/api/bot-leaderboard")
def api_bot_leaderboard():
    return {"ok": True, "leaderboard": auth.get_bot_leaderboard(50)}


# ============================================================
# BOT API – used by external bots authenticated via X-API-Key
# ============================================================

def _bot_from_request(request: Request) -> dict:
    api_key = request.headers.get("X-API-Key", "")
    if not api_key:
        raise HTTPException(401, "missing_api_key")
    bot = auth.authenticate_bot(api_key)
    if not bot:
        raise HTTPException(401, "invalid_api_key")
    return bot


def _cleanup_bot_queue():
    """Remove stale (>5 min) entries from the bot queue."""
    now = time.time()
    global BOT_QUEUE
    BOT_QUEUE = [e for e in BOT_QUEUE if now - e[2] < 300]


def _bot_view(gs: GameState, player: int) -> dict:
    view = gs.get_player_view(player)
    if gs.phase == "battle" and gs.current_player == player:
        view["available_actions"] = _get_available_actions(gs, player)
    return view


class BotNewGameReq(BaseModel):
    # "ai" = immediate game vs built-in AI
    # "queue" = enter queue for another bot; falls back to AI after 30 s
    # "human" = wait for a human opponent (bot's can_play_humans must be true)
    opponent: str = "ai"


@app.post("/api/bot/games")
def api_bot_new_game(req: BotNewGameReq, request: Request):
    bot = _bot_from_request(request)
    _cleanup_bot_queue()

    if req.opponent == "queue":
        # Match with any waiting bot
        for i, (other_bot_id, other_game_id, _ts) in enumerate(BOT_QUEUE):
            if other_bot_id == bot["id"]:
                continue
            # Attach this bot as player 2 of other_game_id
            gs = _load_game(other_game_id)
            if not gs or gs.phase != "placement":
                continue
            BOT_PLAYERS.setdefault(other_game_id, {})[2] = bot["id"]
            BOT_QUEUE.pop(i)
            _save_game(gs)
            return {"ok": True, "game_id": other_game_id, "player": 2,
                    "opponent": "bot"}
        # No match; create queued game as player 1 and return
        gs = GameState(mode="bot")
        GAMES[gs.game_id] = gs
        BOT_PLAYERS[gs.game_id] = {1: bot["id"]}
        BOT_QUEUE.append((bot["id"], gs.game_id, time.time()))
        _save_game(gs)
        return {"ok": True, "game_id": gs.game_id, "player": 1,
                "opponent": "pending", "queue_timeout_s": 30}

    # opponent == "ai" (default) – start a game vs built-in AI right away
    gs = GameState(mode="bot")
    gs.ai_player = 2
    GAMES[gs.game_id] = gs
    BOT_PLAYERS[gs.game_id] = {1: bot["id"]}
    GAME_PLAYERS[gs.game_id] = {2: "AI"}
    ai = AI(player=2)
    ai.do_placement(gs)
    gs.confirm_placement(2, force=True)
    _save_game(gs)
    return {"ok": True, "game_id": gs.game_id, "player": 1, "opponent": "ai"}


@app.post("/api/bot/queue/fallback/{game_id}")
def api_bot_queue_fallback(game_id: str, request: Request):
    """Client-called after 30 s of queue wait: replaces queue slot with built-in AI."""
    bot = _bot_from_request(request)
    gs = _load_game(game_id)
    if not gs or gs.mode != "bot":
        raise HTTPException(404, "game_not_found")
    bots = BOT_PLAYERS.get(game_id, {})
    if bots.get(1) != bot["id"]:
        raise HTTPException(403, "not_owner")
    if 2 in bots or gs.ai_player:
        return {"ok": True, "opponent": "ai_already_set"}
    # Wire up AI as player 2
    gs.ai_player = 2
    GAME_PLAYERS.setdefault(game_id, {})[2] = "AI"
    ai = AI(player=2)
    ai.do_placement(gs)
    gs.confirm_placement(2, force=True)
    # Remove from queue
    global BOT_QUEUE
    BOT_QUEUE = [e for e in BOT_QUEUE if e[1] != game_id]
    _save_game(gs)
    return {"ok": True, "opponent": "ai"}


def _bot_game_and_player(game_id: str, request: Request) -> tuple:
    bot = _bot_from_request(request)
    gs = _load_game(game_id)
    if not gs or gs.mode != "bot":
        raise HTTPException(404, "game_not_found")
    bots = BOT_PLAYERS.get(game_id, {})
    player = None
    for p, bid in bots.items():
        if bid == bot["id"]:
            player = p
            break
    if player is None:
        raise HTTPException(403, "not_in_game")
    return gs, player


@app.get("/api/bot/games/{game_id}/state")
def api_bot_state(game_id: str, request: Request):
    gs, player = _bot_game_and_player(game_id, request)
    return {"ok": True, **_bot_view(gs, player)}


class BotPlaceReq(BaseModel):
    utype: str
    col: int
    row: int


class BotPresetApplyReq(BaseModel):
    preset: list  # list of {utype, col, row}


class BotConfirmReq(BaseModel):
    force: bool = False


class BotMoveReq(BaseModel):
    unit_id: str
    col: int
    row: int
    rationale: Optional[str] = None  # agent's reasoning, stored with the action


class BotAttackReq(BaseModel):
    attacker_id: str
    target_id: str
    rationale: Optional[str] = None


class BotSpecialReq(BaseModel):
    unit_id: str
    action: str
    target_id: str
    rationale: Optional[str] = None


class BotPassReq(BaseModel):
    rationale: Optional[str] = None


@app.post("/api/bot/games/{game_id}/place")
def api_bot_place(game_id: str, req: BotPlaceReq, request: Request):
    gs, player = _bot_game_and_player(game_id, request)
    r = gs.place_new_unit(player, req.utype, req.col, req.row)
    if r["ok"]:
        _save_game(gs)
    return r


@app.post("/api/bot/games/{game_id}/clear_placement")
def api_bot_clear(game_id: str, request: Request):
    gs, player = _bot_game_and_player(game_id, request)
    r = gs.clear_placement(player)
    if r["ok"]:
        _save_game(gs)
    return r


@app.post("/api/bot/games/{game_id}/random_place")
def api_bot_random_place(game_id: str, request: Request):
    """Convenience: built-in AI fills the bot's side so they only tweak if wanted."""
    gs, player = _bot_game_and_player(game_id, request)
    if gs.phase != "placement":
        return {"ok": False, "error": "not_placement_phase"}
    ai = AI(player=player)
    ai.do_placement(gs)
    placed = len([u for u in gs.units if u.owner == player and u.placed])
    _save_game(gs)
    return {"ok": True, "placed": placed}


@app.post("/api/bot/games/{game_id}/apply_preset")
def api_bot_apply_preset(game_id: str, req: BotPresetApplyReq, request: Request):
    gs, player = _bot_game_and_player(game_id, request)
    r = gs.apply_preset(player, req.preset)
    if r["ok"]:
        _save_game(gs)
    return r


@app.post("/api/bot/games/{game_id}/confirm")
def api_bot_confirm(game_id: str, req: BotConfirmReq, request: Request):
    gs, player = _bot_game_and_player(game_id, request)
    r = gs.confirm_placement(player, force=req.force)
    if r["ok"]:
        if gs.phase == "battle":
            _maybe_ai_turn(gs)
        _save_game(gs)
    return r


def _tag_last_action_rationale(gs: GameState, rationale: Optional[str]):
    """Store agent's reasoning on the most recent replay action record."""
    if rationale and gs.replay_actions:
        gs.replay_actions[-1]["rationale"] = rationale[:2000]  # cap length


@app.post("/api/bot/games/{game_id}/move")
def api_bot_move(game_id: str, req: BotMoveReq, request: Request):
    gs, player = _bot_game_and_player(game_id, request)
    r = gs.move_unit(player, req.unit_id, req.col, req.row)
    if r["ok"]:
        _tag_last_action_rationale(gs, req.rationale)
        if gs.phase == "finished":
            _record_result(gs)
        else:
            _maybe_ai_turn(gs)
        _save_game(gs)
    return r


@app.post("/api/bot/games/{game_id}/attack")
def api_bot_attack(game_id: str, req: BotAttackReq, request: Request):
    gs, player = _bot_game_and_player(game_id, request)
    r = gs.attack_unit(player, req.attacker_id, req.target_id)
    if r["ok"]:
        _tag_last_action_rationale(gs, req.rationale)
        if gs.phase == "finished":
            _record_result(gs)
        else:
            _maybe_ai_turn(gs)
        _save_game(gs)
    return r


@app.post("/api/bot/games/{game_id}/special")
def api_bot_special(game_id: str, req: BotSpecialReq, request: Request):
    gs, player = _bot_game_and_player(game_id, request)
    r = gs.do_special(player, req.unit_id, req.action, req.target_id)
    if r["ok"]:
        _tag_last_action_rationale(gs, req.rationale)
        if gs.phase == "finished":
            _record_result(gs)
        else:
            _maybe_ai_turn(gs)
        _save_game(gs)
    return r


@app.post("/api/bot/games/{game_id}/pass")
def api_bot_pass(game_id: str, request: Request, req: BotPassReq = BotPassReq()):
    gs, player = _bot_game_and_player(game_id, request)
    r = gs.pass_turn(player)
    if r["ok"]:
        _tag_last_action_rationale(gs, req.rationale)
        _maybe_ai_turn(gs)
        _save_game(gs)
    return r


# ============================================================
# BOT RULES ENDPOINT – machine-readable game description
# ============================================================

@app.get("/api/bot/rules")
def api_bot_rules():
    """Full game rules + unit definitions in a structured format.

    An external LLM agent can inject this JSON into its system prompt to
    bootstrap understanding of the game without parsing API.md.
    """
    return {
        "ok": True,
        "version": "1.0",
        "description": (
            "Maršál & Špión – 2-player hex-grid strategy with fog of war. "
            "Players place 51 units in three level zones, then take turns "
            "moving / attacking / using special abilities. Win by stepping "
            "a ground (or special) unit onto the enemy citadel."
        ),
        "board": {
            "num_rows": NUM_ROWS,
            "shape": "odd-r offset hex diamond, 17 rows",
            "mountains": [list(m) for m in MOUNTAINS],
            "citadels": {str(k): list(v) for k, v in CITADELS.items()},
            "zones": [
                {"rows": [0], "zone_type": "citadel", "player": 1},
                {"rows": [1, 2], "zone_type": "level", "player": 1, "level": 0},
                {"rows": [3, 4], "zone_type": "level", "player": 1, "level": 1},
                {"rows": [5, 6], "zone_type": "level", "player": 1, "level": 2},
                {"rows": [7, 8, 9], "zone_type": "battlefield"},
                {"rows": [10, 11], "zone_type": "level", "player": 2, "level": 2},
                {"rows": [12, 13], "zone_type": "level", "player": 2, "level": 1},
                {"rows": [14, 15], "zone_type": "level", "player": 2, "level": 0},
                {"rows": [16], "zone_type": "citadel", "player": 2},
            ],
            "level_hexes_per_player": {"0": 9, "1": 17, "2": 25, "total": 51},
        },
        "placement_rules": {
            "all_hexes_must_be_filled": True,
            "special_cap_per_level": {"0": special_cap(0), "1": special_cap(1), "2": special_cap(2),
                                       "formula": "2 + level"},
            "unique_per_level": list(PER_LEVEL_UNIQUE),
            "artillery_level_0_only": True,
            "first_battle_turn": "whoever confirms placement first",
        },
        "battle_rules": {
            "actions_per_turn": ["move", "attack", "special", "pass"],
            "movement": {
                "ground_through_own": False,
                "ground_through_enemy": False,
                "fighter_through_own": True,
                "air_ignores_mountains": True,
                "ground_onto_mine_field": "attacker dies, mine reveals + stays",
            },
            "melee_attack": {
                "higher_attack_wins_and_moves_into_hex": True,
                "tie_both_die": True,
                "attacker_reveals_on_engage": True,
                "defender_reveals_on_engage": True,
            },
            "special_interactions": {
                "hacker_vs_terminator": "hacker always wins (melee)",
                "fighter_vs_non_air": "wasted turn",
                "engineer_vs_mine_field": "mine destroyed, engineer survives",
                "ground_vs_mine_field": "ground dies, mine survives+reveals",
                "air_vs_mine_field": "both reveal, neither dies",
                "attack_drone_special": "destroys target if attack<4; otherwise drone revealed + target temp-revealed 1 turn",
                "corruptor_floor": "can't reduce attack/range below 1; wasted turn if would",
                "corruptor_stealth": "does NOT reveal self or change target's reveal state",
            },
            "trainer_ability": {
                "boost": f"+1 attack on allied ground/air, max {TRAINER_BOOST_MAX_PER_UNIT} per target",
                "convert_to_hacker": f"own ground with attack>3 becomes Hacker, max {HACKER_CONVERSIONS_PER_GAME} per game",
                "visibility": "trainer boosts/conversions hidden from opponent until that unit is revealed",
            },
            "jammer_effect": "nearby friendly units (range 1+L) cannot be revealed by recon drones (combat reveal still works)",
            "artillery_ability": "destroys any ground/special at range 2 (one hit)",
            "citadel_capture": "ground OR special unit steps onto enemy citadel → immediate win",
            "speedup_flow": "2 min idle → opponent may POST /speedup → 60 s countdown → turn force-passed on expiry",
        },
        "unit_defs": {t: {
            "name": d["name_cs"],
            "abbr": d["abbr"],
            "base_attack": d["base_attack"],
            "movement": d["movement"],
            "category": d["category"],
            "is_special": d["is_special"],
            "max_count": d["max_count"],
            "level_scales_attack": d["level_scales_attack"],
            "base_range": d["base_range"],
            "range_scales_level": d["range_scales_level"],
            "std_attack_targets": list(d["std_attack_targets"]),
            "can_be_std_target": d["can_be_std_target"],
            "corruptor_effect": d["corruptor_effect"],
            "trainer_effect": d["trainer_effect"],
        } for t, d in UNIT_DEFS.items()},
        "event_types": {
            "attacker_wins": "attacker stronger, defender dies, attacker moves in",
            "defender_wins": "defender stronger, attacker dies",
            "both_die": "equal attack, both destroyed",
            "hacker_kills_terminator": "hacker auto-kill on terminator",
            "mine_kills_ground": "ground attacker destroyed by mine_field",
            "mine_defused_by_attack": "engineer cleared mine_field",
            "mine_reveals_air": "helicopter + mine both revealed, both stay",
            "wasted_turn": "attack was invalid category (fighter vs ground, etc.)",
            "citadel_captured": "victory",
            "drone_kill": "attack drone destroyed target (attack<4)",
            "drone_miss": "attack drone failed; drone revealed + target temp-revealed",
            "boosted": "trainer +1 attack on ally",
            "weakened_attack": "corruptor -1 attack on enemy",
            "weakened_range": "corruptor -1 range on enemy",
            "weaken_wasted": "corruptor at floor, turn wasted",
            "converted_to_hacker": "trainer changed own unit type",
            "artillery_kill": "artillery destroyed target at range 2",
            "concealed": "engineer hid an ally",
            "revealed": "recon drone exposed enemy",
        },
        "recommended_agent_workflow": [
            "1. GET /api/bot/rules once at startup; inject into system prompt",
            "2. POST /api/bot/games {opponent: 'ai'|'queue'|'human'}",
            "3. POST /api/bot/games/{id}/random_place + /confirm — OR place units one by one from a preset",
            "4. Loop: GET /state → if current_player == you, decide using available_actions → POST action with `rationale` → sleep → repeat",
            "5. After game ends, GET /api/bot/games/{id}/replay for full annotated history",
        ],
    }


@app.get("/api/bot/games/{game_id}/replay")
def api_bot_replay(game_id: str, request: Request):
    """After a game finishes, fetch the full replay for analysis."""
    _bot_from_request(request)  # just auth; anyone with a key may read
    conn = auth.get_db()
    row = conn.execute("SELECT * FROM game_replays WHERE game_id=?", (game_id,)).fetchone()
    conn.close()
    if not row:
        raise HTTPException(404, "replay_not_ready")
    data = dict(row)
    data["actions"] = json.loads(data["actions_json"])
    del data["actions_json"]
    return {"ok": True, "replay": data}


# ============================================================
# STATIC FILES
# ============================================================

@app.get("/")
def index():
    return FileResponse("static/index.html", headers={
        "Cache-Control": "no-cache, no-store, must-revalidate",
        "Pragma": "no-cache", "Expires": "0"
    })


app.mount("/static", StaticFiles(directory="static"), name="static")

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8030)
