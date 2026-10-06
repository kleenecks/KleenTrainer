"""KleenTrainer entry point.

  python main.py live [config.toml]      watch the client with the debug overlay
  python main.py replay FOLDER           step through saved frames

Live: press the save-frame hotkey (config) in the game to save a PNG to the
run folder. Pauses while the client is not in front. Quit with Ctrl+C in the
terminal (or q in the separate overlay window, if that one is used).
Replay: in the overlay, n or space = next, p = previous, q = quit.
"""

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
import status
import window


def load_config(path):
    with Path(path).open("rb") as f:
        return tomllib.load(f)


def parse_key(name):
    """'f10' -> Key.f10, 'x' -> KeyCode for x."""
    return getattr(keyboard.Key, name, None) or keyboard.KeyCode.from_char(name)


def minimap_view(reading):
    """Overlay text, boxes and points for a minimap reading."""
    if reading.state != "normal":
        return [f"minimap: {reading.state}"], [], []
    b = reading.bounds
    if reading.dot is None:
        return [f"minimap: normal {b.width}x{b.height}, no dot"], [b], []
    dx, dy = reading.dot
    return ([f"minimap: normal {b.width}x{b.height}, dot ({dx}, {dy})"],
            [b], [(b.x + dx, b.y + dy)])


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
    def __init__(self, config):
        self.minimap = minimap.MinimapReader(config["minimap"])
        self.player = player.PlayerLocator(config["player"])
        self.unexpected = status.UnexpectedScreenWatch(config["status"])
        self.death = status.DeathDetector(config["status"])
        self.mobs = mobs.MobDetector(list(config["mobs"]), config["mob_detection"])
        self.was_dead = False
        self.hp_bar = config["status"]["hp_bar"]
        self.mp_bar = config["status"]["mp_bar"]
        self.last_unexpected = None

    def run(self, frame, now=None):
        """Run every detector on frame; returns overlay lines, rects and points."""
        now = time.monotonic() if now is None else now
        lines, rects, points = [], [], []
        reading = self.minimap.read(frame)
        me = self.player.read(frame, now)
        found = self.mobs.detect(frame, me.feet[1]) if me.feet else []
        for view in (minimap_view(reading), player_view(me), mob_view(found, me.feet)):
            lines += view[0]
            rects += view[1]
            points += view[2]
        lines.append(bar_text("HP", status.bar_fill(frame, self.hp_bar)) + ", " +
                     bar_text("MP", status.bar_fill(frame, self.mp_bar)))

        # The bot only watches in stages 1 to 6, so an unexpected screen is
        # shown and printed rather than stopping anything.
        reason = self.unexpected.update(reading, now)
        if reason:
            lines.insert(0, f"UNEXPECTED SCREEN: {reason}")
            if self.last_unexpected is None:
                print(f"Unexpected screen: {reason}")
        self.last_unexpected = reason

        dead = self.death.is_dead(frame)
        if dead:
            lines.insert(0, "DEAD")
            if not self.was_dead:
                print("Death detected.")
        self.was_dead = dead
        return lines, rects, points


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

    save_requested = threading.Event()
    save_key = parse_key(config["hotkeys"]["save_frame"])

    def on_press(key):
        if key == save_key:
            save_requested.set()

    listener = keyboard.Listener(on_press=on_press)
    listener.start()

    capturer = capture.Capturer(hwnd)
    detectors = Detectors(config)
    # Stages 1 to 6 send no input, so the bot cannot open the minimap itself.
    state = detectors.minimap.read(capturer.grab()).state
    if state != "normal":
        capturer.close()
        listener.stop()
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
    print(f"Run folder: {run_dir}")
    print("Watching. Press Ctrl+C here to quit.")

    try:
        while True:
            started = time.perf_counter()
            if not window.find_window(config["window_title"]):
                print("The client window closed.")
                break

            if window.is_foreground(hwnd):
                frame = capturer.grab()
                lines, rects, points = detectors.run(frame)
                mode = "LIVE"
                if save_requested.is_set():
                    print(f"Saved {capture.save_frame(frame, frames_dir)}")
            else:
                mode = "PAUSED: client not in front"
            save_requested.clear()

            if frame is not None:
                h, w = frame.shape[:2]
                view.show(frame, [mode, f"client {w}x{h}"] + lines, rects, points)

            wait = max(1, int((interval - (time.perf_counter() - started)) * 1000))
            if view.key(wait) == ord("q") or view.is_closed():
                break
    except KeyboardInterrupt:
        print("Stopped.")
    finally:
        listener.stop()
        capturer.close()
        view.close()


def replay(config, folder):
    frames = capture.load_frames(folder)
    if not frames:
        sys.exit(f"No PNG files in {folder}")

    detectors = Detectors(config)
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
