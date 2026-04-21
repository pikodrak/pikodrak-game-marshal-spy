# CHANGELOG – Marshal & Spy

## 2026-04-21 – v1.0 (WIP) – Complete ruleset overhaul (engine + AI + API skeleton)

### Brand new ruleset (breaking change – v0.x saves are rejected)
- **Assassin removed**; **Hacker added** (ground, ATK 1; auto-kills Terminator in melee)
- **Mine** replaced by **Minové pole** (ATK 0; destroys any ground attacker except Engineer, who clears it; helicopter vs mine = both revealed, no kill; Fighter treats mine as non-air → wasted turn)
- **Attack drone** reworked: threshold-kill (kills units with ATK < 4); miss = drone permanently revealed + target temp-revealed for one opponent turn
- **Artillery**: no standard attack, kills any ground/special within range 2 as a **special action**
- **Fighter**: no ranged, air targets only; non-air attacks produce a "wasted turn" (`Zmařený tah`)
- **Helicopter**: movement 1 (was 2), attacks ground/air/special
- **Terminator**: ATK 8 + level (was 6)
- **Engineer**: no level scaling on attack (stays at 1)
- **Trainer**: boost +1 ATK, max 2× per target; may convert own ground (ATK > 3) into a Hacker, max 2× per game; no cooldowns
- **Corruptor**: can weaken ATK or range by 1 (per unit type), floor 1 — action is wasted if it would push below the floor; corruptor does **not** reveal itself or change target's reveal state
- **Recon/Attack drone/Corruptor/Jammer** range = 1 + placement level; **Trainer** range fixed at 1; **Artillery** range fixed at 2
- **Placement**: all hexes in level zones must be filled (or player force-confirms); special cap per level = **2 + level** (was 5 + level); 1 Terminator per level (Assassin limit gone)
- **Unit counts** are now per-player **maxima**; player chooses their composition up to these caps (Ženista 8, Vojín 10, Průzkumník 6, Výsadkář 5, Tank 4, Terminátor 1, Hacker 1, Vrtulník 4, Stíhač 4, Minové pole 8)
- **First battle turn**: the player who confirmed placement first
- **Fighter** may path through own units (cannot stop on them); no one passes through enemies
- **Citadel capture** now allowed by ground AND special units (air still excluded)
- **Inactivity / speedup**: engine exposes `request_speedup` + `check_speedup_expired` for the 2-min idle + 60-sec force-pass flow

### Engine / internals
- Bumped `engine_version` to "1.0"; old v0.x JSON is rejected cleanly (server returns `save_incompatible_v1_engine` instead of crashing)
- `Unit` has new fields: `boost_count`, `corrupt_attack`, `corrupt_range`, `temp_revealed_ticks_left`, `converted_from`; serialization handled in `to_dict`/`from_dict`
- `GameState.replay_actions` records every action for future replay UI
- New engine API: `place_new_unit(player, utype, col, row)`, `clear_placement(player)`, `apply_preset(player, [...])`, `confirm_placement(player, force=False)`, `request_speedup(player)`, `check_speedup_expired()`, `force_end_placement()`

### AI
- `ai_engine.py` fully rewritten for v1.0 units + rules; placement now fills all 51 hexes with a balanced composition (defensive L0, ranged support L1, strike force L2)
- Scoring adjusted for hacker vs terminator auto-win, drone threshold, artillery special, fighter wasted turns, corruptor value

### Server API
- `POST /api/game/{id}/place` now takes `{utype, col, row}` instead of `{unit_id, col, row}`
- `POST /api/game/{id}/clear_placement` – wipes all of player's placed units in phase 1
- `POST /api/game/{id}/apply_preset` – applies a preset `[{utype, col, row}, …]`
- `POST /api/game/{id}/confirm` accepts `{force: bool}` to confirm with empty hexes
- `POST /api/game/{id}/speedup` – opponent requests forced-pass countdown
- Hotseat endpoint mirrors all new actions (`place`, `clear`, `preset`, etc.)

### DB schema additions (Phase 3)
- `unit_presets` – up to 5 saved formations per user (12-char name + unit list)
- `admin_config` – single-row table guarding admin UI (bcrypt-style hash); on first boot the server generates a random password, stores the hash, and writes the plaintext to `admin_password.txt` (chmod 600, gitignored)
- `bot_accounts` – bot API identities owned by human users; each has unique `api_key`, separate bot ELO + W/L/D, and `can_play_humans` toggle
- `game_replays` – finished-game archive with initial state + ordered action list for the replay viewer
- Helper functions in `auth.py`: `verify_admin_password`, `change_admin_password`, `get/set_admin_settings`, `list/save/delete_preset`, `create/list/delete_bot_account`, `authenticate_bot`, `update_bot_elo`, `get_bot_leaderboard`, `save/list/load_replay`
- `.gitignore` updated for `admin_password.txt` + SQLite WAL/SHM files

### Phase 4 – Server endpoints backing the new DB
- `GET/POST/DELETE /api/presets[/{slot}]` – CRUD for the 5 saved formations (12-char names, full unit list)
- `POST /api/admin/login` (+ optional `/api/admin/change_password`) – returns a short-lived admin token
- `GET /api/admin/unit_defs` – shows current unit definitions and any active overrides
- `POST /api/admin/unit_override` / `DELETE /api/admin/unit_override/{utype}` – edit or clear per-type overrides (base_attack, movement, base_range, max_count, category)
- `GET /api/replays?mine=true|false` + `GET /api/replays/{id}` – list and load saved replays
- Every finished game is now archived to `game_replays` (human + bot label, winner, turn count, full action log, battle-start snapshot)

### Phase 9 – Bot API (external bots) + documentation
- `GET/POST/DELETE /api/bot-accounts[/{id}]` – human user creates bot accounts; each gets a unique `X-API-Key`
- `GET /api/bot-leaderboard` – separate bot ELO ranking (not mixed with humans)
- `POST /api/bot/games { opponent: "ai" | "queue" | "human" }` – bot creates a game; `queue` enters a bot-vs-bot waiting queue with 30-second fallback to built-in AI via `POST /api/bot/queue/fallback/{game_id}`
- `GET /api/bot/games/{id}/state` and `POST /api/bot/games/{id}/{place|clear_placement|apply_preset|random_place|confirm|move|attack|special|pass|replay}` – full bot action surface
- `API.md` rewritten from scratch for v1.0 – 12 sections, every endpoint documented with JSON examples, plus a minimal Python example of a working bot loop
- Engine now snapshots `battle_start_state` at `_start_battle` for compact replay storage

### AI tuning round 1
- Lowered Trainer boost base score (12/8/4 → 9/5/2) with a late-game decay so boosts stop being the default pick after turn 15
- Lowered "convert to Hacker" score when no terminator is visible (18 → 1) so it doesn't auto-convert when there's nothing to counter
- Ground rushers (Terminator/Tank/Paratrooper/Scout/Private) get a late-game advance multiplier (`+0.6 per turn past turn 10`) and a +3 "adjacent to enemy" bonus for setup-to-attack
- AI-vs-AI smoke: 5 games @ 200 turns → **3 P1 / 1 P2 / 1 draw** (was 0/0/5 before tuning)

### Data migration (v0.x → v1.0)
- One-off cleanup script removed **21 incompatible `active_games` rows** that still carried v0.x engine state (verified by checking each row's `engine_version`)
- `users`, `player_stats` (ELO), and `saved_games` are kept untouched per the user's request
- v0.x `saved_games` remain in DB but will return `save_incompatible_v1_engine` on load; user can delete via the existing `DELETE /api/saves/{id}` if desired

### Phase 5–8 – Frontend v1.0 (symbolic / placeholder graphics, Karlos will reskin)
- `static/index.html` rewritten from scratch for v1.0 API (from ~1800 lines to a single self-contained SPA)
- CSS design tokens at the top of `<style>` – tweak them to reskin the whole app
- **Screens** implemented: auth (login/register tabs), lobby, game (placement + battle), formations editor, replay viewer, admin, bot accounts, manual (HOW TO PLAY)
- **Placement UI**: click hex → pick unit type from right-side picker (16 types in a 4-column grid with per-type counter `used/max`); click already-placed unit to unplace (if no type selected) or replace (if type selected); valid hexes are cyan-highlighted when a type is picked; Artillery restricted to L0 is auto-enforced. Forces per-level and per-type cap errors are surfaced via toasts
- **Formation loader** (5 presets) appears in-line above the picker; one-click loads a formation into the current game's placement
- **"Uložit jako formaci"** prompts for slot + name and posts to `/api/presets`
- **Battle UI**: right-panel shows info panel (top half) + log (bottom half); action bar has Move / Attack / Special / Pass / Speedup buttons
- **Unit rendering on hex**: body color = level (pale blue/green/orange), outer border = reveal state (green concealed / yellow revealed), inner red ring if corrupted, small triangular flag in top-left corner (blue = yours, red = opponent), centered text = `abbr` + `attack`, hidden enemies render as `?`
- **Mobile**: right panel hidden entirely; placement uses a horizontal swipe bar of unit buttons above the board (per 2026-04-21 decision); no info panel on phones
- **"Jsi na tahu / Tah soupeře"** banner in the top bar (green / red)
- **Zrychlení hry**: button appears when you're waiting and opponent has been idle ≥ 2 min; countdown badge shown when the deadline is running against you
- **Replay viewer**: lists your finished games (`/api/replays?mine=true`); selecting one loads initial state + action list; seek bar + 1×/2×/4× speeds + step forward/back + play/pause. (v1 limitation: events are shown as a list as the slider moves; the board itself is not re-applied frame-by-frame – that's a follow-up.)
- **Admin section**: password login (token cached in localStorage, 1 h expiry), then a table of all 16 unit types where base_attack / movement / base_range / max_count are editable; yellow highlight = override active; per-row "Uložit" / "Clear overrides". "Změnit heslo" dialog also exposed.
- **Bot accounts**: list + create (shows generated `api_key` in an alert so user can copy), delete; separate bot leaderboard below; link to `/static/API.md` for bot developers
- **Manual**: updated with v1.0 rules, unit table, and key mechanic cheatsheet

### Known limits of this pass (for Karlos to polish)
- Graphics are symbolic: colored hexes + abbreviations, no sprites yet. `--p1-flag`, `--p2-flag`, `--level-{0,1,2}-body`, `--concealed`, `--revealed`, `--corrupted` tokens are in `:root` for easy reskinning.
- Replay does not animate the board (only the event list advances); a follow-up will re-apply each action to reconstruct mid-game frames.
- Hotseat flow is reachable from the lobby but uses the single-player placement UI (works, but cramped for two people on one device).
- `run_sim.py` not extended with `--parallel` / external-HTTP AI support. External AI can already play via `/api/bot/*` (see `API.md` §12).

### Polish pass (same day)
- **Canvas now reads every `--var` from `:root`** (level colors, reveal/concealed borders, corrupted inner ring, player flags, zone tints, accents) – reskin by changing CSS variables and the board re-paints on next resize. Level-zone backgrounds auto-derive from the body-color tokens via a built-in `darken()` helper.
- **Selected unit tracked by ID** across the 2-second state refreshes, so your selection doesn't blink off when the poll lands.
- **Intent buttons** (Pohyb / Útok / Speciál) now show `primary` highlight for the active intent, clicking an own unit auto-picks a sensible default (attack > special > move) based on what's available.
- **Info panel** stays on the selected unit after you hover away; only overridden while the cursor is over another unit.
- **Replay viewer** now actually re-paints the board. New endpoint `GET /api/replays/{id}/snapshot?at=N&as_player=1|2` rebuilds state from the initial snapshot by applying N recorded actions; the client fetches this on every scrub/step/play tick so the hex grid animates through the match. Events list shows human-readable labels ("pohyb na (7,8)", "útok attacker_wins", …).
- **Replay storage slimmer**: placement actions are filtered out when saving; the initial snapshot already holds placements, and only the `move|attack|attack_wasted|special|pass|turn_timeout` actions go into `actions_json`.
- **AI tuning round 2** – late-game attrition bias on attack scoring (`+0.5·(turn-10)` for known kills, trades, and unknown jabs); wasted fighter/mine attacks pushed further into the negatives; ground rushers' adjacent-to-enemy bonus paired with the earlier advance multiplier now finishes games reliably. 8-game smoke: **5 P1 / 3 P2 / 0 draws, avg 92 turns** (was 170-turn stalemates).
- **`run_sim.py --parallel N`** spawns a multiprocessing pool; 8 games at 4 workers ran in 28 s wall-time (was 86 s serial).

### Movement buff (Karlos decision 2026-04-21 afternoon)
- **Scout** movement 2 → **3** (rychlý průzkum, přejde bitevní pole v jednom tahu)
- **Tank** movement 1 → **2** (rychlý průlom)
- **Výsadkář** movement 1 → **2** (rychlý nájezdník)
- 30-game smoke: 14 P1 / 16 P2 / 0 draws, avg **71 tahů** (bylo 86) – hry o 17 % svižnější, balanc P1/P2 zůstává ~50/50

### LLM-agent readiness (Claude / any external LLM)
- **`GET /api/bot/rules`** (no auth) – single JSON blob (~9 kB) with the full game description: board topology, zones, placement rules, battle rules, all 16 unit defs, event-type glossary, and a `recommended_agent_workflow`. Drop it straight into an LLM system prompt and the agent knows the game without parsing `API.md`.
- **`rationale` field** added to every bot action (`/move`, `/attack`, `/special`, `/pass`). Server stores it on the matching `replay_actions` entry → persistent audit trail of "why did the agent do X".
- **Enriched game log** – `game_logs/{id}.json` now includes `replay_actions` (with rationales), `battle_start_state` snapshot, `{p1,p2}_alive_by_type`, `hacker_conversions`, `first_confirmed`, `ply`, `engine_version`, `bots` map. Ideal for post-mortem LLM review.
- **`API.md` §13 Claude agent template** – complete working system-prompt + Python runtime loop using the Anthropic SDK, prompt-caching tip for the rules block, and description of what ends up in the game log for analysis.

### Heuristic external agent (`agent/` package)
- New `agent/` module: a self-contained heuristic bot that plays vs the built-in CPU AI through the bot API. Serves as a baseline and a starting point for further tuning/learning experiments.
- Files: `agent/player.py` (9-priority decision tree), `agent/config.py` (all tunable parameters — defense radius, boost priorities, stall thresholds, scoring weights), `agent/run_game.py` (bootstrap + orchestration), `agent/README.md` (usage + tuning guide)
- Persistent state at `agent/state/` (gitignored): `current_game.json`, `agent_state.json` (cumulative kills, stall tracking, action counts), `agent_log.txt`
- CLI: `python -m agent.run_game --new` bootstraps a fresh user + bot + game vs AI with the curated 51-hex formation and plays until done. `--no-play` just sets up. `python -m agent.player --turns N` plays N turns of the current game.
- Smoke-test: first end-to-end run from fresh install finished game at T93 with **10:0 kills** for the agent
- Designed for iteration: strategy knobs live in `config.py`; the README documents what each value does and how to tune (stall detection, attack strictness, rusher priorities, boost targets, etc.)

### Coming next (owner decisions pending)
- Karlos's graphics pass (sprites / icons per unit, richer level colors, flag art, fonts)
- Combat animations (hit flash on kill)
- Artillery balancing (2 artillery = 195 kills / 50 games — possibly too strong)
- AI trade-avoidance tuning for expensive units
- Automatic turn-expiry background task for speedup flow
- Agent improvements: multi-game parameter search, LLM fallback on tough turns, look-ahead simulation, opponent type inference from movement patterns

## 2026-04-11 – v0.10 – Unit Info, Combat Feedback & AI Overhaul

### Unit info tooltip
- Right-click (PC) or long-press (mobile) on any unit shows info popup
- Shows unit name, type, ATK, MOV, and special ability description
- Enemy hidden units show "Unknown" with hint to use Recon Drone

### Combat result toast
- After each action, new combat log entries appear as toast messages
- Player now sees what happened when a unit dies (e.g. mine, lost combat)

### Movement trail improvements
- Hexagon outline now shown at BOTH origin and destination of last move
- Arrowhead in the middle of the dashed trail line showing direction of movement

### AI overhaul (v2)
- **Placement**: Strategic level assignments (rush force → L2, defense → L0, support → L1)
- **Placement**: Units positioned within levels (combat front, mines center, support back)
- **Movement**: Role-based advance (assassin/terminator/tank rush at 8x, privates at 4x)
- **Movement**: Air units harass instead of rushing citadel (can't capture)
- **Movement**: Anti-clustering penalty prevents traffic jams
- **Movement**: Support units stay near combat allies instead of rushing
- **Attack**: Ranged shots against unhittable targets now score negative (fixes 91% miss rate)
- **Attack**: Path-clearing bonus near enemy citadel
- **Attack**: Assassin/terminator now engage unknowns near citadel
- **Specials**: Boost prioritized by proximity to combat, not just unit value
- **Specials**: Reveal prioritized for threats near own citadel
- **Defense**: Private placed near citadel blocks scout rush (was 22% instant wins)
- **Helicopter**: Won't waste turns attacking unhittable targets (fixes 255-attack loops)
- **Assassin**: Advances behind allies, placed at L1 for protection (was 4 kills / 34 deaths)
- **Mines**: Randomized positions (was always same 2 hexes)
- **Results**: P1 45%, P2 50%, Draw 5% (was P1 70%, P2 30%, Draw 0%)
- **Results**: Avg 65 turns, games are more strategic with balanced outcomes

## 2026-04-10 – v0.9 – Narrowed Battlefield

### Board change
- Battlefield rows (7-9) shrunk by 1 hex on each side
- Battlefield is now NARROWER than Level 2 (creates bottleneck)
- Row 7: 14 hexes, Row 8: 15 hexes, Row 9: 14 hexes
- Level 2 remains 12-13 hexes wide
- 147 total hexes (was 153)
- Still perfectly symmetric (all centers at 8.0)

## 2026-04-10 – v0.8 – Symmetric Diamond & Auto-fit

### Board symmetry fix
- Odd rows now get +1 hex on the left side to compensate for hex offset
- All row centers align at exactly the same X position (col 8.0)
- Diamond is now perfectly symmetric visually
- 153 hexes total (was 145)

### Auto-fit zoom
- Board automatically scales to fill 95% of available canvas
- Works on any screen size (mobile, desktop, fullscreen)
- Manual zoom (scroll wheel) overrides auto-fit
- Home key resets to auto-fit

### Canvas width fix
- `#game-screen { align-items:stretch }` – game container fills full width
- `#game-container { width:100% }` – explicit full width
- Desktop: side panel stays visible during battle

## 2026-04-10 – v0.7 – UI Polish & Profile

### Animation sequence (corrected)
- Player move → 500ms pause → camera pan to enemy (600ms) → THEN enemy moves (350ms)
- Camera pan completes BEFORE enemy unit starts moving
- `panToHex` now supports `onDone` callback for sequencing

### UI improvements
- **Fullscreen** button (⛶) in game top bar, works on PC and mobile
- **Custom abort dialog** – styled overlay with YES/NO instead of browser confirm
- **Delete saved games** – DEL button next to each save in lobby
- **Profile redesign** – click username to expand settings: password change, email, logout, changelog link
- **Email field** – optional email in profile settings, saved to DB

### Backend
- `DELETE /api/saves/{id}` endpoint
- `POST /api/auth/email` endpoint
- Email column migration for existing DB (ALTER TABLE)

## 2026-04-10 – v0.6 – Sequenced Animations

### Animation overhaul
- Player's move animates FIRST (350ms)
- Then 500ms pause
- Then camera pans to enemy's unit (600ms)
- Then enemy's unit animates to new position (350ms)
- Death flashes tied to correct phase
- 3-phase system: phase 1 (player) → phase 2 (pause) → phase 3 (enemy)

## 2026-04-10 – v0.5 – Full Feature Update

### Bugfixes
- Victory overlay: fixed re-showing loop, PROCEED button now works (JS listener)
- Canvas width: panel uses display:none (instant), proper resize after hide
- Panel hide/show with dedicated functions, no CSS transition conflicts

### New features
- **Camera follow enemy**: after player's move, 0.5s delay → smooth camera pan to enemy's moving unit
- **Unit movement animation**: 350ms easeInOutQuad slide + death flash
- **Last-move highlight**: pulsing ring + dashed trail per player
- **Profile & password change** in lobby
- **Save/Load games**: manual save with custom name, load from lobby
- **Autosave**: every turn automatically saved (1 autosave per game)
- **SAVE button** in game top bar
- **CHANGELOG** link in lobby footer
- **Detailed AI decision logging**: every turn logs what AI sees, all options scored, top 5 options, chosen action with reasoning
- AI decision logs included in game_logs JSON files

## 2026-04-10 – v0.4 – Map Resize & Simulation

### Map changes
- Battlefield reduced from 5 to 3 rows (rows 7-9)
- Total grid: 17 rows, 145 hexes (was 19 rows, 181 hexes)
- Mountains repositioned to (4,7), (12,7), (4,9), (12,9)
- Citadels at (8,0) and (8,16)
- Not a perfect diamond but intentional per design

### Simulation & Logging
- `run_sim.py` – CLI simulation runner for AI vs AI games
- Full game logging to `game_logs/` directory (JSON per game)
- Logs include every action, placement, combat result for analysis

## 2026-04-10 – v0.3 – Game Rules & UX Fixes

### Rules corrections
- Placement only into Level zones (citadel and battlefield blocked)
- Special units limit per level: 5 + level number (L0=5, L1=6, L2=7)
- Max 1 Terminator and 1 Assassin per level
- All ranged/recon abilities: range = 2 + placement level (dynamic)

### Features
- **Random Deploy button** – auto-places all units + auto-confirms
- **Auto-advance placement** – after placing a unit, next unplaced auto-selects
- **View flip** – player's territory always rendered at bottom, attacking upward
- **ELO ranking system** – 1000 starting, K=32, leaderboard in lobby
- **Leaderboard API** – `GET /api/leaderboard`, `GET /api/stats/{user_id}`

### Bugfixes
- AI placement no longer tries citadel hex (caused 22/23 placement)
- Confirm button shows only when all units are placed
- Camera centering works correctly with flipped view

## 2026-04-10 – v0.2 – Futuristic UI & Sprite System

### UI overhaul
- Futuristic military theme (Orbitron + Share Tech Mono fonts)
- Dark color scheme with neon accents (cyan/red/gold)
- Sprite system: load from `/static/img/units/{type}.png`, auto-fallback to canvas

### Backend
- FastAPI server on port 8030
- SQLite persistence (users, games, stats)
- JWT authentication (30-day tokens)
- AI opponent (scoring-based decision making)

## 2026-04-10 – v0.1 – Initial Implementation

### Core game engine
- Diamond hex grid: 19 rows, 181 hexes, 4 fixed mountains
- 17 unit types: 7 ground, 2 air, 8 special
- 23 units per player
- Fog of war (hidden units, reveal/conceal mechanics)
- Jammer protection against scouting and combat revelation
- Special combat rules: Assassin vulnerability, Helicopter safe attacks, Mine traps
- Special abilities: Engineer (conceal/defuse), Recon (reveal), Trainer (boost),
  Corruptor (weaken), Artillery (ranged ground), Attack Drone (ranged all)
- Win condition: capture enemy citadel with ground unit

### Game modes
- VS AI (single player)
- Online PVP (2 players via server)
- Hot-seat (2 players, 1 device)

### API
- REST endpoints for all game actions
- Player view filtering (fog of war server-side)
- Available actions computed per turn
