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
- Marked points (v1): safe spots, minimap positions where the character can
  rest (on the floor or on a platform). Home adds a spot at the character's
  position, or moves an existing spot within 5 minimap px (config). Page Down
  removes the spot nearest the character. When the bot needs to rest it goes
  to the closest spot by walking distance: horizontal minimap distance to the
  spot's takeoff point (or the spot itself before a route is known).
  Pre-mapped maps come later.
- Getting onto a platform spot: no marking; the bot searches (movement.py).
  Takeoff spots are tried nearest first, 2 minimap px apart, up to 30 px to
  each side: on the starting level around the spot's x, on a level partway up
  around where the character landed. First running jumps toward the spot
  (back up 6 minimap px, config runup, then jump while still walking when
  passing the takeoff spot): they clear mobs in the way, where a standing
  jump next to a mob got knocked down in the game. Then jumps straight up
  (platforms can be jumped up through from below), then standing jumps
  toward the spot. After each landing check the minimap height: at the
  spot's height = done; higher = keep searching from that level; lower =
  fell, search again from there. Landing a little above the spot's height
  (sloped platforms) is fine: the height that counts is the one at the
  spot's x (the old "ended up above the safe spot" stop ended a run in the
  game). Already on the spot's level (e.g. after a fight while resting):
  just walk to it. Walking off a level's edge is remembered,
  so later tries on that level stay inside it. Give up after 60 tries
  (config): log and stop. The route that worked (each takeoff x, jump
  direction and landing height) is saved with the spot in the points file and
  replayed next time; the search only runs again if the replay does not land
  where expected. Moving a spot clears its route.
- Walking: hold the arrow key toward the target x, pulse it within 4 minimap
  px to avoid overshooting, stop within 1 px. No progress for 3 s = the walk
  fails. A jump has landed when the minimap height has not changed for
  0.25 s, at least 0.5 s after the jump (config land_still_s, min_air_s; a
  count of 3 equal readings mistook the top of a jump for a landing at the
  higher frame rate). A moving jump's arrow is let go 0.15 s after the jump
  (config air_hold_s): the jump keeps its sideways momentum in the air, and
  holding the arrow until touchdown walked the character past the safe spot
  (and off narrow platforms) after landing. Raise it if running jumps fall
  short.
- Fight and loot loop (trainer.py, rebuilt simply after the rule-on-rule
  version got erratic): (1) no target and no loot waiting: first the side:
  each reachable mob weighs (2 - HP share) / (1 + distance / 150 px)
  (config side_falloff_px), and the bot goes to the side (left or right)
  whose mobs weigh more, so a crowd farther away outweighs a single close
  mob, and a very close mob outweighs a couple of distant ones (one at
  30 px beats two at 200 and 300 px; five at 400-800 px beat one at 50 px).
  Then on that side: the lowest-HP mob among those at most 300 screen px
  (config prefer_hurt_within_px) farther than the nearest there, nearest
  first among equals (mobs take several hits, so wounded ones get finished
  off, but not by walking across the map). A wounded mob nearby can lose
  to a crowd on the other side (60% HP at 81 px vs six unhurt to the
  right); raise its weight if that should not happen. (2) fight it until it
  is dead; while still walking to it, a mob 100 screen px (config
  retarget_margin_px) closer, no healthier and in the direction of travel
  takes over and the far target is forgotten (e.g. mobs spawning nearer);
  once attacking, the target is kept; (3) loot: walk with Z held
  through every spot where a mob died and 40 screen px (config loot
  past_px) beyond the furthest, in the direction of the drops (the way the
  character faces if they are right under it); (4) a mob between the
  character and the loot (or on top of the character) is fought first, then
  the walk goes on and collects both drops, which also covers stacked mobs;
  (5) nothing to fight or loot: sweep. Looting is skipped when HP is below
  25%. Each kill and each finished loot walk is logged.
- The loot key (Z) is held whenever the character walks (to a target,
  looting, sweeping) and is never tapped or pressed while attacking. Windows
  does not auto-repeat keys held by a program, and picking up reacts to each
  key-down, so while Z is held a background thread re-sends its key-down
  about 30 times a second without releasing it (once per loop step missed
  loot).
- Drop and mob positions are kept as map positions in screen px (minimap x
  times 16.2 screen px per minimap px, plus the screen offset), so camera
  movement does not shift them. 16.2 (config [map]) is from the map data:
  minimap area 2317 game px wide shown as 214 minimap px, times the 1.5x
  stretch.
- Sweep wall time: the minimap x unchanged for 1 s while sweeping = a wall.
- Edge margins: the bot stays 20 minimap px (config) away from each side of
  the minimap's map area, since the character gets lost at the very edges.
  Sweeping turns around there, mobs beyond it are not targeted, and loot
  passes stop there.
- The training loop is trainer.py; python main.py train runs it (fight, loot,
  sweep; resting comes in stage 10).
- Map data from maplestory.io (GMS v92, map 104040000) lists the map's mob
  spawns, platforms (footholds) and ropes. Bottom floor (spawn height 215):
  Shroom and Blue Snail only. Higher levels: Red Snail, Pig, Blue Snail,
  Shroom. No Orange Mushroom on this map (the orange-capped mobs are
  Shrooms). Useful for automatic map setup later.
- Rest: below 25% HP (priority above fighting and sweeping), go to the
  closest safe spot (stage 7 movement: saved route or search), sit on the
  chair (key Y), get up with a jump (so the character does not walk off the
  platform) when HP is at least 98% (config hp_full_percent; the bar
  reading can stop a hair short of 100). On the way, jump when a mob is
  within 60 screen px ahead (config jump_over_px) and fight nothing; a
  failed jump just carries on walking. If no safe spot can be reached, the
  run stops (move failed) rather than fighting on at low HP. Rests are
  counted in the summary. Being hit blocks the chair for a while (seen in
  the game: the press did nothing and the bot waited indefinitely), so the
  bot presses the chair key only once HP has not dropped for 3 s (config
  chair_wait_s), and presses again if HP is not rising 2.5 s later (config
  chair_check_s; was 5 s, too slow in the game; must stay longer than one
  HP step while sitting, since a re-press while sitting may stand up).
- Hit while sitting: a mob detected at the character's height while
  resting is fought (get up, kill it), then the bot goes back to the spot
  and sits again. Only the detected mobs ([mobs]: Blue Snail, Shroom) are
  seen; Red Snails or Pigs reaching a safe spot would not be.
- MP: below 10% use basic attacks. Resume the skill above 50%.
- Attack (attack.py; v1 for now: basic attack only, key Ctrl; the skill and
  the warrior's values come later). One target at a time, given by the
  trainer. Out of range: walk toward it. Facing away (the character faces
  the way it last moved): tap the arrow toward it. In range and facing it:
  hold Ctrl (the game repeats the swing). Range: screen px between the feet
  (config range_px; 90 hit, 120-150 tried, now 130 set by the user). While
  walking in, the range check can lead the target (config lead_s; frames
  are about 0.2 s old by the time a key takes effect). A 0.2 s lead stopped
  the character walking into approaching mobs but sometimes started swings
  that missed completely; now 0 (no prediction) to compare. Once attacking
  it keeps at it while the target is within the range plus 10 px (config
  hold_margin_px), so stopping to swing does not flip it back to walking.
  The target is recognized from frame to frame by its map position, so
  camera movement or a slow frame does not lose it. Dead = seen during the
  attack, then unseen for 0.2 s (config target_lost_s; a dying mob vanishes
  from detection, so this is also the pause after a kill; if still
  noticeable, detecting the death animation would make it instant). Not
  seen for 0.5 s otherwise (config lost_s): given up. Each change of action
  can be logged (config debug_log).
- Dropped in the rebuild (they interfered with each other): following a
  lost target for 1.5 s, the 0.4 s keep-attacking timer, and the separate
  stacked-mob rule. Switching to a closer mob while walking came back as
  the single retarget rule above, with a larger margin (100 px, was 40).
- Partly covered mobs: a mob that fails the normal match is still accepted
  if at least 80% of its solid pixels match (config min_match_share; each
  pixel within 30 per color channel). Measured: Blue Snails under a portal
  tooltip 0.97 (now found), mobs half behind another mob, the character's
  weapon or an item (now found), a Red Snail against the Blue Snail sprite
  0.68 (still rejected). Still missed: a Shroom 40% under loot (0.59, below
  the Red Snail, so no single cutoff separates them) and a mob being hit
  behind the character and swing effects (0.06-0.18).
- Mob HP (mobs.attach_hp): a hurt mob shows an HP bar centered about 95 px
  above its feet, about 75 px wide: a grey/white frame, inside green for HP
  left and black for HP gone. A mob without a bar is unhurt (1.0). The bar
  is read in a box above each detected mob: the row of green-or-black
  pixels with the grey frame just above it (plain black is not a bar:
  region capture fills uncaptured areas with black). Bars fade when a mob
  has not been hit for a while, dimming every color, so green means
  green-dominant, not bright. ~1-2 ms per frame. Region capture's strip
  reaches 130 px above the feet to include the bars. The overlay shows the
  target's HP.
- Known limitation, accepted for v1: a mob that spawns on top of the
  character is hidden by the character's body, weapon and effects and is
  not detected until it steps out. A prototype scoring only pixels outside
  the character's box failed (hidden mobs 39-57, empty spots 37-63; ~60 ms
  per frame). A possible fallback, not built: HP dropping with no mob
  detected nearby = a hidden mob touching the character; swing facing,
  then turn.
- Movement stuck: movement keys sent but minimap position unchanged for 3 s.
  Recovery: jump, then walk the opposite way for 1 s and retry. After 3 failed
  attempts, stop and log with a screenshot.
- Attack stuck: same spot attacked for 15 s with the target still detected.
  Ignore that spot for 60 s and go back to sweeping. 3 in a row: stop and log.
- Focus lost (client not in the foreground): full-screen screenshot, release
  all keys, console message, log entry, bring the client to the front and
  continue. Stop instead if Windows refuses the focus change, or if focus is
  lost 3 times in a row (config; "in a row" = without a full minute of normal
  running in between). Every client screenshot brings the client to
  the front first; the focus-lost full-screen screenshot is taken before
  refocusing, so it shows what took focus.
- Minimap: only the normal-size expanded state counts as open. Large (the +
  button) and closed do not. In this client, closed leaves a bar with the map
  name and the - (greyed), + and WORLD buttons; it never disappears fully.
  The minimap key is M. M closes a large minimap. At startup, if not open,
  press M and check again (if it was large: M twice, since the first press
  closes it); stop if it is still not normal. In stages 1 to 6 (no input), refuse to start with
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
  where v1 fights ([mobs] in config): Blue Snail, Shroom. Red Snail and Pig
  spawn higher up on the map. Red Snail and Orange Mushroom sprites are
  downloaded but not detected (Orange Mushroom turned out not to be on this
  map).
- Mob detection (mobs.py): only the rows where a mob's feet would be within
  10 px (config) of the character's feet are searched, which is the
  reachable filter. Each sprite is scaled 1.5x in four half-pixel-shifted
  versions, plus mirrored for facing right; only stand, move and hit1 frames
  are used. A rough half-size grayscale pass on just that strip picks
  candidates, grouped per mob and spot; an exact color pass confirms each
  group. Matches run in parallel threads. About 14 ms typical, 32 ms busy.
- Live loop timing (test laptop): about 90 ms per frame with full-frame
  capture, of which capture was about 35-45 ms. Capture (mss) costs about
  6 ms per call plus its pixels (full frame ~34 ms, the full-width strip
  ~12 ms), so region capture (config [capture]) grabs only the strip around
  the feet (whole width, so distant mobs are seen) and the minimap every
  frame, plus the HP/MP bars and death dialog every 5th frame: ~26 ms per
  frame. A full frame is still taken when the name tag or minimap must be
  searched for again, for saved frames, for the window overlay, and every
  second. The frame rate cap is 20 (capture_fps) so the loop does not idle
  between frames. A much faster capture would need Windows' GPU capture
  (e.g. the dxcam package; a new dependency, not checked on Python 3.14).
- The overlay marks reachable mobs and boxes the target the bot would pick
  (nearest reachable mob), and draws the safe spot as a small box on the
  minimap.
- Stages 1 to 6 are validated with the user playing. The debug overlay shows
  detections and, from stage 5, what the bot would do.
- Frames can be saved and replayed so detection is testable without the game.
  The save-frame hotkey (Delete) writes a PNG to the run folder's frames/
  subfolder, always from a full capture of its own (with region capture,
  saving the analyzed frame could save only the regions). Replay loads a
  folder of PNGs.
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
- Entry point: python main.py live | replay FOLDER | walk X | safe. walk and
  safe press game keys (stage 7 tests): walk to minimap x X, or go to the
  closest safe spot. train fights, loots and sweeps until the kill hotkey.
- Settings in config.toml: window title, keybinds stored by action name (attack, skill,
  loot, chair, jump, minimap), thresholds, intervals. Hand-edited now. A GUI will edit
  the same file later.
- Marked points in a JSON file, one per map: points/<map_name>.json (map_name
  in config), written by the point-marker hotkey.
- Hotkeys (heard while the game is in front): Delete saves a frame, Home
  adds a safe spot, Page Down removes the nearest safe spot, End is the kill
  hotkey. The game's quick slots Del, Hm, Pdn and End must stay empty, since
  the game also receives these presses.
- Movement keys: Left and Right arrows. Jump: Alt. Minimap: M. Attack: Ctrl.
  Loot: Z. Chair: Y (the GUI's hotkeys screen must include it later).
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
- Log timestamps have milliseconds (to time short pauses).
- The log is run.log in the run folder; lines are also printed. In stages 1
  to 6 it records run start, unexpected screens, deaths, safe spot marked,
  the status line every minute (state "watching") and a summary at stop
  (duration, deaths seen, unexpected screens, frames saved).

## Open items (ask, do not guess)
- Which skill and its key (later; v1 uses basic attack for now); the
  warrior's attack range
- Client resolution on the real server. The test server uses a custom size
  (client area 2049x1152).
- Overnight PC settings: sleep, lock, updates, display scaling
- From the admins: run report contents, whether the real server's client
  blocks synthetic input (the test server's does not), whether the real server's client runs as administrator (the test
  server's does)

## Findings for the admins (run report)
- 2026-10-06: the test server's bot detection started flagging the trainer
  during stage 9 testing. Suspected (not confirmed) signal: very regular
  input timing; the loop runs at a fixed 10 Hz and the held loot key's
  key-down is re-sent at that steady rate. Anti-detection stays out of v1;
  change input timing only if the admins ask for an evasion test.

## After v1
Mapping as its own part of the tool, separate from the trainer: marking
safe spots (and sweep ends, other points) moves there. The trainer detects
which map it is on by itself and loads that map's safe spots, routes and
mob list automatically. Map files are shareable, in the project folders.
Rope climbing,
randomized pathing, slow training mode, anti-detection (channel change, relog,
map population check), EXP tracking. Pots and buffs: keys in the same config;
buff recast automatic from the on-screen buff icon, matched to the specific
buff so other active buffs are ignored. GUI with a hotkeys screen (HP pot, MP
pot, skills, buffs, chair, and the other game keys in [keys]) and a
focus-lost popup that does not take focus itself.