"""
Authentication – SQLite users, bcrypt passwords, JWT tokens.
"""

import sqlite3
import hashlib
import hmac
import os
import time
import json
import base64

DB_PATH = "game03.db"
JWT_SECRET = os.environ.get("JWT_SECRET", "marshal_spy_secret_2026")
TOKEN_EXPIRY = 30 * 24 * 3600  # 30 days


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
    """)
    conn.commit()
    # Migrate: add email column if missing
    try:
        conn.execute("ALTER TABLE users ADD COLUMN email TEXT DEFAULT ''")
        conn.commit()
    except Exception:
        pass
    conn.close()


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


init_db()
