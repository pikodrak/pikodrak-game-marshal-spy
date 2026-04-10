# CHANGELOG – Marshal & Spy

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
