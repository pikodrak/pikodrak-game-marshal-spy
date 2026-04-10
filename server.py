"""
Maršál a Špión – FastAPI Server
"""

import os
import json
import time
from typing import Optional
from fastapi import FastAPI, HTTPException, Request, Depends
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel
import uvicorn

from game_engine import GameState
from ai_engine import AI
import auth

app = FastAPI(title="Maršál a Špión")

# In-memory game store (backed by SQLite for persistence)
GAMES: dict[str, GameState] = {}
# Map game_id -> {1: user_id, 2: user_id}
GAME_PLAYERS: dict[str, dict] = {}


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
    gs = GameState.from_json(data)
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
    unit_id: str
    col: int
    row: int


class UnplaceReq(BaseModel):
    unit_id: str


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
    result = gs.place_unit(player, req.unit_id, req.col, req.row)
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
def api_confirm(game_id: str, user=Depends(get_user)):
    gs, player = _get_game_and_player(game_id, user)
    result = gs.confirm_placement(player)
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
    action: str  # place, unplace, confirm, move, attack, special, pass_turn
    unit_id: Optional[str] = None
    target_id: Optional[str] = None
    col: Optional[int] = None
    row: Optional[int] = None
    special_action: Optional[str] = None


@app.post("/api/game/{game_id}/hotseat")
def api_hotseat_action(game_id: str, req: HotseatActionReq, user=Depends(get_user)):
    gs = _load_game(game_id)
    if not gs or gs.mode != "hotseat":
        raise HTTPException(404, "game_not_found")
    p = req.player

    if req.action == "place":
        result = gs.place_unit(p, req.unit_id, req.col, req.row)
    elif req.action == "unplace":
        result = gs.unplace_unit(p, req.unit_id)
    elif req.action == "random_place":
        ai = AI(player=p)
        ai.do_placement(gs)
        placed = len([u for u in gs.units if u.owner == p and u.alive and u.placed])
        result = {"ok": True, "placed": placed}
    elif req.action == "confirm":
        result = gs.confirm_placement(p)
    elif req.action == "move":
        result = gs.move_unit(p, req.unit_id, req.col, req.row)
    elif req.action == "attack":
        result = gs.attack_unit(p, req.unit_id, req.target_id)
    elif req.action == "special":
        result = gs.do_special(p, req.unit_id, req.special_action, req.target_id)
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
    if not gs.winner:
        return
    players = GAME_PLAYERS.get(gs.game_id, {})
    if gs.mode == "online":
        winner_pid = players.get(gs.winner)
        loser_pid = players.get(3 - gs.winner)
        if winner_pid and loser_pid and winner_pid != "AI" and loser_pid != "AI":
            auth.update_elo(winner_pid, loser_pid)


def _save_game_log(gs: GameState):
    import os
    from datetime import datetime
    os.makedirs("game_logs", exist_ok=True)
    players = GAME_PLAYERS.get(gs.game_id, {})
    log = {
        "game_id": gs.game_id,
        "mode": gs.mode,
        "finished_at": datetime.now().isoformat(),
        "winner": gs.winner,
        "turns": gs.turn,
        "players": {str(k): v for k, v in players.items()},
        "p1_alive": len([u for u in gs.units if u.owner == 1 and u.alive]),
        "p2_alive": len([u for u in gs.units if u.owner == 2 and u.alive]),
        "placement": {},
        "events": gs.log,
        "final_units": [u.to_dict() for u in gs.units],
    }
    for p in (1, 2):
        log["placement"][p] = [
            {"type": u.type, "col": u.col, "row": u.row,
             "attack": u.attack, "level": u.placement_level}
            for u in gs.units if u.owner == p
        ]
    fname = f"game_logs/{gs.game_id}.json"
    with open(fname, "w", encoding="utf-8") as f:
        import json as json_mod
        json_mod.dump(log, f, ensure_ascii=False, indent=1)


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
    gs = GameState.from_json(row["state_json"])
    GAMES[game_id] = gs
    GAME_PLAYERS[game_id] = {}
    if row["player1_id"]:
        GAME_PLAYERS[game_id][1] = row["player1_id"]
    if row["player2_id"]:
        GAME_PLAYERS[game_id][2] = row["player2_id"]
    return gs


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
