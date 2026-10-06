"""Download mob animation frames from maplestory.io into templates/mobs/.

Uses the [mobs] list in the config. Saves every frame of every animation as
templates/mobs/<name>/<animation>_<frame>.png (transparent PNGs, facing
left as stored in the game files).

Usage: python fetch_mobs.py [config.toml]
"""

import json
import sys
import tomllib
import urllib.request
from pathlib import Path

API = "https://maplestory.io/api/GMS/92/mob"
OUT = Path(__file__).parent / "templates" / "mobs"
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def get(url):
    # The site rejects Python's default User-Agent.
    request = urllib.request.Request(url, headers={"User-Agent": "KleenTrainer fetch_mobs"})
    with urllib.request.urlopen(request, timeout=30) as response:
        return response.read()


def main():
    config_path = Path(sys.argv[1] if len(sys.argv) > 1 else "config.toml")
    with config_path.open("rb") as f:
        mobs = tomllib.load(f)["mobs"]

    for name, mob_id in mobs.items():
        info = json.loads(get(f"{API}/{mob_id}"))
        folder = OUT / name
        folder.mkdir(parents=True, exist_ok=True)
        saved = 0
        for animation, count in info["framebooks"].items():
            for frame in range(count):
                data = get(f"{API}/{mob_id}/render/{animation}/{frame}")
                if not data.startswith(PNG_SIGNATURE):
                    sys.exit(f"{name} {animation} {frame}: not a PNG, stopping.")
                (folder / f"{animation}_{frame}.png").write_bytes(data)
                saved += 1
        print(f"{name} ({info['name']}, {mob_id}): {saved} frames")


if __name__ == "__main__":
    main()
