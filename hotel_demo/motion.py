"""Follow RMF paths and operate the hotel's kinematic building equipment."""

from collections import deque
import math

DRIVE_EFFICIENCY = 0.8


def approach(value, target, step):
    return value + max(-step, min(step, target - value))


def angle_error(current, target):
    return (target - current + math.pi) % (2 * math.pi) - math.pi


class Robot:
    def __init__(self, spec, levels):
        self.spec = spec
        self.x, self.y, self.yaw = spec["x"], spec["y"], spec["yaw"]
        self.level = spec["level"]
        self.z = levels[self.level]["z"]
        self.path = deque()
        self.command = None
        self.task_id = ""
        self.speed = 0.0
        self.travel_yaw = None
        self.battery = 100.0
        self.lift = None
        mechanical = spec.get("mechanical", {})
        self.drive_force = (mechanical.get("mass", 0.0) * 9.81 * mechanical.get("friction_coefficient", 0.0)
                            / DRIVE_EFFICIENCY)

    def request(self, command):
        if command["revision"] == self.command:
            return
        self.command = command["revision"]
        self.task_id = command["task_id"]
        path = command["path"]
        # The upstream fleet manager sends the last reported position first.
        # It sends that position twice when stopping a robot.
        self.path = deque(path[1:] if len(path) > 1 else path)
        if len(path) == 2 and path[0] == path[1]:
            self.path.clear()
        self.speed = 0.0

    def at_charger(self):
        return (self.level == self.spec["level"]
                and math.hypot(self.x - self.spec["x"], self.y - self.spec["y"]) < 0.10)

    def step(self, dt):
        self.travel_yaw = None
        old_x, old_y, old_yaw = self.x, self.y, self.yaw
        if self.path and not self.lift:
            self.follow_path(dt)

        distance = math.hypot(self.x - old_x, self.y - old_y)
        turn = abs(angle_error(old_yaw, self.yaw))
        battery = self.spec["battery"]
        if not (self.path or self.lift or distance or turn) and self.at_charger():
            energy = battery["charging_current"] * battery["voltage"] * dt
        else:
            # Approximate wheel travel while turning from the fleet footprint.
            wheel_travel = distance + turn * self.spec["radius"] / 2
            energy = -(self.spec["watts"] * dt + self.drive_force * wheel_travel)
        capacity = 3600 * battery["capacity"] * battery["voltage"]
        self.battery = min(100.0, max(0.0, self.battery + 100 * energy / capacity))

    def follow_path(self, dt):
        level, x, y, yaw, speed_limit = self.path[0]
        if level != self.level:
            return
        dx, dy = x - self.x, y - self.y
        distance = math.hypot(dx, dy)
        linear, accel = self.spec["limits"]["linear"]
        turn, _ = self.spec["limits"]["angular"]
        if distance > 1e-5:
            heading = math.atan2(dy, dx)
            error = angle_error(self.yaw, heading)
            if self.spec["reversible"] and abs(error) > math.pi / 2:
                error = angle_error(self.yaw, heading + math.pi)
            if abs(error) > 0.035:
                self.yaw += approach(0.0, error, turn * dt)
                self.speed = 0.0
                return
            allowed = min(linear, speed_limit) if speed_limit else linear
            target = min(allowed, math.sqrt(2 * accel * distance))
            self.speed = approach(self.speed, target, accel * dt)
            fraction = min(1.0, self.speed * dt / distance)
            self.x += fraction * dx
            self.y += fraction * dy
            self.travel_yaw = heading
            if fraction < 1:
                return
        error = angle_error(self.yaw, yaw)
        self.yaw += approach(0.0, error, turn * dt)
        if abs(error) <= turn * dt:
            self.yaw = yaw
            self.path.popleft()
            self.speed = 0.0

    def snapshot(self):
        mode = "idle"
        if self.lift or (self.path and self.path[0][0] != self.level):
            mode = "waiting"
        elif self.path:
            mode = "moving"
        elif self.at_charger() and self.battery < 99.95:
            mode = "charging"
        return {"name": self.spec["name"], "level": self.level, "x": self.x, "y": self.y,
                "z": self.z, "yaw": self.yaw, "travel_yaw": self.travel_yaw,
                "battery": self.battery, "mode": mode,
                "task_id": self.task_id, "path": list(self.path), "lift": self.lift}


class Lift:
    def __init__(self, spec):
        self.spec = spec
        self.level = spec["level"]
        self.target = self.level
        self.z = spec["levels"][self.level]
        self.door = 0.0
        self.open = False
        self.session = ""
        self.riders = []
        self.motion = "stopped"

    def request(self, command):
        self.session = command["session"]
        self.target = command["level"]
        self.open = command["open"]

    def contains(self, robot):
        dx, dy = robot.x - self.spec["x"], robot.y - self.spec["y"]
        yaw = self.spec["yaw"]
        local_x = dx * math.cos(yaw) + dy * math.sin(yaw)
        local_y = -dx * math.sin(yaw) + dy * math.cos(yaw)
        return (robot.level == self.level and not robot.lift
                and abs(local_x) < self.spec["width"] / 2 - 0.08
                and abs(local_y) < self.spec["depth"] / 2 - 0.08)

    def step(self, dt, robots):
        target_z = self.spec["levels"][self.target]
        if self.z == target_z:
            self.motion = "stopped"
            self.level = self.target
            for robot in self.riders:
                robot.lift = None
                robot.level = self.level
            self.riders.clear()
            self.door = approach(self.door, float(self.open), dt / 1.5)
            return
        self.door = approach(self.door, 0.0, dt / 1.5)
        if self.door:
            return
        if self.motion == "stopped":
            self.riders = [robot for robot in robots if self.contains(robot)]
            for robot in self.riders:
                robot.lift = self.spec["name"]
        self.motion = "up" if self.z < target_z else "down"
        self.z = approach(self.z, target_z, 1.8 * dt)
        for robot in self.riders:
            robot.z = self.z

    def snapshot(self):
        return {"name": self.spec["name"], "level": self.level, "target": self.target,
                "z": self.z, "door": self.door, "session": self.session, "motion": self.motion,
                "levels": list(self.spec["levels"])}


class Hotel:
    def __init__(self, layout):
        self.robots = {spec["name"]: Robot(spec, layout["levels"]) for spec in layout["robots"]}
        self.lifts = {spec["name"]: Lift(spec) for spec in layout["lifts"]}
        self.doors = {door["name"]: 0.0 for data in layout["levels"].values() for door in data["doors"]}
        self.door_targets = dict(self.doors)

    def request(self, commands):
        for name, command in commands["robots"].items():
            self.robots[name].request(command)
        for name, command in commands["lifts"].items():
            self.lifts[name].request(command)
        self.door_targets.update(commands["doors"])

    def step(self, dt):
        for name, current in self.doors.items():
            self.doors[name] = approach(current, self.door_targets[name], dt / 1.1)
        for lift in self.lifts.values():
            lift.step(dt, self.robots.values())
        for robot in self.robots.values():
            robot.step(dt)
