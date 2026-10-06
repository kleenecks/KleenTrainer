"""KleenTrainer entry point.

  python main.py live [config.toml]      watch the client with the debug overlay
  python main.py replay FOLDER           step through saved frames

Live: press the save-frame hotkey (config) in the game to save a PNG to the
run folder. Pauses while the client is not in front. Press q in the overlay
or close it to quit.
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
import overlay
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
    reader = minimap.MinimapReader(config["minimap"])
    # Stages 1 to 6 send no input, so the bot cannot open the minimap itself.
    state = reader.read(capturer.grab()).state
    if state != "normal":
        capturer.close()
        listener.stop()
        sys.exit(f"The minimap is {state}. Open it at normal size "
                 "(not large, not closed) and run again.")

    view = overlay.Overlay(config["overlay_scale"])
    interval = 1 / config["capture_fps"]
    frame = None
    print(f"Run folder: {run_dir}")
    print("Watching. Press q in the overlay to quit.")

    try:
        while True:
            started = time.perf_counter()
            if not window.find_window(config["window_title"]):
                print("The client window closed.")
                break

            if window.is_foreground(hwnd):
                frame = capturer.grab()
                lines, rects, points = minimap_view(reader.read(frame))
                status = "LIVE"
                if save_requested.is_set():
                    print(f"Saved {capture.save_frame(frame, frames_dir)}")
            else:
                status = "PAUSED: client not in front"
            save_requested.clear()

            if frame is not None:
                h, w = frame.shape[:2]
                view.show(frame, [status, f"client {w}x{h}"] + lines, rects, points)

            wait = max(1, int((interval - (time.perf_counter() - started)) * 1000))
            if view.key(wait) == ord("q") or view.is_closed():
                break
    finally:
        listener.stop()
        capturer.close()
        view.close()


def replay(config, folder):
    frames = capture.load_frames(folder)
    if not frames:
        sys.exit(f"No PNG files in {folder}")

    reader = minimap.MinimapReader(config["minimap"])
    view = overlay.Overlay(config["overlay_scale"])
    i = 0
    try:
        while True:
            path, frame = frames[i]
            lines, rects, points = minimap_view(reader.read(frame))
            view.show(frame, [f"REPLAY {i + 1}/{len(frames)}", path.name] + lines,
                      rects, points)
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
