"""KleenTrainer entry point.

  python main.py live                    watch the client with the debug overlay
  python main.py replay FOLDER           step through saved frames
  python main.py walk X                  walk to minimap x X (presses keys)
  python main.py safe                    go to the closest safe spot (presses keys)
  python main.py train                   fight, loot and sweep (presses keys)

Each command can take a config file as its last argument (default
config.toml). walk and safe press game keys: run them from an administrator
terminal.

Hotkeys (config) work while the game is in front: save a frame as a PNG to
the run folder, add or remove a safe spot at the character's minimap
position, or stop (kill hotkey; also releases all keys). Watching pauses
while the client is not in front. Ctrl+C in the terminal also stops (or q in
the separate overlay window, if that one is used). Each run writes run.log
in its run folder.
Replay: in the overlay, n or space = next, p = previous, q = quit.
"""

import json
import sys
import threading
import time
import tomllib
from datetime import datetime
from pathlib import Path

from pynput import keyboard

import capture
import keys
import minimap
import mobs
import movement
import overlay
import player
import points
import runlog
import status
import trainer
import window


def load_config(path):
    with Path(path).open("rb") as f:
        return tomllib.load(f)


def parse_key(name):
    """'f10' -> Key.f10, 'x' -> KeyCode for x."""
    return getattr(keyboard.Key, name, None) or keyboard.KeyCode.from_char(name)


def minimap_view(reading, safe_spots):
    """Overlay text, boxes and points for a minimap reading. Each marked safe
    spot is drawn as a small box on the minimap."""
    if reading.state != "normal":
        return [f"minimap: {reading.state}"], [], []
    b = reading.bounds
    rects = [b]
    for sx, sy in safe_spots:
        rects.append(capture.Region(b.x + sx - 4, b.y + sy - 4, 8, 8))
    if reading.dot is None:
        return [f"minimap: normal {b.width}x{b.height}, no dot"], rects, []
    dx, dy = reading.dot
    return ([f"minimap: normal {b.width}x{b.height}, dot ({dx}, {dy})"],
            rects, [(b.x + dx, b.y + dy)])


def player_view(reading):
    """Overlay text and points for a player reading."""
    if reading.feet is None:
        return [f"player: {reading.status}"], [], []
    x, y = reading.feet
    score = f" {reading.score:.2f}" if reading.status == "found" else ""
    return [f"player: {reading.status}{score}, feet ({x}, {y})"], [], [reading.feet]


def mob_view(found, feet):
    """Overlay text, boxes and points for reachable mobs. The box marks the
    target the bot would pick: the nearest reachable mob."""
    if feet is None:
        return ["mobs: no player position"], [], []
    if not found:
        return ["mobs: none reachable"], [], []
    target = min(found, key=lambda m: abs(m.feet[0] - feet[0]))
    tx, ty = target.feet
    box = capture.Region(tx - 30, ty - 60, 60, 64)
    return ([f"mobs: {len(found)} reachable, target {target.name} at ({tx}, {ty})"],
            [box], [m.feet for m in found])


def bar_text(name, fill):
    return f"{name} unreadable" if fill is None else f"{name} {fill:.0f}%"


class Detectors:
    def __init__(self, config, event=print, safe_spots=()):
        """event is called with a message for each notable change."""
        self.minimap = minimap.MinimapReader(config["minimap"])
        self.player = player.PlayerLocator(config["player"])
        self.unexpected = status.UnexpectedScreenWatch(config["status"])
        self.death = status.DeathDetector(config["status"])
        self.mobs = mobs.MobDetector(list(config["mobs"]), config["mob_detection"])
        self.hp_bar = config["status"]["hp_bar"]
        self.mp_bar = config["status"]["mp_bar"]
        self.event = event
        self.safe_spots = list(safe_spots)
        self.was_dead = False
        self.last_unexpected = None
        self.deaths = 0
        self.unexpected_screens = 0
        # Latest readings, for the status line, the safe-spot marker and the
        # attack: minimap reading, player reading, reachable mobs, HP, MP.
        self.reading = None
        self.me = None
        self.found = []
        self.hp = self.mp = None
        # False when this frame's capture left out the HP/MP bars and the
        # death dialog (region capture): keep the last readings.
        self.read_slow = True

    def run(self, frame, now=None):
        """Run every detector on frame; returns overlay lines, rects and points."""
        now = time.monotonic() if now is None else now
        lines, rects, points = [], [], []
        reading = self.minimap.read(frame)
        me = self.player.read(frame, now)
        found = self.mobs.detect(frame, me.feet[1]) if me.feet else []
        for view in (minimap_view(reading, self.safe_spots), player_view(me),
                     mob_view(found, me.feet)):
            lines += view[0]
            rects += view[1]
            points += view[2]
        self.reading = reading
        self.me = me
        self.found = found
        if self.read_slow:
            self.hp = status.bar_fill(frame, self.hp_bar)
            self.mp = status.bar_fill(frame, self.mp_bar)
        lines.append(bar_text("HP", self.hp) + ", " + bar_text("MP", self.mp))

        # The bot only watches in stages 1 to 6, so an unexpected screen is
        # shown and logged rather than stopping anything.
        reason = self.unexpected.update(reading, now)
        if reason:
            lines.insert(0, f"UNEXPECTED SCREEN: {reason}")
            if self.last_unexpected is None:
                self.unexpected_screens += 1
                self.event(f"Unexpected screen: {reason}")
        elif self.last_unexpected:
            self.event("Screen back to normal.")
        self.last_unexpected = reason

        dead = self.death.is_dead(frame) if self.read_slow else self.was_dead
        if dead:
            lines.insert(0, "DEAD")
            if not self.was_dead:
                self.deaths += 1
                self.event("Death detected.")
        self.was_dead = dead
        return lines, rects, points

    def status_line(self, state):
        dot = self.reading.dot if self.reading else None
        where = f"minimap {dot}" if dot else "minimap position unknown"
        return f"Status: {where}, {bar_text('HP', self.hp)}, {bar_text('MP', self.mp)}, state {state}"


class Stopped(Exception):
    """The run must stop; the message is the reason."""


class Session:
    """Everything a run needs: the client window, capture, detectors,
    overlay, hotkeys, run log and (when input is on) game keys.

    tick() is one step of the loop: grab a frame, run the detectors, act on
    hotkeys, update the overlay, and raise Stopped when the run must end.
    Watching (input off): pauses while the client is not in front, and only
    shows an unexpected screen or death. With input on: focus loss is
    handled (screenshot, release keys, refocus; 3 in a row stops), and an
    unexpected screen or death stops the run.
    """

    def __init__(self, config, input_on):
        self.config = config
        self.input_on = input_on
        window.make_dpi_aware()
        self.hwnd = window.find_window(config["window_title"])
        if not self.hwnd:
            sys.exit(f'No window titled "{config["window_title"]}". Is the client running?')
        if not window.bring_to_front(self.hwnd, config["focus_settle_s"]):
            sys.exit("Windows did not let the client come to the front. "
                     "Click the client and run again.")

        self.run_dir = Path("runs") / datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        self.run_dir.mkdir(parents=True)
        self.log = runlog.RunLog(self.run_dir, config["log"]["status_interval_s"])
        self.log.event(f"Run start ({'input on' if input_on else 'watching'}). "
                       f"Config: {json.dumps(config)}")

        # Hotkeys are heard by a background listener; tick() acts on them.
        hotkeys = config["hotkeys"]
        self.requests = {name: threading.Event()
                         for name in ("save_frame", "mark_safe_spot", "remove_safe_spot", "kill")}
        by_key = {parse_key(hotkeys[name]): name for name in self.requests}

        def on_press(key):
            if key in by_key:
                self.requests[by_key[key]].set()

        self.listener = keyboard.Listener(on_press=on_press)
        self.listener.start()

        self.map_name = config["map_name"]
        self.capturer = capture.Capturer(self.hwnd)
        self.detectors = Detectors(config, self.log.event, points.safe_spots(self.map_name))
        self.keys = keys.Keys(self.hwnd, config["keys"]) if input_on else None
        if config["overlay"] == "game":
            self.view = overlay.GameOverlay(self.hwnd)
        else:
            self.view = overlay.Overlay(config["overlay_scale"])
        # Opening the overlay window can take focus from the client.
        window.bring_to_front(self.hwnd, config["focus_settle_s"])

        self.interval = 1 / config["capture_fps"]
        self.frame = None
        self.saved = 0
        self.focus_losses = 0
        self.last_focus_loss = 0.0
        self.reopen_tried = False
        self.attacker = None   # set by tasks that fight, for the summary
        self.state = "moving" if input_on else "watching"   # for the status line
        self.last_full = 0.0   # when the last full frame was captured
        self.slow_count = 0    # frames since the bars and death dialog were captured
        self.started = time.perf_counter()
        print(f"Run folder: {self.run_dir}")
        print(f"Press {hotkeys['kill']} (kill hotkey) or Ctrl+C here to stop.")

    def tick(self):
        """One loop step; returns the latest minimap reading (or None)."""
        if self.requests["kill"].is_set():
            raise Stopped("kill hotkey")
        if not window.find_window(self.config["window_title"]):
            raise Stopped("client window closed")

        mode = "INPUT ON" if self.input_on else "LIVE"
        if not window.is_foreground(self.hwnd):
            if self.input_on:
                self._focus_lost()
            else:
                mode = "PAUSED: client not in front"

        lines, rects, pts = [], [], []
        if window.is_foreground(self.hwnd):
            self.frame = self._grab()
            lines, rects, pts = self.detectors.run(self.frame)
            self._hotkeys()
            if self.input_on:
                self._stop_conditions()
        for name in ("save_frame", "mark_safe_spot", "remove_safe_spot"):
            self.requests[name].clear()

        if self.log.status_due():
            self.log.event(self.detectors.status_line(self.state))
        if self.frame is not None:
            h, w = self.frame.shape[:2]
            self.view.show(self.frame, [mode, f"client {w}x{h}"] + lines, rects, pts)

        wait = max(1, int((self.interval - (time.perf_counter() - self.started)) * 1000))
        if self.view.key(wait) == ord("q") or self.view.is_closed():
            raise Stopped("overlay closed")
        self.started = time.perf_counter()
        return self.detectors.reading

    def _grab(self):
        """Capture what the detectors need. Usually only regions: a strip
        around the character's feet spanning the whole width (mobs far left
        and right included), the minimap, the HP/MP bars and the death-dialog
        area. A full frame when something must be searched for again (name
        tag or minimap not tracked), for a saved frame, for the separate
        overlay window, and every full_every_s as a safety net."""
        c = self.config["capture"]
        d = self.detectors
        now = time.monotonic()
        reading, me = d.reading, d.me
        if (not c["regions"] or self.config["overlay"] == "window"
                or self.requests["save_frame"].is_set()
                or reading is None or reading.state != "normal" or reading.bounds is None
                or me is None or me.feet is None
                or now - self.last_full >= c["full_every_s"]):
            self.last_full = now
            d.read_slow = True
            return self.capturer.grab()

        feet_y = me.feet[1]
        # Above the feet: the tallest mob sprite plus the mob search margins;
        # below: the name tag and its tracking margin.
        above = d.mobs.reach_up + c["strip_margin"]
        below = player.TAG_TO_FEET_Y + player.TRACK_MARGIN + d.player.letters.shape[0] + c["strip_margin"]
        b = reading.bounds
        regions = [
            capture.Region(0, feet_y - above, 1 << 16, above + below),        # full width
            capture.Region(0, 0, b.x + b.width + 40, b.y + b.height + 40),   # minimap and buttons
        ]
        # Each capture call costs about 6 ms on top of its pixels, so the HP/MP
        # bars and the death dialog (which need no 10-per-second checking) are
        # only captured every slow_every frames; in between the detectors keep
        # their last readings.
        self.slow_count = (self.slow_count + 1) % c["slow_every"]
        self.detectors.read_slow = self.slow_count == 0
        if self.detectors.read_slow:
            hp, mp = d.hp_bar, d.mp_bar
            regions += [capture.Region(hp[0] - 5, hp[2] - 5, mp[1] - hp[0] + 10, 11),
                        d.death.area]
        return self.capturer.grab_regions(regions)

    def _hotkeys(self):
        if self.requests["save_frame"].is_set():
            print(f"Saved {capture.save_frame(self.frame, self.run_dir / 'frames')}")
            self.saved += 1
        if self.requests["mark_safe_spot"].is_set():
            mark_safe_spot(self.detectors, self.map_name,
                           self.config["points"]["merge_distance"], self.log)
        if self.requests["remove_safe_spot"].is_set():
            remove_safe_spot(self.detectors, self.map_name, self.log)

    def _stop_conditions(self):
        d = self.detectors
        if d.was_dead:
            raise Stopped("death")
        reason = d.last_unexpected
        if reason is None:
            if d.reading and d.reading.state == "normal":
                self.reopen_tried = False
            return
        if reason.startswith("minimap") and "size" not in reason and not self.reopen_tried:
            # Minimap missing for 3 s: release keys, press M once, and allow
            # another 3 s before stopping.
            self.reopen_tried = True
            self.keys.release_all()
            self.log.event(f"{reason}: pressing M once.")
            self.keys.tap("minimap")
            d.unexpected.missing_since = time.monotonic()
            d.last_unexpected = None
            return
        raise Stopped(f"unexpected screen: {reason}")

    def _focus_lost(self):
        """Screenshot of the whole screen (shows what took focus), release
        all keys, log, bring the client back. 3 in a row stops the run."""
        self.keys.release_all()
        now = time.monotonic()
        if now - self.last_focus_loss > 60:
            self.focus_losses = 0
        self.focus_losses += 1
        self.last_focus_loss = now
        shot = capture.save_full_screen(self.run_dir / f"focus_lost_{self.focus_losses}.png")
        self.log.event(f"Focus lost ({self.focus_losses} in a row); keys released; "
                       f"screenshot {shot.name}.")
        if self.focus_losses >= self.config["focus"]["max_losses_in_a_row"]:
            raise Stopped(f"focus lost {self.focus_losses} times in a row")
        if not window.bring_to_front(self.hwnd, self.config["focus_settle_s"]):
            raise Stopped("Windows refused to bring the client back to the front")
        self.log.event("Client back in front; continuing.")

    def close(self, reason):
        if self.keys:
            self.keys.close()
        self.listener.stop()
        self.capturer.close()
        self.view.close()
        self.log.event(f"Stop: {reason}.")
        d = self.detectors
        self.log.event(f"Summary: duration {self.log.duration()}, deaths seen {d.deaths}, "
                       f"unexpected screens {d.unexpected_screens}, "
                       f"focus losses {self.focus_losses}, frames saved {self.saved}"
                       + (f", attacks {self.attacker.attacks}" if self.attacker else "") + ".")
        self.log.close()


def run_session(config, input_on, task):
    """Run task(session) until it finishes or the run is stopped; always
    release keys and log the stop."""
    session = Session(config, input_on)
    reason = "finished"
    try:
        task(session)
    except Stopped as e:
        reason = str(e)
    except movement.MoveFailed as e:
        reason = f"move failed: {e}"
    except keys.NotInFront:
        reason = "client not in front when a key was due"
    except KeyboardInterrupt:
        reason = "Ctrl+C"
    finally:
        session.close(reason)


def live(config):
    """Watch only: the user plays."""
    def watch(session):
        # Stages 1 to 6 send no input, so the bot cannot open the minimap.
        reading = session.tick()
        state = reading.state if reading else "not visible"
        if state != "normal":
            print(f"The minimap is {state}. Open it at normal size "
                  "(not large, not closed) and run again.")
            raise Stopped(f"minimap {state} at startup")
        while True:
            session.tick()
    run_session(config, False, watch)


def walk(config, x):
    """Input on: open the minimap if needed, then walk to minimap x."""
    def task(session):
        mover = movement.Mover(session, config)
        mover.ensure_minimap()
        mover.walk_to(x)
        session.log.event(f"Arrived at minimap {mover.settle()}.")
    run_session(config, True, task)


def train(config):
    """Input on: fight, loot and sweep the bottom floor until the kill hotkey
    (resting comes in stage 10)."""
    def task(session):
        t = trainer.Trainer(session, config)
        try:
            t.run()
        finally:
            t.attacker.stop()
    run_session(config, True, task)


def go_safe(config):
    """Input on: open the minimap if needed, then go to the closest safe spot
    (searching for a route the first time)."""
    def task(session):
        mover = movement.Mover(session, config)
        mover.ensure_minimap()
        index = mover.closest_safe_spot(session.map_name)
        mover.goto_safe_spot(session.map_name, index)
        session.detectors.safe_spots = points.safe_spots(session.map_name)
    run_session(config, True, task)


def current_dot(detectors):
    reading = detectors.reading
    if not reading or reading.state != "normal":
        return None
    return reading.dot


def mark_safe_spot(detectors, map_name, merge_distance, log):
    """Add a safe spot at the character's minimap position (or move the
    existing spot right next to it)."""
    dot = current_dot(detectors)
    if dot is None:
        log.event("Safe spot not marked: the minimap dot is not visible.")
        return
    action = points.add_safe_spot(map_name, dot, merge_distance)
    detectors.safe_spots = points.safe_spots(map_name)
    log.event(f"Safe spot {action} at minimap {dot}; {len(detectors.safe_spots)} marked.")


def remove_safe_spot(detectors, map_name, log):
    """Remove the safe spot nearest the character's minimap position."""
    dot = current_dot(detectors)
    if dot is None:
        log.event("Safe spot not removed: the minimap dot is not visible.")
        return
    removed = points.remove_nearest_safe_spot(map_name, dot)
    detectors.safe_spots = points.safe_spots(map_name)
    if removed is None:
        log.event("Safe spot not removed: none are marked.")
    else:
        log.event(f"Safe spot removed at minimap {removed}; {len(detectors.safe_spots)} left.")


def replay(config, folder):
    frames = capture.load_frames(folder)
    if not frames:
        sys.exit(f"No PNG files in {folder}")

    detectors = Detectors(config, safe_spots=points.safe_spots(config["map_name"]))
    view = overlay.Overlay(config["overlay_scale"])
    i, shown = 0, None
    try:
        while True:
            if shown != i:
                # Detect once per frame shown, not on every redraw.
                path, frame = frames[i]
                lines, rects, pts = detectors.run(frame)
                view.show(frame, [f"REPLAY {i + 1}/{len(frames)}", path.name] + lines,
                          rects, pts)
                shown = i
            key = view.key(50)
            if key in (ord("n"), ord(" ")):
                i = min(i + 1, len(frames) - 1)
            elif key == ord("p"):
                i = max(i - 1, 0)
            elif key == ord("q") or view.is_closed():
                break
    finally:
        view.close()


def main():
    args = sys.argv[1:]
    # Commands and how many arguments they take before the optional config.
    commands = {"live": 0, "replay": 1, "walk": 1, "safe": 0, "train": 0}
    if not args or args[0] not in commands or len(args) < 1 + commands[args[0]]:
        sys.exit(__doc__)
    command, rest = args[0], args[1:]
    n = commands[command]
    config = load_config(rest[n] if len(rest) > n else "config.toml")
    if command == "live":
        live(config)
    elif command == "replay":
        replay(config, rest[0])
    elif command == "walk":
        walk(config, int(rest[0]))
    elif command == "train":
        train(config)
    else:
        go_safe(config)


if __name__ == "__main__":
    main()
