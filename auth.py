"""
Authentication – SQLite users, bcrypt passwords, JWT tokens.
Also: saved formations (5 per user), admin config, bot API accounts, game replays.
"""

import sqlite3
import hashlib
import hmac
import os
import secrets
import time
import json
import base64

DB_PATH = "game03.db"
JWT_SECRET = os.environ.get("JWT_SECRET", "marshal_spy_secret_2026")
TOKEN_EXPIRY = 30 * 24 * 3600  # 30 days
ADMIN_PASSWORD_FILE = "admin_password.txt"
PRESETS_PER_USER = 5
PRESET_NAME_MAX = 12


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db():
    conn = get_db()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            email TEXT DEFAULT '',
            created_at REAL DEFAULT (strftime('%s','now'))
        );
        CREATE TABLE IF NOT EXISTS active_games (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            game_id TEXT UNIQUE NOT NULL,
            player1_id INTEGER,
            player2_id INTEGER,
            mode TEXT DEFAULT 'online',
            state_json TEXT,
            created_at REAL DEFAULT (strftime('%s','now')),
            updated_at REAL DEFAULT (strftime('%s','now'))
        );
        CREATE TABLE IF NOT EXISTS player_stats (
            user_id INTEGER PRIMARY KEY REFERENCES users(id),
            username TEXT NOT NULL,
            elo INTEGER DEFAULT 1000,
            wins INTEGER DEFAULT 0,
            losses INTEGER DEFAULT 0,
            draws INTEGER DEFAULT 0,
            games_played INTEGER DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS saved_games (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            game_id TEXT NOT NULL,
            state_json TEXT NOT NULL,
            is_autosave INTEGER DEFAULT 0,
            created_at REAL DEFAULT (strftime('%s','now'))
        );

        -- v1.0 additions --

        -- Saved unit formations: up to 5 per user, each holds a full placement list
        CREATE TABLE IF NOT EXISTS unit_presets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            slot INTEGER NOT NULL,  -- 1..5
            name TEXT NOT NULL,     -- <= 12 chars
            units_json TEXT NOT NULL,  -- [{utype, col, row}, ...]
            created_at REAL DEFAULT (strftime('%s','now')),
            updated_at REAL DEFAULT (strftime('%s','now')),
            UNIQUE(user_id, slot)
        );

        -- Admin config (single row, id=1). password_hash guards admin UI.
        -- settings_json may later hold unit base-stat overrides.
        CREATE TABLE IF NOT EXISTS admin_config (
            id INTEGER PRIMARY KEY,
            password_hash TEXT NOT NULL,
            settings_json TEXT DEFAULT '{}',
            updated_at REAL DEFAULT (strftime('%s','now'))
        );

        -- Bot API accounts. Each tied to a user; api_key authenticates bot requests.
        CREATE TABLE IF NOT EXISTS bot_accounts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            bot_name TEXT NOT NULL,   -- shown to human opponents (with bot tag)
            api_key TEXT UNIQUE NOT NULL,
            can_play_humans INTEGER DEFAULT 1,
            elo INTEGER DEFAULT 1000,
            wins INTEGER DEFAULT 0,
            losses INTEGER DEFAULT 0,
            draws INTEGER DEFAULT 0,
            games_played INTEGER DEFAULT 0,
            created_at REAL DEFAULT (strftime('%s','now')),
            last_seen REAL DEFAULT (strftime('%s','now'))
        );

        -- Finished-game replays for the replay viewer.
        CREATE TABLE IF NOT EXISTS game_replays (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            game_id TEXT UNIQUE NOT NULL,
            mode TEXT NOT NULL,            -- online|hotseat|ai|bot
            player1_id INTEGER,            -- NULL for AI/bot
            player2_id INTEGER,
            player1_label TEXT,            -- display name at time of game (incl. bot tag)
            player2_label TEXT,
            winner INTEGER,                -- 1, 2, or NULL for draw/abandoned
            turns INTEGER,
            finished_at REAL DEFAULT (strftime('%s','now')),
            actions_json TEXT NOT NULL,    -- list of action records from GameState.replay_actions
            initial_state_json TEXT NOT NULL  -- state at start of battle (after placement)
        );
    """)
    conn.commit()
    # Migrate: add email column if missing
    try:
        conn.execute("ALTER TABLE users ADD COLUMN email TEXT DEFAULT ''")
        conn.commit()
    except Exception:
        pass
    conn.close()
    _ensure_admin_password()


def _hash_password(password: str) -> str:
    salt = os.urandom(16)
    h = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 100_000)
    return base64.b64encode(salt + h).decode()


def _verify_password(password: str, stored: str) -> bool:
    data = base64.b64decode(stored.encode())
    salt = data[:16]
    stored_hash = data[16:]
    h = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 100_000)
    return hmac.compare_digest(h, stored_hash)


def _make_jwt(payload: dict) -> str:
    header = base64.urlsafe_b64encode(json.dumps({"alg": "HS256"}).encode()).decode().rstrip("=")
    body = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
    sig_input = f"{header}.{body}".encode()
    sig = hmac.new(JWT_SECRET.encode(), sig_input, hashlib.sha256).digest()
    sig_b64 = base64.urlsafe_b64encode(sig).decode().rstrip("=")
    return f"{header}.{body}.{sig_b64}"


def _decode_jwt(token: str):
    try:
        parts = token.split(".")
        if len(parts) != 3:
            return None
        sig_input = f"{parts[0]}.{parts[1]}".encode()
        sig = hmac.new(JWT_SECRET.encode(), sig_input, hashlib.sha256).digest()
        sig_b64 = base64.urlsafe_b64encode(sig).decode().rstrip("=")
        if not hmac.compare_digest(sig_b64, parts[2]):
            return None
        padding = 4 - len(parts[1]) % 4
        body = base64.urlsafe_b64decode(parts[1] + "=" * padding)
        payload = json.loads(body)
        if payload.get("exp", 0) < time.time():
            return None
        return payload
    except Exception:
        return None


def register(username: str, password: str) -> dict:
    if not username or len(username) < 2:
        return {"ok": False, "error": "username_too_short"}
    if not password or len(password) < 3:
        return {"ok": False, "error": "password_too_short"}
    conn = get_db()
    try:
        conn.execute("INSERT INTO users (username, password_hash) VALUES (?, ?)",
                      (username, _hash_password(password)))
        conn.commit()
        user = conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
        token = _make_jwt({"user_id": user["id"], "username": username,
                           "exp": time.time() + TOKEN_EXPIRY})
        return {"ok": True, "token": token, "user_id": user["id"], "username": username}
    except sqlite3.IntegrityError:
        return {"ok": False, "error": "username_taken"}
    finally:
        conn.close()


def login(username: str, password: str) -> dict:
    conn = get_db()
    user = conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
    conn.close()
    if not user:
        return {"ok": False, "error": "invalid_credentials"}
    if not _verify_password(password, user["password_hash"]):
        return {"ok": False, "error": "invalid_credentials"}
    token = _make_jwt({"user_id": user["id"], "username": username,
                       "exp": time.time() + TOKEN_EXPIRY})
    return {"ok": True, "token": token, "user_id": user["id"], "username": username}


def verify_token(token: str):
    return _decode_jwt(token)


def update_email(user_id: int, email: str) -> dict:
    conn = get_db()
    conn.execute("UPDATE users SET email = ? WHERE id = ?", (email, user_id))
    conn.commit()
    conn.close()
    return {"ok": True}


def get_email(user_id: int) -> str:
    conn = get_db()
    row = conn.execute("SELECT email FROM users WHERE id = ?", (user_id,)).fetchone()
    conn.close()
    return row["email"] if row and row["email"] else ""


def change_password(user_id: int, old_password: str, new_password: str) -> dict:
    if not new_password or len(new_password) < 3:
        return {"ok": False, "error": "password_too_short"}
    conn = get_db()
    user = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    if not user:
        conn.close()
        return {"ok": False, "error": "user_not_found"}
    if not _verify_password(old_password, user["password_hash"]):
        conn.close()
        return {"ok": False, "error": "wrong_password"}
    conn.execute("UPDATE users SET password_hash = ? WHERE id = ?",
                 (_hash_password(new_password), user_id))
    conn.commit()
    conn.close()
    return {"ok": True}


def save_game(user_id: int, name: str, game_id: str, state_json: str, is_autosave=False):
    conn = get_db()
    if is_autosave:
        conn.execute("DELETE FROM saved_games WHERE user_id=? AND game_id=? AND is_autosave=1",
                     (user_id, game_id))
    conn.execute("INSERT INTO saved_games (user_id, name, game_id, state_json, is_autosave) VALUES (?,?,?,?,?)",
                 (user_id, name, game_id, state_json, 1 if is_autosave else 0))
    conn.commit()
    conn.close()


def get_saves(user_id: int):
    conn = get_db()
    rows = conn.execute(
        "SELECT id, name, game_id, is_autosave, created_at FROM saved_games WHERE user_id=? ORDER BY created_at DESC LIMIT 20",
        (user_id,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def load_save(save_id: int, user_id: int):
    conn = get_db()
    row = conn.execute("SELECT * FROM saved_games WHERE id=? AND user_id=?", (save_id, user_id)).fetchone()
    conn.close()
    if not row:
        return None
    return row["state_json"]


def delete_save(save_id: int, user_id: int) -> bool:
    conn = get_db()
    cur = conn.execute("DELETE FROM saved_games WHERE id=? AND user_id=?", (save_id, user_id))
    conn.commit()
    deleted = cur.rowcount > 0
    conn.close()
    return deleted


def ensure_stats(user_id: int, username: str):
    conn = get_db()
    conn.execute(
        "INSERT OR IGNORE INTO player_stats (user_id, username) VALUES (?, ?)",
        (user_id, username))
    conn.commit()
    conn.close()


def update_elo(winner_id: int, loser_id: int):
    conn = get_db()
    w = conn.execute("SELECT elo FROM player_stats WHERE user_id=?", (winner_id,)).fetchone()
    l = conn.execute("SELECT elo FROM player_stats WHERE user_id=?", (loser_id,)).fetchone()
    if not w or not l:
        conn.close()
        return
    elo_w, elo_l = w["elo"], l["elo"]
    K = 32
    exp_w = 1 / (1 + 10 ** ((elo_l - elo_w) / 400))
    exp_l = 1 - exp_w
    new_w = round(elo_w + K * (1 - exp_w))
    new_l = round(elo_l + K * (0 - exp_l))
    if new_l < 100:
        new_l = 100
    conn.execute("UPDATE player_stats SET elo=?, wins=wins+1, games_played=games_played+1 WHERE user_id=?",
                 (new_w, winner_id))
    conn.execute("UPDATE player_stats SET elo=?, losses=losses+1, games_played=games_played+1 WHERE user_id=?",
                 (new_l, loser_id))
    conn.commit()
    conn.close()


def get_leaderboard(limit=50):
    conn = get_db()
    rows = conn.execute(
        "SELECT username, elo, wins, losses, draws, games_played FROM player_stats ORDER BY elo DESC LIMIT ?",
        (limit,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_player_stats(user_id: int):
    conn = get_db()
    row = conn.execute("SELECT * FROM player_stats WHERE user_id=?", (user_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


# ============================================================
# ADMIN CONFIG
# ============================================================

def _ensure_admin_password():
    """On first run, generate a random admin password, hash it, store hash in DB,
    and write the plaintext to admin_password.txt (gitignored) so Karlos sees it."""
    conn = get_db()
    row = conn.execute("SELECT password_hash FROM admin_config WHERE id=1").fetchone()
    if row:
        conn.close()
        return
    password = secrets.token_urlsafe(12)
    phash = _hash_password(password)
    conn.execute("INSERT INTO admin_config (id, password_hash) VALUES (1, ?)", (phash,))
    conn.commit()
    conn.close()
    try:
        with open(ADMIN_PASSWORD_FILE, "w", encoding="utf-8") as f:
            f.write(password + "\n")
        try:
            os.chmod(ADMIN_PASSWORD_FILE, 0o600)
        except Exception:
            pass
    except Exception:
        # File write failed (permissions?); hash is still in DB. Admin can reset via CLI.
        pass


def verify_admin_password(password: str) -> bool:
    conn = get_db()
    row = conn.execute("SELECT password_hash FROM admin_config WHERE id=1").fetchone()
    conn.close()
    if not row:
        return False
    return _verify_password(password, row["password_hash"])


def change_admin_password(new_password: str) -> dict:
    if not new_password or len(new_password) < 6:
        return {"ok": False, "error": "password_too_short"}
    conn = get_db()
    conn.execute("UPDATE admin_config SET password_hash=?, updated_at=strftime('%s','now') WHERE id=1",
                 (_hash_password(new_password),))
    conn.commit()
    conn.close()
    return {"ok": True}


def get_admin_settings() -> dict:
    conn = get_db()
    row = conn.execute("SELECT settings_json FROM admin_config WHERE id=1").fetchone()
    conn.close()
    if not row or not row["settings_json"]:
        return {}
    try:
        return json.loads(row["settings_json"])
    except Exception:
        return {}


def set_admin_settings(settings: dict) -> dict:
    conn = get_db()
    conn.execute("UPDATE admin_config SET settings_json=?, updated_at=strftime('%s','now') WHERE id=1",
                 (json.dumps(settings, ensure_ascii=False),))
    conn.commit()
    conn.close()
    return {"ok": True}


# ============================================================
# UNIT PRESETS (saved formations)
# ============================================================

def list_presets(user_id: int) -> list:
    conn = get_db()
    rows = conn.execute(
        "SELECT id, slot, name, units_json, updated_at FROM unit_presets "
        "WHERE user_id=? ORDER BY slot", (user_id,)).fetchall()
    conn.close()
    out = []
    for r in rows:
        out.append({
            "id": r["id"], "slot": r["slot"], "name": r["name"],
            "units": json.loads(r["units_json"]), "updated_at": r["updated_at"],
        })
    return out


def save_preset(user_id: int, slot: int, name: str, units: list) -> dict:
    if slot < 1 or slot > PRESETS_PER_USER:
        return {"ok": False, "error": "invalid_slot"}
    if not name or len(name) == 0:
        return {"ok": False, "error": "empty_name"}
    if len(name) > PRESET_NAME_MAX:
        return {"ok": False, "error": "name_too_long"}
    if not isinstance(units, list):
        return {"ok": False, "error": "invalid_units"}
    conn = get_db()
    conn.execute("""
        INSERT INTO unit_presets (user_id, slot, name, units_json)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(user_id, slot) DO UPDATE SET
            name = excluded.name,
            units_json = excluded.units_json,
            updated_at = strftime('%s','now')
    """, (user_id, slot, name, json.dumps(units, ensure_ascii=False)))
    conn.commit()
    conn.close()
    return {"ok": True}


def delete_preset(user_id: int, slot: int) -> dict:
    conn = get_db()
    cur = conn.execute("DELETE FROM unit_presets WHERE user_id=? AND slot=?",
                       (user_id, slot))
    conn.commit()
    deleted = cur.rowcount > 0
    conn.close()
    return {"ok": deleted}


# ============================================================
# BOT API ACCOUNTS
# ============================================================

def create_bot_account(user_id: int, bot_name: str, can_play_humans: bool = True) -> dict:
    if not bot_name or len(bot_name) > 32:
        return {"ok": False, "error": "invalid_name"}
    api_key = secrets.token_urlsafe(24)
    conn = get_db()
    conn.execute("""
        INSERT INTO bot_accounts (user_id, bot_name, api_key, can_play_humans)
        VALUES (?, ?, ?, ?)
    """, (user_id, bot_name, api_key, 1 if can_play_humans else 0))
    conn.commit()
    conn.close()
    return {"ok": True, "api_key": api_key, "bot_name": bot_name}


def list_bots_for_user(user_id: int) -> list:
    conn = get_db()
    rows = conn.execute(
        "SELECT id, bot_name, api_key, can_play_humans, elo, wins, losses, draws, "
        "games_played, created_at, last_seen "
        "FROM bot_accounts WHERE user_id=? ORDER BY created_at", (user_id,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def authenticate_bot(api_key: str) -> dict:
    conn = get_db()
    row = conn.execute("SELECT * FROM bot_accounts WHERE api_key=?", (api_key,)).fetchone()
    if row:
        conn.execute("UPDATE bot_accounts SET last_seen=strftime('%s','now') WHERE id=?",
                     (row["id"],))
        conn.commit()
    conn.close()
    return dict(row) if row else {}


def delete_bot_account(user_id: int, bot_id: int) -> dict:
    conn = get_db()
    cur = conn.execute("DELETE FROM bot_accounts WHERE id=? AND user_id=?",
                       (bot_id, user_id))
    conn.commit()
    deleted = cur.rowcount > 0
    conn.close()
    return {"ok": deleted}


def update_bot_elo(winner_bot_id: int, loser_bot_id: int):
    """Same ELO math as humans, but on bot_accounts."""
    conn = get_db()
    w = conn.execute("SELECT elo FROM bot_accounts WHERE id=?", (winner_bot_id,)).fetchone()
    l = conn.execute("SELECT elo FROM bot_accounts WHERE id=?", (loser_bot_id,)).fetchone()
    if not w or not l:
        conn.close()
        return
    K = 32
    exp_w = 1 / (1 + 10 ** ((l["elo"] - w["elo"]) / 400))
    new_w = round(w["elo"] + K * (1 - exp_w))
    new_l = max(100, round(l["elo"] - K * (1 - exp_w)))
    conn.execute("UPDATE bot_accounts SET elo=?, wins=wins+1, games_played=games_played+1 WHERE id=?",
                 (new_w, winner_bot_id))
    conn.execute("UPDATE bot_accounts SET elo=?, losses=losses+1, games_played=games_played+1 WHERE id=?",
                 (new_l, loser_bot_id))
    conn.commit()
    conn.close()


def get_bot_leaderboard(limit: int = 50):
    conn = get_db()
    rows = conn.execute(
        "SELECT bot_name, elo, wins, losses, draws, games_played FROM bot_accounts "
        "ORDER BY elo DESC LIMIT ?", (limit,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ============================================================
# GAME REPLAYS
# ============================================================

def save_replay(game_id: str, mode: str, player1_id, player2_id,
                player1_label: str, player2_label: str,
                winner, turns: int, actions: list, initial_state_json: str) -> dict:
    conn = get_db()
    conn.execute("""
        INSERT INTO game_replays (game_id, mode, player1_id, player2_id,
            player1_label, player2_label, winner, turns, actions_json, initial_state_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(game_id) DO UPDATE SET
            winner = excluded.winner, turns = excluded.turns,
            actions_json = excluded.actions_json,
            finished_at = strftime('%s','now')
    """, (game_id, mode, player1_id, player2_id, player1_label, player2_label,
          winner, turns, json.dumps(actions, ensure_ascii=False), initial_state_json))
    conn.commit()
    conn.close()
    return {"ok": True}


def list_replays(user_id=None, limit=50) -> list:
    """If user_id given, only replays where user played; else all."""
    conn = get_db()
    if user_id is None:
        rows = conn.execute("""
            SELECT id, game_id, mode, player1_label, player2_label,
                   winner, turns, finished_at
            FROM game_replays ORDER BY finished_at DESC LIMIT ?
        """, (limit,)).fetchall()
    else:
        rows = conn.execute("""
            SELECT id, game_id, mode, player1_label, player2_label,
                   winner, turns, finished_at
            FROM game_replays WHERE player1_id=? OR player2_id=?
            ORDER BY finished_at DESC LIMIT ?
        """, (user_id, user_id, limit)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def load_replay(replay_id: int) -> dict:
    conn = get_db()
    row = conn.execute("SELECT * FROM game_replays WHERE id=?", (replay_id,)).fetchone()
    conn.close()
    if not row:
        return {}
    d = dict(row)
    d["actions"] = json.loads(d["actions_json"])
    del d["actions_json"]
    return d


# ============================================================
# INIT
# ============================================================

init_db()
