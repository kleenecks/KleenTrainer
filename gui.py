"""KleenTrainer control window: start and stop the trainer, see the run time
and live stats (or how the last run went), switch the overlay's boxes and
circles on or off, and change settings.

Run it from an administrator terminal (the trainer it starts must press keys
in the game, which runs as administrator):

    python gui.py

The small window stays on top of the game but never takes focus when
clicked (the trainer would count that as focus lost) and is hidden from
screen capture (the trainer would otherwise see it in its frames). Drag it
by its title strip. The trainer runs as its own process (main.py train
--control); this window sends it commands on stdin and reads the status.json
it writes once a second.

Settings opens a normal window (it needs keyboard focus for remapping keys),
so it is only available while the trainer is stopped. Saving edits
config.toml in place, keeping its comments (configfile.py).
"""

import ctypes
import json
import re
import subprocess
import sys
import time
import tkinter as tk
from pathlib import Path
from tkinter import ttk

import cv2
import pydirectinput

import capture
import configfile
import player
import points
import window

HERE = Path(__file__).parent
RUNS = HERE / "runs"
# If the trainer has not stopped this long after Stop, it is ended by force
# (and the game keys are released from here).
STOP_TIMEOUT_S = 5

GWL_EXSTYLE = -20
WS_EX_TOOLWINDOW = 0x80       # no taskbar button
WS_EX_NOACTIVATE = 0x08000000  # clicking does not take focus
WDA_EXCLUDEFROMCAPTURE = 0x11

BG, FG, DIM, ACCENT, WARN = "#1e1f24", "#e8e8e8", "#9a9aa0", "#3d7cf0", "#e07a5f"


# --- runs ---------------------------------------------------------------------

def newest_run(after=0.0):
    """The newest run folder created at or after the time.time() `after`."""
    if not RUNS.exists():
        return None
    runs = [r for r in RUNS.iterdir() if r.is_dir() and r.stat().st_ctime >= after - 1]
    return max(runs, key=lambda r: r.name) if runs else None


def last_run():
    """(duration, stop reason, EXP gained or None) of the newest run with a
    summary, or None."""
    if not RUNS.exists():
        return None
    for run in sorted(RUNS.iterdir(), reverse=True):
        log = run / "run.log"
        if not log.exists():
            continue
        text = log.read_text(encoding="utf-8", errors="replace")
        duration = re.search(r"Summary: duration (\d+:\d\d:\d\d)", text)
        if duration:
            reason = re.findall(r" Stop: (.*?)\.(?: Screenshot|$)", text, re.M)
            exp = re.search(r"EXP gained ([+-]\d+\.\d+)%", text)
            return duration.group(1), reason[-1] if reason else "?", exp.group(1) if exp else None
    return None


def clock(seconds):
    seconds = int(seconds)
    return f"{seconds // 3600}:{seconds // 60 % 60:02d}:{seconds % 60:02d}"


# --- keys ---------------------------------------------------------------------

# Tk key names -> (game key name for pydirectinput, hotkey name for pynput).
SPECIAL_KEYS = {
    "Control_L": ("ctrl", "ctrl_l"), "Control_R": ("ctrlright", "ctrl_r"),
    "Alt_L": ("alt", "alt_l"), "Alt_R": ("altright", "alt_r"),
    "Shift_L": ("shift", "shift"), "Shift_R": ("shiftright", "shift_r"),
    "Prior": ("pageup", "page_up"), "Next": ("pagedown", "page_down"),
    "Insert": ("insert", "insert"), "Delete": ("delete", "delete"),
    "Home": ("home", "home"), "End": ("end", "end"),
    "Left": ("left", "left"), "Right": ("right", "right"),
    "Up": ("up", "up"), "Down": ("down", "down"),
    "space": ("space", "space"), "Return": ("enter", "enter"), "Tab": ("tab", "tab"),
    **{f"F{n}": (f"f{n}", f"f{n}") for n in range(1, 13)},
}
# The game's default quick-slot keys: a bot hotkey on one of them must have
# that quick slot empty, since the game also receives the press.
QUICK_SLOT_KEYS = {"insert", "home", "page_up", "pageup", "delete", "end",
                   "page_down", "pagedown", "shift", "ctrl", "ctrl_l"}

GAME_KEYS = [("attack", "Attack"), ("jump", "Jump"), ("loot", "Loot"), ("chair", "Chair"),
             ("minimap", "Minimap"), ("left", "Move left"), ("right", "Move right")]
BOT_HOTKEYS = [("kill", "Stop (kill hotkey)"), ("save_frame", "Save frame"),
               ("mark_safe_spot", "Add safe spot"), ("remove_safe_spot", "Remove safe spot")]


def key_names(keysym, char):
    """(game key, hotkey) names for a Tk key press, or None if unsupported."""
    if keysym in SPECIAL_KEYS:
        return SPECIAL_KEYS[keysym]
    if char and len(char) == 1 and char.isalnum():
        return char.lower(), char.lower()
    return None


def same_key(a, b):
    """Whether a game key name and a hotkey name mean the same key."""
    norm = {"pageup": "page_up", "pagedown": "page_down", "ctrl": "ctrl_l", "alt": "alt_l"}
    return norm.get(a, a) == norm.get(b, b)


# --- settings window ------------------------------------------------------------

NUMBERS = [
    # (section, key, label, type, help)
    ("rest", "hp_low_percent", "Rest below HP %", int, "Goes to the safe spot below this."),
    ("rest", "hp_full_percent", "Get up at HP %", int, "Stands up from the chair at this."),
    ("attack", "range_px", "Attack range (px)", int, "Screen px between the feet."),
    ("sweep", "edge_margin", "Edge margin (minimap px)", int, "Keeps this far from the map's ends."),
]


class SettingsWindow:
    def __init__(self, parent):
        self.config = configfile.load()
        self.top = tk.Toplevel(parent)
        self.top.title("KleenTrainer settings")
        self.top.attributes("-topmost", True)
        self.top.resizable(False, False)
        self.changes = {}
        self.capturing = None   # (section, key, button) while waiting for a key press

        tabs = ttk.Notebook(self.top)
        tabs.pack(fill="both", expand=True, padx=8, pady=8)
        tabs.add(self._keys_tab(tabs), text="Keys")
        tabs.add(self._numbers_tab(tabs), text="Training")
        tabs.add(self._spots_tab(tabs), text="Safe spots")
        tabs.add(self._character_tab(tabs), text="Character")
        tabs.add(self._run_tab(tabs), text="Run")

        bottom = tk.Frame(self.top)
        bottom.pack(fill="x", padx=8, pady=(0, 8))
        self.message = tk.Label(bottom, text="", fg="#b04030", anchor="w", wraplength=330, justify="left")
        self.message.pack(side="left", fill="x", expand=True)
        tk.Button(bottom, text="Cancel", width=8, command=self.top.destroy).pack(side="right")
        tk.Button(bottom, text="Save", width=8, command=self.save).pack(side="right", padx=4)
        self.top.bind("<KeyPress>", self._key_pressed)
        self.top.focus_force()

    # Keys tab
    def _keys_tab(self, tabs):
        frame = ttk.Frame(tabs, padding=8)
        self.key_values = {("keys", k): v for k, v in self.config["keys"].items()}
        self.key_values.update({("hotkeys", k): v for k, v in self.config["hotkeys"].items()})
        row = 0
        for title, section, items in (("Game keys (as set in the game)", "keys", GAME_KEYS),
                                      ("Bot hotkeys", "hotkeys", BOT_HOTKEYS)):
            ttk.Label(frame, text=title, font=("Segoe UI", 9, "bold")).grid(
                row=row, column=0, columnspan=2, sticky="w", pady=(6 if row else 0, 2))
            row += 1
            for key, label in items:
                ttk.Label(frame, text=label).grid(row=row, column=0, sticky="w", padx=(0, 12))
                button = tk.Button(frame, width=14, text=self.key_values[(section, key)])
                button.config(command=lambda s=section, k=key, b=button: self._capture(s, k, b))
                button.grid(row=row, column=1, sticky="w", pady=1)
                row += 1
        ttk.Label(frame, text="Click a key, then press the new key (Esc cancels).",
                  foreground="#666").grid(row=row, column=0, columnspan=2, sticky="w", pady=(8, 0))
        return frame

    def _capture(self, section, key, button):
        if self.capturing:
            self.capturing[2].config(text=self.key_values[self.capturing[:2]], relief="raised")
        self.capturing = (section, key, button)
        button.config(text="press a key...", relief="sunken")
        self.top.focus_force()

    def _key_pressed(self, event):
        if not self.capturing:
            return
        section, key, button = self.capturing
        self.capturing = None
        button.config(relief="raised")
        if event.keysym == "Escape":
            button.config(text=self.key_values[(section, key)])
            return
        names = key_names(event.keysym, event.char)
        if names is None:
            button.config(text=self.key_values[(section, key)])
            self.message.config(text=f"{event.keysym} is not supported; pick another key.")
            return
        name = names[0] if section == "keys" else names[1]
        self.key_values[(section, key)] = name
        self.changes[(section, key)] = name
        button.config(text=name)
        self._check_keys()

    def _check_keys(self):
        """Warn about a key used twice, and about bot hotkeys on quick slots.
        Returns False if a key is used twice (not saved then)."""
        twice, notes = [], []
        items = list(self.key_values.items())
        for i, ((s1, k1), v1) in enumerate(items):
            for (s2, k2), v2 in items[i + 1:]:
                if same_key(v1, v2):
                    twice.append(f'"{v1}" is used for both {k1} and {k2}.')
        for key, _ in BOT_HOTKEYS:
            value = self.key_values[("hotkeys", key)]
            if value in QUICK_SLOT_KEYS or len(value) == 1:
                notes.append(f'The game also gets "{value}" ({key}): keep it unbound in the game.')
        self.message.config(text=" ".join((twice + notes)[:2]))
        return not twice

    # Training tab
    def _numbers_tab(self, tabs):
        frame = ttk.Frame(tabs, padding=8)
        self.number_vars = {}
        for row, (section, key, label, kind, help_text) in enumerate(NUMBERS):
            ttk.Label(frame, text=label).grid(row=2 * row, column=0, sticky="w", padx=(0, 12))
            var = tk.StringVar(value=str(self.config[section][key]))
            ttk.Entry(frame, textvariable=var, width=8).grid(row=2 * row, column=1, sticky="w")
            ttk.Label(frame, text=help_text, foreground="#666").grid(
                row=2 * row + 1, column=0, columnspan=2, sticky="w", pady=(0, 4))
            self.number_vars[(section, key)] = (var, kind)
        return frame

    # Safe spots tab
    def _spots_tab(self, tabs):
        frame = ttk.Frame(tabs, padding=8)
        self.map_name = self.config["map_name"]
        self.spot_list = tk.Listbox(frame, width=48, height=7)
        self.spot_list.pack(fill="x")
        buttons = ttk.Frame(frame)
        buttons.pack(fill="x", pady=(6, 0))
        ttk.Button(buttons, text="Remove", command=self._remove_spot).pack(side="left")
        ttk.Button(buttons, text="Forget route", command=self._forget_route).pack(side="left", padx=4)
        ttk.Label(frame, text="Add spots in the game with the add-safe-spot hotkey while "
                              "the trainer runs. Changes here apply immediately.",
                  foreground="#666", wraplength=320, justify="left").pack(anchor="w", pady=(6, 0))
        self._fill_spots()
        return frame

    def _fill_spots(self):
        self.spot_list.delete(0, "end")
        for s in points.load(self.map_name)["safe_spots"]:
            route = f'{len(s["route"])} jump(s)' if s.get("route") else "not learned yet"
            exit_way = s.get("exit", "not learned yet")
            self.spot_list.insert("end", f'minimap {tuple(s["spot"])}   route: {route}   exit: {exit_way}')

    def _selected_spot(self):
        sel = self.spot_list.curselection()
        if not sel:
            self.message.config(text="Select a safe spot first.")
        return sel[0] if sel else None

    def _remove_spot(self):
        i = self._selected_spot()
        if i is not None:
            data = points.load(self.map_name)
            del data["safe_spots"][i]
            points.save(self.map_name, data)
            self._fill_spots()

    def _forget_route(self):
        i = self._selected_spot()
        if i is not None:
            data = points.load(self.map_name)
            data["safe_spots"][i]["route"] = None
            data["safe_spots"][i].pop("exit", None)
            points.save(self.map_name, data)
            self._fill_spots()

    # Character tab
    def _character_tab(self, tabs):
        frame = ttk.Frame(tabs, padding=8)
        ttk.Label(frame, text="The trainer reads the character's name from the status bar at the "
                              "start of each run and finds the name tag above the character with "
                              "it, so any character works without setup.",
                  wraplength=320, justify="left").pack(anchor="w")
        ttk.Button(frame, text="Show the name it reads now", command=self._show_name).pack(
            anchor="w", pady=8)
        self.name_view = ttk.Label(frame)
        self.name_view.pack(anchor="w")
        return frame

    def _show_name(self):
        hwnd = window.find_window(self.config["window_title"])
        if not hwnd:
            self.message.config(text="The game window is not open.")
            return
        capturer = capture.Capturer(hwnd)
        frame = capturer.grab()
        capturer.close()
        letters = player.name_from_status_bar(frame, self.config["player"]["name_region"])
        if letters is None:
            self.message.config(text="No name found in the status bar.")
            return
        image = cv2.resize((letters * 255).astype("uint8"), None, fx=3, fy=3,
                           interpolation=cv2.INTER_NEAREST)
        ok, png = cv2.imencode(".png", 255 - image)
        self.name_image = tk.PhotoImage(data=png.tobytes())
        self.name_view.config(image=self.name_image)
        self.message.config(text="")

    # Run tab
    def _run_tab(self, tabs):
        frame = ttk.Frame(tabs, padding=8)
        ttk.Label(frame, text="Stop after (hours, 0 = no limit)").grid(row=0, column=0, sticky="w")
        self.max_hours = tk.StringVar(value=str(self.config["run"]["max_hours"]))
        ttk.Entry(frame, textvariable=self.max_hours, width=8).grid(row=0, column=1, sticky="w", padx=8)
        ttk.Label(frame, text="Screenshot every (minutes)").grid(row=1, column=0, sticky="w", pady=4)
        minutes = self.config["log"]["screenshot_every_s"] / 60
        self.shot_minutes = tk.StringVar(value=f"{minutes:g}")
        ttk.Entry(frame, textvariable=self.shot_minutes, width=8).grid(row=1, column=1, sticky="w", padx=8)
        self.debug_log = tk.BooleanVar(value=self.config["attack"]["debug_log"])
        ttk.Checkbutton(frame, text="Decision log (detailed; turn off for overnight runs)",
                        variable=self.debug_log).grid(row=2, column=0, columnspan=2, sticky="w", pady=4)
        return frame

    # Saving
    def save(self):
        if not self._check_keys():
            return   # a key used twice: say so, do not save
        changes = dict(self.changes)
        try:
            for (section, key), (var, kind) in self.number_vars.items():
                value = kind(var.get())
                if value != self.config[section][key]:
                    changes[(section, key)] = value
            max_hours = float(self.max_hours.get())
            changes[("run", "max_hours")] = int(max_hours) if max_hours == int(max_hours) else max_hours
            changes[("log", "screenshot_every_s")] = int(float(self.shot_minutes.get()) * 60)
        except ValueError:
            self.message.config(text="Numbers only, please (use . for decimals).")
            return
        changes[("attack", "debug_log")] = self.debug_log.get()
        configfile.set_values(changes)
        self.top.destroy()


# --- control window -------------------------------------------------------------

class ControlWindow:
    def __init__(self):
        window.make_dpi_aware()
        self.config = configfile.load()
        self.proc = None
        self.started = 0.0
        self.started_wall = 0.0
        self.stop_sent = None
        self.settings = None

        self.root = tk.Tk()
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.configure(bg=BG)
        self.root.geometry("+40+40")

        bar = tk.Frame(self.root, bg=ACCENT)
        bar.pack(fill="x")
        title = tk.Label(bar, text="KleenTrainer", bg=ACCENT, fg="white",
                         font=("Segoe UI", 9, "bold"), padx=8, pady=2)
        title.pack(side="left")
        close = tk.Label(bar, text="\u2715", bg=ACCENT, fg="white", padx=8, cursor="hand2")
        close.pack(side="right")
        close.bind("<Button-1>", lambda e: self.close())
        for w in (bar, title):
            w.bind("<ButtonPress-1>", self._drag_start)
            w.bind("<B1-Motion>", self._drag)

        body = tk.Frame(self.root, bg=BG, padx=10, pady=8)
        body.pack(fill="both")
        self.status = tk.Label(body, text="", bg=BG, fg=FG, font=("Segoe UI", 10),
                               anchor="w", width=34)
        self.status.pack(fill="x")
        self.stats = tk.Label(body, text="", bg=BG, fg=FG, font=("Segoe UI", 9),
                              anchor="w", width=40, justify="left")
        self.stats.pack(fill="x")
        self.detail = tk.Label(body, text="", bg=BG, fg=DIM, font=("Segoe UI", 8),
                               anchor="w", width=40)
        self.detail.pack(fill="x", pady=(0, 6))
        row = tk.Frame(body, bg=BG)
        row.pack(fill="x")
        self.button = tk.Button(row, text="Start", width=8, command=self.toggle_run,
                                bg=ACCENT, fg="white", activebackground="#2f63c4",
                                activeforeground="white", relief="flat")
        self.button.pack(side="left")
        self.settings_button = tk.Button(row, text="Settings", width=8, command=self.open_settings,
                                         bg="#3a3b42", fg=FG, activebackground="#4a4b52",
                                         activeforeground=FG, relief="flat")
        self.settings_button.pack(side="left", padx=6)
        self.overlay_on = tk.BooleanVar(value=self.config.get("overlay_visible", True))
        tk.Checkbutton(row, text="Overlay", variable=self.overlay_on, command=self.toggle_overlay,
                       bg=BG, fg=FG, selectcolor=BG, activebackground=BG,
                       activeforeground=FG).pack(side="right")

        self.root.update()
        self._no_focus_no_capture()
        self.warning = (None if ctypes.windll.shell32.IsUserAnAdmin()
                        else "Not administrator: the trainer cannot press keys.")
        self.tick()

    def _no_focus_no_capture(self):
        user32 = ctypes.windll.user32
        hwnd = user32.GetParent(self.root.winfo_id()) or self.root.winfo_id()
        style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        user32.SetWindowLongW(hwnd, GWL_EXSTYLE, style | WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW)
        user32.SetWindowDisplayAffinity(hwnd, WDA_EXCLUDEFROMCAPTURE)

    def _drag_start(self, event):
        self.drag_from = (event.x_root - self.root.winfo_x(), event.y_root - self.root.winfo_y())

    def _drag(self, event):
        dx, dy = self.drag_from
        self.root.geometry(f"+{event.x_root - dx}+{event.y_root - dy}")

    # --- running the trainer --------------------------------------------------

    def running(self):
        return self.proc is not None and self.proc.poll() is None

    def toggle_run(self):
        if self.running():
            self.stop()
        else:
            self.start()

    def start(self):
        if self.settings and self.settings.top.winfo_exists():
            self.settings.top.destroy()
        self.config = configfile.load()
        self.proc = subprocess.Popen(
            [sys.executable, str(HERE / "main.py"), "train", "--control"],
            cwd=HERE, stdin=subprocess.PIPE, text=True)
        self.started, self.started_wall, self.stop_sent = time.monotonic(), time.time(), None
        self.send("overlay on" if self.overlay_on.get() else "overlay off")

    def stop(self):
        self.send("stop")
        self.stop_sent = time.monotonic()

    def send(self, command):
        if self.running():
            try:
                self.proc.stdin.write(command + "\n")
                self.proc.stdin.flush()
            except OSError:
                pass

    def force_stop(self):
        """The trainer did not stop in time: end it and release every game
        key it might have held (it can no longer do so itself)."""
        self.proc.kill()
        for key in self.config["keys"].values():
            pydirectinput.keyUp(key)

    def toggle_overlay(self):
        configfile.set_value(None, "overlay_visible", self.overlay_on.get())
        self.send("overlay on" if self.overlay_on.get() else "overlay off")

    def open_settings(self):
        if self.running():
            return
        if self.settings and self.settings.top.winfo_exists():
            self.settings.top.lift()
            return
        self.settings = SettingsWindow(self.root)

    def close(self):
        if self.running():
            self.stop()
            self.closing = True
        else:
            self.root.destroy()

    # --- display ----------------------------------------------------------------

    def live_stats(self):
        """The running trainer's status.json, or None."""
        run = newest_run(self.started_wall)
        try:
            return json.loads((run / "status.json").read_text(encoding="utf-8")) if run else None
        except (OSError, ValueError):
            return None

    def tick(self):
        if self.running():
            if self.stop_sent and time.monotonic() - self.stop_sent > STOP_TIMEOUT_S:
                self.force_stop()
            self.status.config(text=f"Running  {clock(time.monotonic() - self.started)}")
            s = self.live_stats()
            if s:
                hp = "?" if s["hp"] is None else f'{s["hp"]:.0f}%'
                mp = "?" if s["mp"] is None else f'{s["mp"]:.0f}%'
                exp = "?" if s["exp_gained"] is None else f'{s["exp_gained"]:+.2f}%'
                self.stats.config(text=f'{s["state"]}   HP {hp}  MP {mp}\n'
                                       f'Rests {s["rests"]}   EXP {exp}')
            if self.stop_sent:
                self.detail.config(text="Stopping...", fg=DIM)
            elif self.warning:
                self.detail.config(text=self.warning, fg=WARN)
            else:
                self.detail.config(text="")
            self.button.config(text="Stop", bg="#c94f4f", activebackground="#a33e3e")
            self.settings_button.config(state="disabled")
        else:
            if getattr(self, "closing", False):
                self.root.destroy()
                return
            last = last_run()
            if self.proc is not None and self.proc.returncode not in (0, None) and not self.stop_sent:
                self.status.config(text="Trainer stopped with an error")
                self.detail.config(text="See the terminal and the run's run.log.", fg=WARN)
            elif last:
                self.status.config(text=f"Last run  {last[0]}")
                self.stats.config(text=f"EXP {last[2]}%" if last[2] else "")
                reason = last[1] if len(last[1]) <= 48 else last[1][:45] + "..."
                self.detail.config(text=f"Stopped: {reason}", fg=DIM)
            else:
                self.status.config(text="No runs yet")
            if self.warning:
                self.detail.config(text=self.warning, fg=WARN)
            self.button.config(text="Start", bg=ACCENT, activebackground="#2f63c4")
            self.settings_button.config(state="normal")
        self.root.after(500, self.tick)


if __name__ == "__main__":
    ControlWindow().root.mainloop()
