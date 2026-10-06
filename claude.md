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
- The test server client runs as administrator, so the bot must too: Windows
  hides an elevated window's keypresses from (and blocks input to it from)
  non-elevated programs. Run from an administrator terminal.

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
2. Minimap reader: minimap state (normal, large, closed), bounds, player dot
3. Player on-screen position
4. Status reader: HP bar, MP bar, death, unexpected screen
5. Mob detection: animation frames, both facings, reachable filter
6. Hotkeys and logging: kill hotkey, point marker, basic log

Input check (done after stage 4, before stage 5, to confirm the input method
early): input_test.py presses jump once with pydirectinput and checks that
the name tag moved up. Run it from an administrator terminal. Result on the
test server: PASS (the client accepts pydirectinput keys).

Bot takes input:
7. Movement: open the minimap when not expanded, walk to x, jump onto a safe platform, focus
   check
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
  character on screen within a tolerance. v1 has no marked sweep ends, so the
  whole bottom floor counts.
- Sweep: walk the whole bottom floor end to end when no mob is reachable. An
  end is reached when the minimap x stops changing while walking (the map's
  walls); then turn around. This must not count as movement stuck.
- Marked points (v1): one safe spot, the minimap position where the
  character rests, marked with the Home hotkey. Pre-mapped maps come later.
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
- Minimap: only the normal-size expanded state counts as open. Large (the +
  button) and closed do not. In this client, closed leaves a bar with the map
  name and the - (greyed), + and WORLD buttons; it never disappears fully. At startup, if not open, press the minimap key and check again; stop
  if it is still not open. In stages 1 to 6 (no input), refuse to start with
  a console message asking the user to open it. During a run, if the minimap
  is not found for 3 s, release keys, press the minimap key once, and stop
  if it is still not found 3 s later.
- Unexpected screen: minimap still missing after the reopen attempt above, or
  minimap size differs from startup. Release keys, console message, log
  entry, stop. Dialog boxes are not detected directly in v1; the stuck and
  minimap checks catch them. Revisit if one causes trouble overnight.
- Death: stop and log. Detected by template-matching the "PRESS OK TO BE
  REVIVED." headline of the death dialog, near its usual spot. The dialog
  appears a couple of seconds after dying.
- HP and MP: read from how much of the red and blue status bars is filled,
  along one row of each bar (positions in config.toml). Filled = colored,
  empty = grey; anything else means the bar is covered and reads as
  unreadable. At low HP (seen from about 41%) the HP bar blinks between its
  normal look and a dark one, so brightness is ignored.
- In stages 1 to 6 an unexpected screen is shown on the overlay and printed;
  nothing stops.

## Detection approach
- All screen regions are relative to the client window, never absolute screen
  coordinates.
- The test server's game draws at 1366x768 and the client stretches it 1.5x
  with smoothing to 2049x1152. Game sprites (mobs) must be scaled 1.5x to
  match the screen. Templates cut from captured frames are already at screen
  scale.
- Minimap: the - / + / WORLD button group gives the state (normal: both blue;
  large: + greyed; closed: - greyed). In the normal state, template-match the
  inner corners of the minimap frame to get the map area, then template-match
  the player dot inside it. Store position relative to the map area. (Method
  borrowed from the open-source Auto Maple bot. Its images target a different
  game version, so only the method transfers.) Templates are in
  templates/minimap/, cut from test-server frames.
- Each read searches near the last found position first; a full-frame search
  (about 1 s) runs only when that fails, e.g. on a state change.
- Player on-screen position is separate from minimap position. The camera lags
  and clamps at map edges, so the character is not always at screen center.
  Found by matching the character's name tag (one template per character,
  templates/name_tag.png for the test Wizard). Only light grey letter pixels
  are compared, each allowed to be 1 px off (letter edges render slightly
  differently over different backgrounds), so a tag partly covered by grass
  or mobs still matches. The
  feet are 5 px above the top of the tag box, at its center. Search above the
  bottom UI only (it also shows the name). Search near the last position
  first; when the tag is covered, keep the last position for 1 s, then report
  it lost.
  Populated maps may cover the tag or contain lookalike names; revisit if
  that becomes a problem.
- Mob templates are the game's own sprites (WZ data), downloaded from
  maplestory.io (GMS v92) by fetch_mobs.py into templates/mobs/<name>/ as
  <animation>_<frame>.png, facing left. Account for multiple animation
  frames, transparency, and both facings. Mobs on the v1 map's bottom floor,
  where v1 fights ([mobs] in config): Blue Snail, Shroom. Red Snail and
  Orange Mushroom spawn elsewhere on the map; their sprites are downloaded
  but not detected.
- Mob detection (mobs.py): only the rows where a mob's feet would be within
  10 px (config) of the character's feet are searched, which is the
  reachable filter. Each sprite is scaled 1.5x in four half-pixel-shifted
  versions, plus mirrored for facing right; only stand, move and hit1 frames
  are used. A rough half-size grayscale pass on just that strip picks
  candidates, grouped per mob and spot; an exact color pass confirms each
  group. Matches run in parallel threads. About 14 ms typical, 32 ms busy.
- Live loop timing (test laptop): about 90 ms per frame, of which screen
  capture is about 45 ms and HP/MP/death about 17 ms.
- The overlay marks reachable mobs and boxes the target the bot would pick
  (nearest reachable mob), and draws the safe spot as a small box on the
  minimap.
- Stages 1 to 6 are validated with the user playing. The debug overlay shows
  detections and, from stage 5, what the bot would do.
- Frames can be saved and replayed so detection is testable without the game.
  The save-frame hotkey (Delete) writes a PNG to the run folder's frames/
  subfolder. Replay loads a folder of PNGs.
- Debug overlay (config: overlay = "game" or "window"):
  - game (default): a see-through, click-through window drawn directly over
    the client. It is excluded from screen capture (Windows 10 2004+), so the
    bot never sees its own drawings, and it never takes focus. Status text is
    at the top right. Stop with the kill hotkey (End) or Ctrl+C in the
    terminal.
  - window: a separate OpenCV window showing a scaled copy of the frame,
    placed beside the client. Replay always uses this one.
- The live watch loop pauses while the client is not in front and does not
  pull focus; the bring-to-front rule applies to saved screenshots.

## Config and files
- Entry point: python main.py live | replay FOLDER
- Settings in config.toml: window title, keybinds stored by action name (attack, skill,
  loot, chair, jump, minimap), thresholds, intervals. Hand-edited now. A GUI will edit
  the same file later.
- Marked points in a JSON file, one per map: points/<map_name>.json (map_name
  in config), written by the point-marker hotkey.
- Hotkeys (heard while the game is in front): Delete saves a frame, Home
  marks the safe spot, End is the kill hotkey. The game's quick slots Del,
  Hm and End must stay empty, since the game also receives these presses.
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
- The log is run.log in the run folder; lines are also printed. In stages 1
  to 6 it records run start, unexpected screens, deaths, safe spot marked,
  the status line every minute (state "watching") and a summary at stop
  (duration, deaths seen, unexpected screens, frames saved).

## Open items (ask, do not guess)
- Which skill; keybinds for attack, skill, loot, chair, minimap (jump is
  Alt); attack range and facing
- What the minimap key does when the minimap is large (switch to normal, or
  close it so a second press is needed); check in-game before stage 7
- Client resolution on the real server. The test server uses a custom size
  (client area 2049x1152).
- Focus lost: how many times in a row before stopping (decide at stage 7)
- How long the minimap x must stay unchanged to count as reaching the end of
  the floor (shorter than the 3 s stuck limit); decide at stage 9
- Which mobs can reach the safe platform while resting (whether Red Snail or
  Orange Mushroom detection is needed for "hit while sitting"); decide at
  stage 10
- Jump-over trigger distance
- How the bot gets onto a safe spot on a platform: where on the floor to jump
  from, which way, and how many jumps (e.g. mark a takeoff point too, or
  search for it). v1 has no rope climbing, so the platform must be reachable
  by jumping from the bottom floor. Decide at stage 7
- Overnight PC settings: sleep, lock, updates, display scaling
- From the admins: run report contents, whether the real server's client
  blocks synthetic input (the test server's does not), whether the real server's client runs as administrator (the test
  server's does)

## After v1
Pre-mapped maps (sweep ends, several safe spots) and a map recorder with
shareable map files in the project folders, rope climbing,
randomized pathing, slow training mode, anti-detection (channel change, relog,
map population check), EXP tracking. Pots and buffs: keys in the same config;
buff recast automatic from the on-screen buff icon, matched to the specific
buff so other active buffs are ignored. GUI with a hotkeys screen (HP pot, MP
pot, skills, buffs) and a focus-lost popup that does not take focus itself.