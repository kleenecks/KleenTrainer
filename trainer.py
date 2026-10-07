"""The training loop (resting comes in stage 10).

1. Hunt: no target and no loot waiting: pick the closest reachable mob.
2. Fight it until it is dead (attack.py). While still walking to it, a mob
   that is retarget_margin_px closer takes over (e.g. mobs spawning nearer
   than a far target); once attacking, the target is kept.
3. Loot: walk with the loot key held through every spot where a mob died
   and loot_past_px beyond the furthest. A mob between the character and
   the loot is fought first (back to 2), then the walk goes on and collects
   both drops.
4. Nothing to fight or loot: sweep the bottom floor, turning at the walls
   and at the edge margins.

The loot key is held whenever the character walks and is never tapped.
Held keys sent by a program are not auto-repeated by Windows, and picking up
reacts to each key-down, so keys.hold_repeating re-sends the key-down about
30 times a second. Mobs beyond the edge margins are not fought.
"""

import time

import attack
import movement

# A mob this close (screen px) counts as on top of the character, on either
# side, so it is "in the way" of loot in either direction.
ON_TOP_PX = 30


class Trainer:
    def __init__(self, session, config):
        self.session = session
        self.keys = session.keys
        self.log = session.log
        self.attacker = attack.Attacker(session, config)
        session.attacker = self.attacker
        self.mover = movement.Mover(session, config)
        self.wall_s = config["sweep"]["wall_s"]
        self.edge_margin = config["sweep"]["edge_margin"]
        self.loot_past_px = config["loot"]["past_px"]
        self.retarget_margin = config["attack"]["retarget_margin_px"]
        self.no_progress_s = config["movement"]["no_progress_s"]
        self.k = config["map"]["screen_px_per_minimap_px"]
        self.hp_low = config["rest"]["hp_low_percent"]
        self.fighting = False
        self.loot = []            # map positions (screen px) of uncollected drops
        self.loot_dir = None      # "left"/"right" while walking to loot
        self.loot_moved = (None, 0.0)   # last map x while looting, and when it changed
        self.direction = "left"   # sweep direction
        self.sweep_x = None
        self.sweep_moved = 0.0
        self.state = None

    def run(self):
        self.mover.ensure_minimap()
        while True:
            self.session.tick()
            self.step()

    def step(self):
        d = self.session.detectors
        feet = d.me.feet if d.me else None
        here = self.attacker.here()
        mobs = [m for m in d.found if feet and self._inside_margins(m, feet)]

        if self.fighting and not self.attacker.attacking and feet and here is not None:
            # Still walking to the target: a mob that spawned (or walked)
            # clearly closer takes over, and the far target is forgotten.
            target_dx = abs(self.attacker.target - here)
            closer = [m for m in mobs
                      if abs(m.feet[0] - feet[0]) + self.retarget_margin < target_dx]
            if closer:
                mob = min(closer, key=lambda m: abs(m.feet[0] - feet[0]))
                self.log.event(f"Closer mob {abs(mob.feet[0] - feet[0])} px away; "
                               f"dropping the target {round(target_dx)} px away.")
                self._fight(mob, feet)
                return

        if self.fighting:
            result = self.attacker.step()
            if result == "fighting":
                self._set_state("fighting")
                if self.attacker.attacking:
                    self.keys.release("loot")
                else:
                    self.keys.hold_repeating("loot")   # walking to the target
                return
            self.fighting = False
            if result == "dead" and not self._hp_low():
                self.loot.append(self.attacker.target)
                self.log.event(f"Killed; {len(self.loot)} drop(s) to collect.")

        if self.loot and here is not None:
            # A mob between the character and the loot: fight it first.
            in_way = [m for m in mobs if self._in_way(m.feet[0] - feet[0], here)]
            if in_way:
                self._fight(min(in_way, key=lambda m: abs(m.feet[0] - feet[0])), feet)
                return
            if self._walk_to_loot(here):
                return

        if mobs and feet:
            self._fight(min(mobs, key=lambda m: abs(m.feet[0] - feet[0])), feet)
            return
        self._sweep()

    # --- helpers --------------------------------------------------------------

    def _set_state(self, state):
        if state != self.state:
            self.log.event(f"State: {state}.")
            self.state = state
            self.session.state = state

    def _hp_low(self):
        hp = self.session.detectors.hp
        return hp is not None and hp < self.hp_low

    def _limits(self):
        """Lowest and highest minimap x the bot goes to: the minimap's map
        area minus the edge margin on each side."""
        reading = self.session.detectors.reading
        if not reading or not reading.bounds:
            return None
        return self.edge_margin, reading.bounds.width - self.edge_margin

    def _inside_margins(self, mob, feet):
        here, limits = self.attacker.here(), self._limits()
        if here is None or limits is None:
            return True
        mob_x = (here + mob.feet[0] - feet[0]) / self.k
        return limits[0] <= mob_x <= limits[1]

    def _fight(self, mob, feet):
        self.attacker.set_target(mob, feet)
        self.fighting = True
        self._set_state("fighting")
        self.attacker.step()
        if not self.attacker.attacking:
            self.keys.hold_repeating("loot")   # walking to the target

    # --- loot -----------------------------------------------------------------

    def _loot_goal(self, here):
        """(direction, map position to walk to): beyond the furthest drop,
        in the direction of the drops (the way the character faces if they
        are right under it)."""
        if self.loot_dir is None:
            furthest = max(self.loot, key=lambda s: abs(s - here))
            if abs(furthest - here) <= ON_TOP_PX:
                self.loot_dir = self.attacker.facing or "right"
            else:
                self.loot_dir = "right" if furthest > here else "left"
        sign = 1 if self.loot_dir == "right" else -1
        goal = max(s * sign for s in self.loot) * sign + sign * self.loot_past_px
        limits = self._limits()
        if limits:
            goal = max(limits[0] * self.k, min(limits[1] * self.k, goal))
        return self.loot_dir, goal

    def _in_way(self, dx, here):
        """Whether a mob dx screen px from the feet is between the character
        and the loot goal (or on top of the character)."""
        direction, goal = self._loot_goal(here)
        sign = 1 if direction == "right" else -1
        return abs(dx) <= ON_TOP_PX or 0 <= dx * sign <= (goal - here) * sign

    def _walk_to_loot(self, here):
        """Walk toward the loot goal with the loot key held. Returns False
        once it is reached (the drops are collected) or progress stops."""
        direction, goal = self._loot_goal(here)
        sign = 1 if direction == "right" else -1
        now = time.monotonic()
        last_x, moved_at = self.loot_moved
        if last_x is None or here != last_x:
            self.loot_moved = (here, now)
        if here * sign >= goal * sign or now - self.loot_moved[1] > self.no_progress_s:
            self.log.event(f"Loot collected ({len(self.loot)} drop(s)).")
            self.loot, self.loot_dir, self.loot_moved = [], None, (None, 0.0)
            self.sweep_x = None
            return False
        self._set_state("looting")
        self.attacker._walk(direction)
        self.keys.hold_repeating("loot")
        return True

    # --- sweep ----------------------------------------------------------------

    def _sweep(self):
        self._set_state("sweeping")
        reading = self.session.detectors.reading
        if not reading or not reading.dot:
            self.attacker.stop()
            return
        x = reading.dot[0]
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
