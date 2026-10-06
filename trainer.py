"""The training loop (stage 9: fight, loot, sweep; resting comes in stage 10).

Looting rides along with moving: the loot key is held whenever the character
walks (to a target, sweeping, on a loot pass) and is never tapped. Held keys
sent by a program are not auto-repeated by Windows, and picking up reacts to
each key-down, so the key-down is re-sent every loop step while held. After a
kill, the character walks without stopping through the spot where the mob
died and about one mob's width past it, holding the loot key, then goes on
to the next target.

Each step, on the latest readings:
1. A loot pass is under way: keep walking until past its end.
2. A kill just happened: start a loot pass (skipped when HP is low).
3. A target: fight it (attack.py).
4. Otherwise sweep: walk the bottom floor and turn around when the minimap x
   has not changed for the wall time.
"""

import time

import attack
import movement


class Trainer:
    def __init__(self, session, config):
        self.session = session
        self.keys = session.keys
        self.log = session.log
        self.attacker = attack.Attacker(session, config)
        session.attacker = self.attacker
        self.mover = movement.Mover(session, config)
        self.wall_s = config["sweep"]["wall_s"]
        self.loot_past_px = config["loot"]["past_px"]
        self.no_progress_s = config["movement"]["no_progress_s"]
        self.screen_per_minimap = config["map"]["screen_px_per_minimap_px"]
        self.hp_low = config["rest"]["hp_low_percent"]
        self.edge_margin = config["sweep"]["edge_margin"]
        self.attacker.allowed = self._inside_margins
        self.direction = "left"
        self.sweep_x = None       # minimap x when it last changed while sweeping
        self.sweep_moved = 0.0
        self.loot_pass = None     # [end minimap x, direction, last x, when x last changed]
        self.state = None

    def run(self):
        self.mover.ensure_minimap()
        while True:
            self.session.tick()
            self.step()

    def step(self):
        if self.loot_pass:
            self._continue_loot_pass()
            return
        has_target = self.attacker.step()
        if self.attacker.kill:
            dx, dot_x, facing = self.attacker.kill
            self.attacker.kill = None
            if self._start_loot_pass(dx, dot_x, facing):
                return
        if has_target:
            self._set_state("fighting")
            self.sweep_x = None
            if self.attacker.attacking:
                self.keys.release("loot")
            else:
                self.keys.hold_repeating("loot")     # walking to the target
            return
        self._sweep()

    def _set_state(self, state):
        if state != self.state:
            self.log.event(f"State: {state}.")
            self.state = state
            self.session.state = state

    def _dot_x(self):
        reading = self.session.detectors.reading
        return reading.dot[0] if reading and reading.dot else None

    def _limits(self):
        """Lowest and highest minimap x the bot goes to: the minimap's map
        area minus the edge margin on each side."""
        reading = self.session.detectors.reading
        width = reading.bounds.width if reading and reading.bounds else None
        if width is None:
            return None
        return self.edge_margin, width - self.edge_margin

    def _inside_margins(self, mob_dx):
        """Whether a mob mob_dx screen px from the feet is inside the edge
        margins (for the attacker's target choice)."""
        x, limits = self._dot_x(), self._limits()
        if x is None or limits is None:
            return True
        mob_x = x + mob_dx / self.screen_per_minimap
        return limits[0] <= mob_x <= limits[1]

    def _start_loot_pass(self, dx, dot_x, facing):
        """Begin walking through the kill spot and a little past it, the way
        the character faced while attacking (the drops are in front of it).
        dx is the mob's last screen offset from the feet, dot_x the minimap x
        at that moment. Returns False if skipped (HP low)."""
        hp = self.session.detectors.hp
        if hp is not None and hp < self.hp_low:
            return False
        direction = facing or ("right" if dx >= 0 else "left")
        sign = 1 if direction == "right" else -1
        # How far the mob was in front of the character (0 if behind or on
        # top of it), plus the distance past it.
        ahead = max(dx * sign, 0)
        distance = ahead + self.loot_past_px
        start_x = self._dot_x()
        if start_x is None:
            start_x = dot_x
        end_x = round(start_x + sign * distance / self.screen_per_minimap)
        limits = self._limits()
        if limits:
            end_x = max(limits[0], min(limits[1], end_x))
        self.attacker.reset()   # stop attacking; pick a fresh target afterwards
        self.loot_pass = [end_x, direction, start_x, time.monotonic()]
        self._set_state("looting")
        self.log.event(f"Loot pass: mob died {ahead} px ahead ({direction}); "
                       f"walking {distance} px, minimap x {start_x} -> {end_x}.")
        self._continue_loot_pass()
        return True

    def _continue_loot_pass(self):
        end_x, direction, last_x, moved_at = self.loot_pass
        x = self._dot_x()
        now = time.monotonic()
        if x is not None and x != last_x:
            self.loot_pass[2:] = [x, now]
        passed = x is not None and (x >= end_x if direction == "right" else x <= end_x)
        if passed or now - moved_at > self.no_progress_s:
            self.loot_pass = None     # no stop: the next step picks a target
            self.sweep_x = None
            return
        self.attacker._walk(direction)
        self.keys.hold_repeating("loot")

    def _sweep(self):
        self._set_state("sweeping")
        x = self._dot_x()
        if x is None:
            self.attacker.stop()
            return
        now = time.monotonic()
        limits = self._limits()
        if limits and self.direction == "left" and x <= limits[0]:
            self._turn(f"Left margin reached at minimap x {x}")
        elif limits and self.direction == "right" and x >= limits[1]:
            self._turn(f"Right margin reached at minimap x {x}")
        elif self.sweep_x is None or x != self.sweep_x:
            self.sweep_x, self.sweep_moved = x, now
        elif now - self.sweep_moved >= self.wall_s:
            self._turn(f"Wall reached at minimap x {x}")
        self.attacker._walk(self.direction)
        self.keys.hold_repeating("loot")

    def _turn(self, why):
        self.direction = "right" if self.direction == "left" else "left"
        self.log.event(f"{why}; turning {self.direction}.")
        self.sweep_x, self.sweep_moved = None, time.monotonic()
