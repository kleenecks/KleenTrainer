"""The training loop (resting comes in stage 10).

1. Hunt: no target and no loot waiting: go to the side (left or right) whose
   mobs weigh more (more mobs, closer, more wounded), then pick the
   lowest-HP mob there unless it is much farther than the nearest
   (pick_target).
2. Fight it until it is dead (attack.py). While still walking to it, a mob
   that is retarget_margin_px closer takes over (e.g. mobs spawning nearer
   than a far target); once attacking, the target is kept.
3. Loot: walk with the loot key held through every spot where a mob died
   and loot_past_px beyond the furthest. A mob between the character and
   the loot is fought first (back to 2), then the walk goes on and collects
   both drops.
4. Nothing to fight or loot: sweep the bottom floor, turning at the walls
   and at the edge margins.
Above all of these: HP below hp_low_percent -> rest (go to the closest safe
spot, jumping over mobs in the way, sit on the chair until HP is full).

The loot key is held whenever the character walks and is never tapped.
Held keys sent by a program are not auto-repeated by Windows, and picking up
reacts to each key-down, so keys.hold_repeating re-sends the key-down about
30 times a second. Mobs beyond the edge margins are not fought.
"""

import time

import attack
import movement
import points

# A mob this close (screen px) counts as on top of the character, on either
# side, so it is "in the way" of loot in either direction.
ON_TOP_PX = 30
# After resting, mobs on the floor are searched this many px above and below
# the floor's estimated screen height.
FLOOR_SCAN_PX = 60


def side_weight(mobs, feet, falloff):
    """How much a group of mobs pulls the character: each mob weighs
    (2 - HP share) / (1 + distance / falloff), so close mobs weigh more,
    far ones less, and wounded ones up to double."""
    return sum((2 - m.hp) / (1 + abs(m.feet[0] - feet[0]) / falloff) for m in mobs)


def pick_target(mobs, feet, prefer_px, falloff):
    """The mob to fight. First the side: the side (left or right of the
    character) whose mobs weigh more (side_weight), so a crowd farther away
    can outweigh a single close mob, and a very close mob outweighs a
    couple of distant ones. Then on that side: the lowest-HP mob
    (mobs.attach_hp; unhurt = 1.0) among those at most prefer_px farther
    than the nearest there, nearest first among equals."""
    def distance(m):
        return abs(m.feet[0] - feet[0])
    left = [m for m in mobs if m.feet[0] < feet[0]]
    right = [m for m in mobs if m.feet[0] >= feet[0]]
    if left and right:
        mobs = left if side_weight(left, feet, falloff) > side_weight(right, feet, falloff) else right
    nearest = min(distance(m) for m in mobs)
    candidates = [m for m in mobs if distance(m) <= nearest + prefer_px]
    return min(candidates, key=lambda m: (m.hp, distance(m)))


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
        self.prefer_hurt_px = config["attack"]["prefer_hurt_within_px"]
        self.side_falloff = config["attack"]["side_falloff_px"]
        self.target_hp = 1.0
        self.no_progress_s = config["movement"]["no_progress_s"]
        self.k = config["map"]["screen_px_per_minimap_px"]
        self.hp_low = config["rest"]["hp_low_percent"]
        self.hp_full = config["rest"]["hp_full_percent"]
        self.jump_over_px = config["rest"]["jump_over_px"]
        self.chair_wait_s = config["rest"]["chair_wait_s"]
        self.chair_check_s = config["rest"]["chair_check_s"]
        self.exit_timeout_s = config["rest"]["exit_timeout_s"]
        self.arrive_wait_s = config["rest"]["arrive_wait_s"]
        self.map_name = session.map_name
        session.rests = 0
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
        # Priority (after the session's stop conditions): HP low -> rest.
        if self._hp_low():
            self._rest()
            return
        feet = d.me.feet if d.me else None
        here = self.attacker.here()
        mobs = [m for m in d.found if feet and self._inside_margins(m, feet)]

        if self.fighting and not self.attacker.attacking and feet and here is not None:
            # Still walking to the target: a mob that spawned (or walked)
            # clearly closer takes over, and the far target is forgotten.
            # Only for a mob no healthier than the target (a wounded target
            # is not abandoned for a fresh mob) and in the direction of
            # travel (no turning back toward a side that was outweighed).
            target_dx = self.attacker.target - here
            sign = 1 if target_dx > 0 else -1
            closer = [m for m in mobs
                      if 0 <= (m.feet[0] - feet[0]) * sign
                      and abs(m.feet[0] - feet[0]) + self.retarget_margin < abs(target_dx)
                      and m.hp <= self.target_hp]
            target_dx = abs(target_dx)
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
            self._fight(pick_target(mobs, feet, self.prefer_hurt_px, self.side_falloff), feet)
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
        self.target_hp = mob.hp
        if mob.hp < 1:
            self.log.event(f"Target: {mob.name} at {mob.hp:.0%} HP, "
                           f"{abs(mob.feet[0] - feet[0])} px away.")
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

    # --- rest -----------------------------------------------------------------

    def _rest(self):
        """HP is low: go to the closest safe spot (jumping over mobs in the
        way, fighting nothing), sit on the chair, and get up once HP is
        full. A mob at the character's height while sitting is fought, then
        the bot returns to the spot and sits again. If no safe spot can be
        reached, the run stops (movement.MoveFailed)."""
        self.attacker.stop()
        self.keys.release("loot")
        self.fighting, self.loot, self.loot_dir = False, [], None
        self.session.rests += 1
        self._set_state("resting")
        self.log.event(f"HP {self.session.detectors.hp:.0f}%: going to rest.")
        reading = self.session.detectors.reading
        floor_y = reading.dot[1] if reading and reading.dot else None   # trains on the floor
        spot = self._go_to_safe_spot()
        self._sit()
        while True:
            self.session.tick()
            d = self.session.detectors
            if d.hp is not None and d.hp >= self.hp_full:
                self.keys.tap("jump")          # stand up without walking off
                self.log.event(f"HP {d.hp:.0f}%: rested; leaving the safe spot.")
                self.mover.settle()
                if floor_y is not None:
                    self._leave_spot(spot, floor_y)
                self.log.event("Back to training.")
                self.sweep_x = None
                return
            feet = d.me.feet if d.me else None
            if feet and d.found:
                self._fight_while_resting(feet)
                spot = self._go_to_safe_spot()
                self._sit()

    def _go_to_safe_spot(self):
        """Go to the closest safe spot, jumping over mobs in the way and
        looting on the way (loot key held, except while dodging a mob)."""
        self.mover.jump_check = self._mob_ahead
        self.mover.on_tick = self._loot_unless_dodging
        try:
            index = self.mover.closest_safe_spot(self.map_name)
            self.mover.goto_safe_spot(self.map_name, index)
            return index
        finally:
            self.mover.jump_check = None
            self.mover.on_tick = None
            self.keys.release("loot")

    def _loot_unless_dodging(self):
        """On the way to rest: hold the loot key while walking, but let go
        while a mob is just ahead (the bot is about to jump over it)."""
        held = self.keys.held
        walking = "right" if self.keys.bindings["right"] in held else (
            "left" if self.keys.bindings["left"] in held else None)
        if walking and not self._mob_ahead(walking):
            self.keys.hold_repeating("loot")
        else:
            self.keys.release("loot")

    def _leave_spot(self, index, floor_y):
        """Get from the safe spot back down to the floor (height floor_y):
        walk off toward the heavier side of the mobs on the floor, or else
        the exit remembered for this spot (or the sweep direction the first
        time), with the loot key held. A wall (no movement for wall_s) or
        no floor within exit_timeout_s: turn around and try the other way.
        The direction that worked is saved with the spot."""
        data = points.load(self.map_name)
        entry = data["safe_spots"][index]
        x, y = self.mover.dot()
        if y >= floor_y - self.mover.level:
            return                                  # already on the floor
        first = self._floor_side(floor_y, y) or entry.get("exit") or self.direction
        for direction in (first, "left" if first == "right" else "right"):
            self.log.event(f"Leaving the safe spot: walking {direction}.")
            start = moved_at = time.monotonic()
            last_x = None
            while True:
                x, y = self.mover.dot()
                now = time.monotonic()
                if y >= floor_y - self.mover.level:
                    self.attacker.stop()
                    self.log.event(f"Back on the floor at minimap x {x}.")
                    if entry.get("exit") != direction:
                        entry["exit"] = direction
                        points.save(self.map_name, data)
                    self.direction = direction      # keep sweeping this way
                    return
                if x != last_x:
                    last_x, moved_at = x, now
                elif now - moved_at > self.wall_s:
                    break                           # a wall: try the other way
                if now - start > self.exit_timeout_s:
                    break
                self.attacker._walk(direction)
                self.keys.hold_repeating("loot")
            self.attacker.stop()
        raise movement.MoveFailed("could not get back down from the safe spot")

    def _floor_side(self, floor_y, y):
        """"left"/"right" toward the heavier side of the mobs on the floor
        (side_weight), or None if none are seen. The detectors only search
        the character's height, so the floor's screen height is worked out
        from the minimap: floor_y - y minimap px below, at the minimap's
        scale."""
        d = self.session.detectors
        feet = d.me.feet if d.me else None
        if not feet:
            return None
        # A full capture: the floor is below the strip region capture grabs.
        frame = self.session.capturer.grab()
        # The minimap height is too coarse to pin the floor's screen height
        # exactly, so several heights around the estimate are searched (once
        # per rest, so the extra time does not matter).
        estimate = round(feet[1] + (floor_y - y) * self.k)
        found = []
        for floor_feet_y in range(estimate - FLOOR_SCAN_PX, estimate + FLOOR_SCAN_PX + 1, 20):
            if floor_feet_y >= frame.shape[0]:
                break
            for m in d.mobs.detect(frame, floor_feet_y):
                if (self._inside_margins(m, feet)
                        and all(abs(m.feet[0] - o.feet[0]) > 25 for o in found)):
                    found.append(m)
        if not found:
            return None
        left = [m for m in found if m.feet[0] < feet[0]]
        right = [m for m in found if m.feet[0] >= feet[0]]
        heavier = ("left" if side_weight(left, feet, self.side_falloff)
                   > side_weight(right, feet, self.side_falloff) else "right")
        self.log.event(f"Mobs on the floor: {len(left)} left, {len(right)} right.")
        return heavier

    def _sit(self):
        """Sit on the chair. Wait arrive_wait_s after arriving, and (each
        attempt) until HP has not dropped for chair_wait_s, since being hit
        blocks the chair for a while; if HP is not rising chair_check_s
        after pressing it, press again."""
        self.mover.settle()
        min_wait = self.arrive_wait_s          # first press: let it settle in
        while True:
            self._wait_for_no_damage(min_wait)
            min_wait = 0
            self.keys.tap("chair")
            start_hp = self.session.detectors.hp
            self.log.event("Sitting on the chair.")
            end = time.monotonic() + self.chair_check_s
            while time.monotonic() < end:
                self.session.tick()
            hp = self.session.detectors.hp
            if hp is None or start_hp is None or hp > start_hp or hp >= self.hp_full:
                return
            self.log.event(f"HP not rising ({start_hp:.0f}% -> {hp:.0f}%); "
                           f"the chair did not take, trying again.")

    def _wait_for_no_damage(self, min_wait=0):
        """Tick for at least min_wait seconds and until HP has not dropped
        for chair_wait_s (both counted from now, so they overlap)."""
        start = time.monotonic()
        last_hp, calm_since = self.session.detectors.hp, start
        while (time.monotonic() - calm_since < self.chair_wait_s
               or time.monotonic() - start < min_wait):
            self.session.tick()
            hp = self.session.detectors.hp
            if hp is not None and last_hp is not None and hp < last_hp - 0.5:
                calm_since = time.monotonic()
            last_hp = hp

    def _mob_ahead(self, direction):
        """Whether a reachable mob is within jump_over_px ahead (for jumping
        over it on the way to rest)."""
        d = self.session.detectors
        feet = d.me.feet if d.me else None
        if not feet:
            return False
        sign = 1 if direction == "right" else -1
        return any(0 <= (m.feet[0] - feet[0]) * sign <= self.jump_over_px for m in d.found)

    def _fight_while_resting(self, feet):
        """A mob reached the character at the safe spot: kill it."""
        d = self.session.detectors
        mob = min(d.found, key=lambda m: abs(m.feet[0] - feet[0]))
        self.log.event(f"Mob at the safe spot ({mob.name}); getting up to fight it.")
        self.attacker.set_target(mob, feet)
        while self.attacker.step() == "fighting":
            self.session.tick()
        self.attacker.stop()

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
