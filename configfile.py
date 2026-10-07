"""Change single values in config.toml while keeping everything else in the
file (comments, layout, order), so hand edits and GUI edits can be mixed.

Python can read TOML (tomllib) but not write it, and a full rewrite would
drop the comments, so values are replaced line by line.
"""

import re
import tomllib
from pathlib import Path

PATH = Path(__file__).parent / "config.toml"


def load(path=None):
    with Path(path or PATH).open("rb") as f:
        return tomllib.load(f)


def _toml(value):
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, str):
        return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'
    if isinstance(value, list):
        return "[" + ", ".join(_toml(v) for v in value) + "]"
    raise TypeError(f"cannot write {value!r} to TOML")


def set_value(section, key, value, path=None):
    """Set key = value in [section] (None = the top, before any section).
    The key must already exist in the file. path: config.toml unless given
    (looked up at call time, so tests can point PATH at a copy)."""
    path = Path(path or PATH)
    lines = path.read_text(encoding="utf-8").split("\n")
    current = None
    for i, line in enumerate(lines):
        header = re.match(r"\s*\[([^\]]+)\]\s*$", line)
        if header:
            current = header.group(1).strip()
            continue
        if current == section and re.match(rf"\s*{re.escape(key)}\s*=", line):
            # Keep a trailing comment, if any.
            comment = re.search(r"\s+#.*$", line.split("=", 1)[1])
            lines[i] = f"{key} = {_toml(value)}" + (comment.group(0) if comment else "")
            path.write_text("\n".join(lines), encoding="utf-8")
            load(path)   # still valid TOML
            return
    raise KeyError(f"{key} not found in [{section}] of {path.name}")


def set_values(changes, path=None):
    """changes: {(section, key): value}."""
    for (section, key), value in changes.items():
        set_value(section, key, value, path)
