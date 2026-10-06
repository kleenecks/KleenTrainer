"""Stage 0 smoke test: find the client window, bring it to the front and
capture one frame of it.

Sends no input. Usage: python smoke_test.py [config.toml]
"""

import sys
import tomllib
from datetime import datetime
from pathlib import Path

import capture
import window


def main():
    config_path = Path(sys.argv[1] if len(sys.argv) > 1 else "config.toml")
    with config_path.open("rb") as f:
        config = tomllib.load(f)

    window.make_dpi_aware()
    hwnd = window.find_window(config["window_title"])
    if not hwnd:
        sys.exit(f'No window titled "{config["window_title"]}". Is the client running?')
    # mss copies screen pixels, so the client must be on top to be captured.
    if not window.bring_to_front(hwnd, config["focus_settle_s"]):
        sys.exit("Windows did not let the client come to the front. "
                 "Click the client and run again.")

    capturer = capture.Capturer(hwnd)
    frame = capturer.grab()
    capturer.close()

    run_dir = Path("runs") / datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    out = capture.save_frame(frame, run_dir)

    h, w = frame.shape[:2]
    print(f"Client area: {w}x{h}")
    print(f"Saved {out}")


if __name__ == "__main__":
    main()
