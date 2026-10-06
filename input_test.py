"""Input check: press jump once with pydirectinput and confirm the character
moved up, using the name tag.

Run from an administrator terminal (the client runs as administrator, and
Windows blocks input to it from normal programs). Stand still on flat ground
first. Usage: python input_test.py [config.toml]
"""

import sys
import time
import tomllib
from pathlib import Path

import pydirectinput

import capture
import player
import window

# A jump must lift the feet at least this many pixels.
MIN_RISE = 10
WATCH_S = 0.8


def main():
    config_path = Path(sys.argv[1] if len(sys.argv) > 1 else "config.toml")
    with config_path.open("rb") as f:
        config = tomllib.load(f)

    window.make_dpi_aware()
    hwnd = window.find_window(config["window_title"])
    if not hwnd:
        sys.exit(f'No window titled "{config["window_title"]}". Is the client running?')
    if not window.bring_to_front(hwnd, config["focus_settle_s"]):
        sys.exit("Windows did not let the client come to the front. "
                 "Click the client and run again.")

    capturer = capture.Capturer(hwnd)
    locator = player.PlayerLocator(config["player"])
    start = locator.read(capturer.grab())
    if start.status != "found":
        sys.exit("Could not find the name tag. Stand where it is clearly visible and run again.")
    print(f"Feet before: {start.feet}")

    key = config["keys"]["jump"]
    pydirectinput.PAUSE = 0
    if not window.is_foreground(hwnd):
        sys.exit("The client lost focus before the key press. Nothing was sent.")
    try:
        pydirectinput.keyDown(key)
        time.sleep(0.05)
    finally:
        pydirectinput.keyUp(key)
    print(f'Pressed "{key}".')

    highest = start.feet[1]
    end = time.perf_counter() + WATCH_S
    while time.perf_counter() < end:
        reading = locator.read(capturer.grab())
        if reading.status == "found":
            highest = min(highest, reading.feet[1])
        time.sleep(0.02)
    capturer.close()

    rise = start.feet[1] - highest
    print(f"Highest point: {rise} px above the start")
    if rise >= MIN_RISE:
        print("PASS: the character jumped, so the game accepts pydirectinput keys.")
    else:
        print("FAIL: no jump seen. Check the jump key, that this terminal runs as "
              "administrator, and that the character was standing still.")


if __name__ == "__main__":
    main()
