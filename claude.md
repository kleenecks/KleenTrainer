# KleenTrainer

Auto-training bot for a v92 MapleStory private server. Trains one character
unattended overnight. Doubles as a bot-detection test: run results are shared
with the server admins.

## Working rules
- Do not fill in blanks. If something is not specified here, ask before
  writing code. Do not assume.
- One build stage at a time. Stop after each stage so it can be tested.
- Anything undecided belongs under Open items, never in code as a guess.
- Keep this file current. When a decision is settled, move it out of Open items.
- The user is new to git and Claude Code. Explain git steps and the reason
  for each.

## Stack
- Python 3.14 on native Windows (not WSL), virtual environment in .venv
- mss (screen capture), OpenCV + numpy (detection), pydirectinput (key input),
  pynput (hotkeys)
- ctypes (standard library) to find the client window by title
- Game client runs windowed at a fixed resolution. Test server window title:
  "Kaizen v92"
- Run from the command line with a config file. No GUI in v1.

## v1 scope
One map: Henesys Hunting Ground I. Flat ground plus platforms used as safe
zones. Melee character, one skill plus basic attack. (The test server
character is a Wizard and is used only for observe-only testing.)

In v1: player position from the minimap, player position on screen, map points
marked in-game with a hotkey, walking and jumping to a safe platform, mob
detection, looting, HP bar / MP bar / death, stuck detection, logging with
screenshots, kill hotkey.

Not in v1: map recorder, rope or ladder climbing, multi-platform navigation,
randomized pathing, GUI, pots, buffs, EXP tracking, anti-detection.

## Build order
Observe only (the user plays, the bot only watches):
0. Smoke test: find the client window by title and capture one frame of it.
   No input.
1. Capture base: regions relative to the client window, save and replay
   frames, debug overlay
2. Minimap reader: minimap bounds, player dot
3. Player on-screen position
4. Status reader: HP bar, MP bar, death, unexpected screen
5. Mob detection: animation frames, both facings, reachable filter
6. Hotkeys and logging: kill hotkey, point marker, basic log

Bot takes input:
7. Movement: input smoke test (make the character jump once), walk to x, jump onto a safe platform, focus check
8. Attack
9. Sweep and loot
10. Rest at safe zone, with the behavior priority order
11. Stuck detection, periodic screenshots, overnight hardening, run report

## Behavior priority (highest wins)
1. Kill hotkey: stop
2. Death or unexpected screen: stop and log
3. Stuck: run recovery
4. HP low: go to the safe zone and rest
5. Reachable mob: attack
6. Otherwise: sweep and loot
MP low is not a state. It only changes which attack is used.

## Behavior details
All numbers are starting values and live in the config.
- Target: nearest reachable mob. Reachable = feet at the same height as the
  character on screen within a tolerance, and inside the marked left and right
  ends of the sweep area.
- Sweep: walk the flat area end to end when no mob is reachable.
- Loot: tap the loot key during attacks and sweeps. After a kill, walk to the
  mob's last detected position while tapping loot, then pick the next target.
  Skip that walk when HP is low.
- Rest: below 25% HP, jump up to a safe platform, sit on a chair, get up when
  HP is full. On the way, jump over mobs in the path and fight nothing. If a
  jump fails, keep going.
- Hit while sitting: kill the mob, sit again.
- MP: below 10% use basic attacks. Resume the skill above 50%.
- Movement stuck: movement keys sent but minimap position unchanged for 3 s.
  Recovery: jump, then walk the opposite way for 1 s and retry. After 3 failed
  attempts, stop and log with a screenshot.
- Attack stuck: same spot attacked for 15 s with the target still detected.
  Ignore that spot for 60 s and go back to sweeping. 3 in a row: stop and log.
- Focus lost (client not in the foreground): full-screen screenshot, release
  all keys, console message, log entry, bring the client to the front and
  continue. Stop instead if Windows refuses the focus change, or if focus is
  lost too many times in a row. Every client screenshot brings the client to
  the front first; the focus-lost full-screen screenshot is taken before
  refocusing, so it shows what took focus.
- Unexpected screen: minimap not found for 3 s, or minimap size differs from
  startup. Release keys, console message, log entry, stop. Dialog box
  detection is an open item.
- Death: stop and log. Detection method is an open item.

## Detection approach
- All screen regions are relative to the client window, never absolute screen
  coordinates.
- Minimap: locate it by template-matching its corners, then template-match the
  player dot inside it. Store position relative to the minimap. (Method
  borrowed from the open-source Auto Maple bot. Its images target a different
  game version, so only the method transfers.)
- Player on-screen position is separate from minimap position. The camera lags
  and clamps at map edges, so the character is not always at screen center.
- Mob templates come from the game's WZ files. Account for multiple animation
  frames, transparency, and both facings.
- Stages 1 to 6 are validated with the user playing. The debug overlay shows
  detections and, from stage 5, what the bot would do.
- Frames can be saved and replayed so detection is testable without the game.

## Config and files
- Settings in config.toml: window title, keybinds stored by action name (attack, skill,
  loot, chair, jump), thresholds, intervals. Hand-edited now. A GUI will edit
  the same file later.
- Marked points in a JSON file, one per map, written by the point-marker hotkey.
- One folder per run under runs/, named by start date and time, holding the
  log and screenshots. runs/ is git-ignored.

## Logging
- Screenshot of the client every 5 minutes, and one on every stop. The
  focus-lost screenshot captures the full screen.
- Timestamped log lines: run start with config values, state changes, switch
  to basic attacks and back, stuck events and each recovery attempt, stop with
  reason, and a status line every minute (position, HP %, MP %, state).
- Summary at stop: duration, rests, stuck events, attack count.
- No per-keypress logging.

## Open items (ask, do not guess)
- Which skill; keybinds for attack, skill, loot, chair, jump; attack range
  and facing
- Client resolution on the real server. The test server uses a custom size
  (client area 2049x1152).
- Focus lost: how many times in a row before stopping (decide at stage 7)
- Death detection method: death dialog template, HP bar empty, or both
  (decide at stage 4)
- Dialog box detection: the stuck check misses dialogs during attacks and
  rest; detect directly or accept the gap (decide at stage 4)
- Mob list on the map
- Points to mark and their hotkeys; kill hotkey; which safe zone when several
- Jump-over trigger distance; height tolerance for reachable mobs
- On-screen position method (name tag template match proposed, not confirmed)
- Points file name
- Overnight PC settings: sleep, lock, updates, display scaling
- WZ extraction tool
- From the admins: run report contents, whether the client blocks synthetic
  input, whether the client runs as administrator

## After v1
Map recorder with shareable map files in the project folders, rope climbing,
randomized pathing, slow training mode, anti-detection (channel change, relog,
map population check), EXP tracking. Pots and buffs: keys in the same config;
buff recast automatic from the on-screen buff icon, matched to the specific
buff so other active buffs are ignored. GUI with a hotkeys screen (HP pot, MP
pot, skills, buffs) and a focus-lost popup that does not take focus itself.