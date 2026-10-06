"""KleenTrainer entry point.

  python main.py live [config.toml]      watch the client with the debug overlay
  python main.py replay FOLDER           step through saved frames

Live: hotkeys (config) work while the game is in front: save a frame as a
PNG to the run folder, mark the safe spot at the character's minimap
position, or stop (kill hotkey). Pauses while the client is not in front.
Ctrl+C in the terminal also stops it (or q in the separate overlay window,
if that one is used). Each run writes run.log in its run folder.
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
import minimap
import mobs
import overlay
import player
import points
import runlog
import status
import window


def load_config(path):
    with Path(path).open("rb") as f:
        return tomllib.load(f)


def parse_key(name):
    """'f10' -> Key.f10, 'x' -> KeyCode for x."""
    return getattr(keyboard.Key, name, None) or keyboard.KeyCode.from_char(name)


def minimap_view(reading, safe_spot):
    """Overlay text, boxes and points for a minimap reading. The safe spot,
    if marked, is drawn as a small box on the minimap."""
    if reading.state != "normal":
        return [f"minimap: {reading.state}"], [], []
    b = reading.bounds
    rects = [b]
    if safe_spot:
        sx, sy = safe_spot
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
    def __init__(self, config, event=print, safe_spot=None):
        """event is called with a message for each notable change."""
        self.minimap = minimap.MinimapReader(config["minimap"])
        self.player = player.PlayerLocator(config["player"])
        self.unexpected = status.UnexpectedScreenWatch(config["status"])
        self.death = status.DeathDetector(config["status"])
        self.mobs = mobs.MobDetector(list(config["mobs"]), config["mob_detection"])
        self.hp_bar = config["status"]["hp_bar"]
        self.mp_bar = config["status"]["mp_bar"]
        self.event = event
        self.safe_spot = safe_spot
        self.was_dead = False
        self.last_unexpected = None
        self.deaths = 0
        self.unexpected_screens = 0
        # Latest readings, for the status line and the safe-spot marker.
        self.reading = None
        self.hp = self.mp = None

    def run(self, frame, now=None):
        """Run every detector on frame; returns overlay lines, rects and points."""
        now = time.monotonic() if now is None else now
        lines, rects, points = [], [], []
        reading = self.minimap.read(frame)
        me = self.player.read(frame, now)
        found = self.mobs.detect(frame, me.feet[1]) if me.feet else []
        for view in (minimap_view(reading, self.safe_spot), player_view(me),
                     mob_view(found, me.feet)):
            lines += view[0]
            rects += view[1]
            points += view[2]
        self.reading = reading
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

        dead = self.death.is_dead(frame)
        if dead:
            lines.insert(0, "DEAD")
            if not self.was_dead:
                self.deaths += 1
                self.event("Death detected.")
        self.was_dead = dead
        return lines, rects, points

    def status_line(self):
        dot = self.reading.dot if self.reading else None
        where = f"minimap {dot}" if dot else "minimap position unknown"
        return f"Status: {where}, {bar_text('HP', self.hp)}, {bar_text('MP', self.mp)}, state watching"


def live(config):
    window.make_dpi_aware()
    hwnd = window.find_window(config["window_title"])
    if not hwnd:
        sys.exit(f'No window titled "{config["window_title"]}". Is the client running?')
    if not window.bring_to_front(hwnd, config["focus_settle_s"]):
        sys.exit("Windows did not let the client come to the front. "
                 "Click the client and run again.")

    run_dir = Path("runs") / datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    run_dir.mkdir(parents=True)
    frames_dir = run_dir / "frames"
    log = runlog.RunLog(run_dir, config["log"]["status_interval_s"])
    log.event(f"Run start. Config: {json.dumps(config)}")

    # Hotkeys are heard by a background listener; the main loop acts on them.
    hotkeys = config["hotkeys"]
    requests = {name: threading.Event() for name in ("save_frame", "mark_safe_spot", "kill")}
    keys = {parse_key(hotkeys[name]): name for name in requests}

    def on_press(key):
        if key in keys:
            requests[keys[key]].set()

    listener = keyboard.Listener(on_press=on_press)
    listener.start()

    map_name = config["map_name"]
    capturer = capture.Capturer(hwnd)
    detectors = Detectors(config, log.event, points.load(map_name).get("safe_spot"))
    # Stages 1 to 6 send no input, so the bot cannot open the minimap itself.
    state = detectors.minimap.read(capturer.grab()).state
    if state != "normal":
        capturer.close()
        listener.stop()
        log.event(f"Stop: the minimap is {state} at startup.")
        log.close()
        sys.exit(f"The minimap is {state}. Open it at normal size "
                 "(not large, not closed) and run again.")

    if config["overlay"] == "game":
        view = overlay.GameOverlay(hwnd)
    else:
        view = overlay.Overlay(config["overlay_scale"])
    # Opening the overlay window can take focus from the client.
    window.bring_to_front(hwnd, config["focus_settle_s"])
    interval = 1 / config["capture_fps"]
    frame = None
    saved = 0
    stop_reason = "overlay closed"
    print(f"Run folder: {run_dir}")
    print(f"Watching. Press {hotkeys['kill']} (kill hotkey) or Ctrl+C here to stop.")

    try:
        while True:
            started = time.perf_counter()
            if requests["kill"].is_set():
                stop_reason = "kill hotkey"
                break
            if not window.find_window(config["window_title"]):
                stop_reason = "client window closed"
                break

            if window.is_foreground(hwnd):
                frame = capturer.grab()
                lines, rects, pts = detectors.run(frame)
                mode = "LIVE"
                if requests["save_frame"].is_set():
                    print(f"Saved {capture.save_frame(frame, frames_dir)}")
                    saved += 1
                if requests["mark_safe_spot"].is_set():
                    mark_safe_spot(detectors, map_name, log)
            else:
                mode = "PAUSED: client not in front"
            requests["save_frame"].clear()
            requests["mark_safe_spot"].clear()

            if log.status_due():
                log.event(detectors.status_line())

            if frame is not None:
                h, w = frame.shape[:2]
                view.show(frame, [mode, f"client {w}x{h}"] + lines, rects, pts)

            wait = max(1, int((interval - (time.perf_counter() - started)) * 1000))
            if view.key(wait) == ord("q") or view.is_closed():
                break
    except KeyboardInterrupt:
        stop_reason = "Ctrl+C"
    finally:
        # From stage 7 the kill hotkey also releases all keys here.
        listener.stop()
        capturer.close()
        view.close()
        log.event(f"Stop: {stop_reason}.")
        log.event(f"Summary: duration {log.duration()}, deaths seen {detectors.deaths}, "
                  f"unexpected screens {detectors.unexpected_screens}, frames saved {saved}.")
        log.close()


def mark_safe_spot(detectors, map_name, log):
    """Save the character's current minimap position as the safe spot."""
    reading = detectors.reading
    if not reading or reading.state != "normal" or reading.dot is None:
        log.event("Safe spot not marked: the minimap dot is not visible.")
        return
    path = points.save_safe_spot(map_name, reading.dot)
    detectors.safe_spot = reading.dot
    log.event(f"Safe spot marked at minimap {reading.dot} ({path.name}).")


def replay(config, folder):
    frames = capture.load_frames(folder)
    if not frames:
        sys.exit(f"No PNG files in {folder}")

    detectors = Detectors(config, safe_spot=points.load(config["map_name"]).get("safe_spot"))
    view = overlay.Overlay(config["overlay_scale"])
    i, shown = 0, None
    try:
        while True:
            if shown != i:
                # Detect once per frame shown, not on every redraw.
                path, frame = frames[i]
                lines, rects, points = detectors.run(frame)
                view.show(frame, [f"REPLAY {i + 1}/{len(frames)}", path.name] + lines,
                          rects, points)
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
    if not args or args[0] not in ("live", "replay"):
        sys.exit(__doc__)
    if args[0] == "live":
        live(load_config(args[1] if len(args) > 1 else "config.toml"))
    else:
        if len(args) < 2:
            sys.exit("Usage: python main.py replay FOLDER [config.toml]")
        replay(load_config(args[2] if len(args) > 2 else "config.toml"), args[1])


if __name__ == "__main__":
    main()
