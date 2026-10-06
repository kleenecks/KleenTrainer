"""Run log: timestamped lines in run.log inside the run folder, also printed.

No per-keypress logging.
"""

import time
from datetime import datetime


class RunLog:
    def __init__(self, run_dir, status_interval_s):
        self.file = (run_dir / "run.log").open("a", encoding="utf-8")
        self.status_interval_s = status_interval_s
        self.started = time.monotonic()
        self.next_status = self.started + status_interval_s

    def event(self, message):
        line = f"{datetime.now():%Y-%m-%d %H:%M:%S} {message}"
        self.file.write(line + "\n")
        self.file.flush()
        print(line)

    def status_due(self, now=None):
        """True once per status interval."""
        now = time.monotonic() if now is None else now
        if now >= self.next_status:
            self.next_status = now + self.status_interval_s
            return True
        return False

    def duration(self):
        seconds = int(time.monotonic() - self.started)
        return f"{seconds // 3600}:{seconds // 60 % 60:02d}:{seconds % 60:02d}"

    def close(self):
        self.file.close()
