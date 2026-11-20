"""Expose the Isaac hotel as RMF robots and building equipment over ROS 2."""

import asyncio
from contextlib import suppress
import json
import os
import socket
import time
from uuid import uuid4

from aiohttp import WSMsgType, web
import rclpy
from builtin_interfaces.msg import Time
from rclpy.clock import Clock as NodeClock, ClockType
from rclpy.executors import SingleThreadedExecutor
from rclpy.node import Node
from rclpy.qos import DurabilityPolicy, QoSProfile, ReliabilityPolicy
from rmf_dispenser_msgs.msg import DispenserRequest, DispenserResult
from rmf_door_msgs.msg import DoorMode, DoorRequest, DoorState
from rmf_fleet_msgs.msg import Location, PathRequest, RobotMode, RobotState
from rmf_ingestor_msgs.msg import IngestorRequest, IngestorResult
from rmf_lift_msgs.msg import LiftRequest, LiftState
from rmf_task_msgs.msg import ApiRequest, ApiResponse
from rosgraph_msgs.msg import Clock

from . import ASSETS, PHYSICS_HZ, ROOT, SIM_ADDRESS
from .lifecycle import RUN_ID, STATE_DIR, request_reset
from .task_history import TaskHistory
from .task_simulation import TaskSimulation

PRESETS = {
    "lobby-patrol": ("Lobby patrol", "Lobby → Restaurant", ["lobby", "restaurant"]),
    "suite-patrol": ("Suite patrol", "Restaurant → L3 master suite", ["restaurant", "L3_master_suite"]),
    "clean-lobby": ("Lobby cleaning", "Clean the L1 lobby", "clean_lobby"),
    "suite-delivery": ("Suite delivery", "Kitchen → L3 master suite", ["kitchen", "L3_master_suite"]),
    "food-delivery": ("Food delivery", "Lobby → Guest room", None),
}
KITCHEN_PICKUP = "hotel_kitchen"
SUITE_DROPOFF = "hotel_suite"
LOBBY_PICKUP = "hotel_lobby"
ROOM_DROPOFF = "hotel_guest_room"
TERMINAL = {"completed", "finished", "failed", "canceled", "killed", "skipped"}
ROBOT_MODES = {"idle": RobotMode.MODE_IDLE, "moving": RobotMode.MODE_MOVING,
               "charging": RobotMode.MODE_CHARGING, "waiting": RobotMode.MODE_WAITING}


def task_request(preset, now, room_waypoint=None):
    route = PRESETS[preset][2]
    request = {"requester": "Hotel dashboard", "unix_millis_request_time": now,
               "unix_millis_earliest_start_time": now}
    if preset == "clean-lobby":
        activities = [
            {"category": "go_to_place", "description": route},
            {"category": "perform_action", "description": {
                "category": "clean", "description": {"zone": route}, "use_tool_sink": True,
                "unix_millis_action_duration_estimate": 60000, "expected_finish_location": route,
            }},
        ]
        request.update(category="compose", description={"category": "clean", "phases": [
            {"activity": {"category": "sequence", "description": {"activities": activities}}},
        ]})
    elif preset in ("suite-delivery", "food-delivery"):
        if preset == "food-delivery":
            route = ["lobby", room_waypoint]
            pickup, dropoff = LOBBY_PICKUP, ROOM_DROPOFF
        else:
            pickup, dropoff = KITCHEN_PICKUP, SUITE_DROPOFF
        request.update(category="delivery", fleet_name="deliveryRobot", description={
            "pickup": {"place": route[0], "handler": pickup, "payload": []},
            "dropoff": {"place": route[1], "handler": dropoff, "payload": []},
        })
    else:
        request.update(category="patrol", description={"places": route, "rounds": 1})
        if preset == "lobby-patrol":
            request["fleet_name"] = "tinyRobot"
    return {"type": "dispatch_task_request", "request": request}


def location(goal, stamp):
    level, x, y, yaw, speed = goal
    return Location(level_name=level, x=float(x), y=float(y), yaw=float(yaw), t=stamp,
                    obey_approach_speed_limit=bool(speed), approach_speed_limit=float(speed or 0))


def opening_mode(fraction):
    return 0 if fraction == 0 else 2 if fraction == 1 else 1


class HotelBridge(Node):
    def __init__(self):
        super().__init__("isaac_hotel_bridge")
        self.layout = json.loads((ASSETS / "hotel.json").read_text())
        self.rooms = {room: entry["waypoint"] for floor in self.layout["levels"].values()
                      for room, entry in floor.get("rooms", {}).items()}
        self.robots = {robot["name"]: robot for robot in self.layout["robots"]}
        self.lifts = {lift["name"]: lift for lift in self.layout["lifts"]}
        self.doors = {door["name"] for floor in self.layout["levels"].values() for door in floor["doors"]}
        self.commands = {"robots": {}, "doors": {}, "lifts": {}}
        self.command_source = uuid4().hex
        self.revision = 0
        self.last_sim = None
        self.last_seen = 0.0
        self.sequence = 0
        self.sim_restarted = False
        self.reset_run = None
        self.fleet_seen = {}
        self.active_tasks = {}
        self.tasks = {}
        self.history = TaskHistory(STATE_DIR / "tasks.sqlite3")
        self.task_simulation = TaskSimulation(self)
        self.responses = {}
        self.manual_lifts = {}
        self.stamp = Time()
        self.link = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.link.connect(SIM_ADDRESS)
        self.link.setblocking(False)

        clock_qos = QoSProfile(depth=1, reliability=ReliabilityPolicy.BEST_EFFORT)
        self.clock_pub = self.create_publisher(Clock, "/clock", clock_qos)
        self.robot_pub = self.create_publisher(RobotState, "robot_state", 10)
        self.door_pub = self.create_publisher(DoorState, "door_states", 10)
        self.lift_pub = self.create_publisher(LiftState, "lift_states", 10)
        durable = QoSProfile(depth=10, durability=DurabilityPolicy.TRANSIENT_LOCAL)
        self.request_pub = self.create_publisher(ApiRequest, "task_api_requests", durable)
        self.manual_door_pub = self.create_publisher(DoorRequest, "adapter_door_requests", 10)
        self.manual_lift_pub = self.create_publisher(LiftRequest, "adapter_lift_requests", durable)
        self.dispenser_pub = self.create_publisher(DispenserResult, "dispenser_results", 10)
        self.ingestor_pub = self.create_publisher(IngestorResult, "ingestor_results", 10)
        self.create_subscription(PathRequest, "robot_path_requests", self.on_path, 20)
        self.create_subscription(DoorRequest, "door_requests", self.on_door, 20)
        self.create_subscription(LiftRequest, "lift_requests", self.on_lift, durable)
        self.create_subscription(ApiResponse, "task_api_responses", self.on_response, durable)
        self.create_subscription(DispenserRequest, "dispenser_requests", self.on_pickup, 10)
        self.create_subscription(IngestorRequest, "ingestor_requests", self.on_dropoff, 10)
        self.create_timer(0.05, self.exchange, clock=NodeClock(clock_type=ClockType.STEADY_TIME))

    def on_path(self, msg):
        robot = self.robots.get(msg.robot_name)
        if robot is None or msg.fleet_name != robot["fleet"]:
            return
        path = [[goal.level_name or robot["level"], goal.x, goal.y, goal.yaw,
                 goal.approach_speed_limit if goal.obey_approach_speed_limit else None] for goal in msg.path]
        if any(goal[0] not in self.layout["levels"] for goal in path):
            return
        command = self.commands["robots"].get(msg.robot_name, {})
        if command.get("task_id") == msg.task_id and command.get("path") == path:
            return
        self.revision += 1
        self.commands["robots"][msg.robot_name] = {
            "task_id": msg.task_id, "path": path, "revision": f"{self.command_source}:{self.revision}",
        }

    def on_door(self, msg):
        if msg.door_name in self.doors:
            self.commands["doors"][msg.door_name] = float(msg.requested_mode.value == DoorMode.MODE_OPEN)

    def on_lift(self, msg):
        lift = self.lifts.get(msg.lift_name)
        if lift is None or msg.destination_floor not in lift["levels"]:
            return
        end = msg.request_type == LiftRequest.REQUEST_END_SESSION
        self.commands["lifts"][msg.lift_name] = {
            "session": "" if end else msg.session_id, "level": msg.destination_floor,
            "open": not end and msg.door_state == LiftRequest.DOOR_OPEN,
        }

    def on_response(self, msg):
        future = self.responses.get(msg.request_id)
        if future is not None and not future.done():
            future.set_result(json.loads(msg.json_msg))

    def on_pickup(self, msg):
        if msg.target_guid in (KITCHEN_PICKUP, LOBBY_PICKUP) and self.sim_live():
            self.dispenser_pub.publish(DispenserResult(time=self.stamp, request_guid=msg.request_guid,
                                                       source_guid=msg.target_guid, status=DispenserResult.SUCCESS))

    def on_dropoff(self, msg):
        if msg.target_guid in (SUITE_DROPOFF, ROOM_DROPOFF) and self.sim_live():
            self.ingestor_pub.publish(IngestorResult(time=self.stamp, request_guid=msg.request_guid,
                                                     source_guid=msg.target_guid, status=IngestorResult.SUCCESS))
            task = self.tasks.get(msg.request_guid)
            if task is not None:
                task["dropoff_acknowledged"] = True

    def sim_live(self):
        return (not self.reset_run and not self.sim_restarted and self.last_sim is not None
                and time.monotonic() - self.last_seen < 2)

    def sim_time(self):
        return self.last_sim["step"] / PHYSICS_HZ if self.last_sim else 0

    def ready(self):
        now = time.monotonic()
        return self.sim_live() and all(now - self.fleet_seen.get(name, -float("inf")) < 10 for name in self.robots)

    def available_fleets(self):
        active = [task for task in self.tasks.values() if task.get("preset") and task["status"] not in TERMINAL]
        if any(not task.get("robot") for task in active):
            return set()
        assigned = {task["robot"] for task in active}
        return {self.robots[robot["name"]]["fleet"] for robot in self.last_sim["robots"]
                if robot["mode"] in ("idle", "charging") and robot["name"] not in assigned}

    def reset(self):
        if RUN_ID is None:
            raise web.HTTPConflict(text="Reset is available when running the hotel with Docker Compose.")
        if not self.reset_run:
            try:
                self.reset_run = request_reset()
            except OSError as error:
                self.get_logger().error(f"Could not record the reset request: {error}")
                raise web.HTTPInternalServerError(text="Could not reset the simulation.") from error
            self.task_simulation.set_enabled(False)
        return {"run": self.reset_run}

    def lift_call(self, name, level, session, release=False):
        self.manual_lift_pub.publish(LiftRequest(
            lift_name=name, request_time=self.stamp, destination_floor=level, session_id=session,
            request_type=LiftRequest.REQUEST_END_SESSION if release else LiftRequest.REQUEST_AGV_MODE,
            door_state=LiftRequest.DOOR_CLOSED if release else LiftRequest.DOOR_OPEN,
        ))

    def operate(self, form):
        if not self.sim_live():
            raise web.HTTPServiceUnavailable(text="The simulation is offline.")
        name, action = form.get("name"), form.get("action")
        if not isinstance(name, str):
            raise web.HTTPBadRequest(text="Select a door or lift.")
        if name in self.doors and action in ("open", "close"):
            mode = DoorMode.MODE_OPEN if action == "open" else DoorMode.MODE_CLOSED
            self.manual_door_pub.publish(DoorRequest(door_name=name, requester_id="hotel_dashboard",
                request_time=self.stamp, requested_mode=DoorMode(value=mode)))
            return

        level = form.get("level")
        if (action != "call" or name not in self.lifts or not isinstance(level, str)
                or level not in self.lifts[name]["levels"]):
            raise web.HTTPBadRequest(text="Select a lift and a destination floor.")
        lift = next(lift for lift in self.last_sim["lifts"] if lift["name"] == name)
        if lift["session"] or name in self.manual_lifts:
            raise web.HTTPConflict(text="A robot task has reserved this lift.")
        session = f"hotel_dashboard-{uuid4().hex}"
        self.manual_lifts[name] = {"session": session, "requested": time.monotonic(), "release_at": None}
        self.lift_call(name, level, session)

    def release_lifts(self, state):
        for lift in state["lifts"]:
            name = lift["name"]
            manual = self.manual_lifts.get(name)
            if manual is None:
                continue
            if lift["session"] != manual["session"]:
                if time.monotonic() - manual["requested"] > 4:
                    self.manual_lifts.pop(name)
                continue
            if lift["motion"] == "stopped" and lift["door"] == 1:
                if manual["release_at"] is None:
                    manual["release_at"] = state["step"] + 5 * PHYSICS_HZ
                elif state["step"] >= manual["release_at"]:
                    self.lift_call(name, lift["level"], manual["session"], release=True)
                    self.manual_lifts.pop(name)

    def exchange(self):
        if self.sim_restarted or self.reset_run:
            return
        state = None
        try:
            while True:
                update = json.loads(self.link.recv(65535))
                if update.get("run") == RUN_ID:
                    state = update
        except (BlockingIOError, ConnectionRefusedError):
            pass
        if state and self.last_sim and state["session"] != self.last_sim["session"]:
            self.sim_restarted = True
            self.get_logger().error("Isaac Sim restarted. Restart both demo services to reset the RMF schedule.")
            return
        session = (state or self.last_sim or {}).get("session")
        try:
            commands = {"session": session, "run": RUN_ID, **self.commands}
            self.link.send(json.dumps(commands, separators=(",", ":")).encode())
        except (BlockingIOError, ConnectionRefusedError):
            pass
        if state is None:
            return
        if self.last_sim and state["step"] <= self.last_sim["step"]:
            return
        self.last_sim = state
        self.last_seen = time.monotonic()
        seconds, nanos = divmod(state["step"] * 1_000_000_000 // PHYSICS_HZ, 1_000_000_000)
        self.stamp = stamp = Time(sec=seconds, nanosec=nanos)
        self.clock_pub.publish(Clock(clock=stamp))
        self.sequence += 1
        self.release_lifts(state)
        for robot in state["robots"]:
            self.robot_pub.publish(RobotState(
                name=robot["name"], model=self.robots[robot["name"]]["fleet"], seq=self.sequence,
                task_id=robot["task_id"], battery_percent=robot["battery"],
                mode=RobotMode(mode=ROBOT_MODES[robot["mode"]]),
                location=location([robot["level"], robot["x"], robot["y"], robot["yaw"], None], stamp),
                path=[location(goal, stamp) for goal in robot["path"]],
            ))
        if self.sequence % 4 == 1:
            for name, fraction in state["doors"].items():
                self.door_pub.publish(DoorState(door_name=name, door_time=stamp,
                                                current_mode=DoorMode(value=opening_mode(fraction))))
            for lift in state["lifts"]:
                self.lift_pub.publish(LiftState(
                    lift_name=lift["name"], lift_time=stamp, available_floors=lift["levels"],
                    current_floor=lift["level"], destination_floor=lift["target"], session_id=lift["session"],
                    door_state=opening_mode(lift["door"]), available_modes=[LiftState.MODE_AGV],
                    current_mode=LiftState.MODE_AGV,
                    motion_state={"stopped": LiftState.MOTION_STOPPED,
                                  "up": LiftState.MOTION_UP, "down": LiftState.MOTION_DOWN}[lift["motion"]],
                ))

    def snapshot(self):
        now = time.monotonic()
        live = self.sim_live()
        state = {} if self.reset_run else self.last_sim or {}
        poses = {robot["name"]: robot for robot in state.get("robots", [])}
        robots = []
        for name, spec in self.robots.items():
            robot = {"battery": None, "path": [], "mode": "offline", **poses.get(name, {})}
            robot.update({key: spec[key] for key in ("name", "fleet", "role", "color")})
            robot.update({key: robot.get(key, spec[key]) for key in ("x", "y", "yaw", "level")})
            robot["online"] = live and name in self.fleet_seen and now - self.fleet_seen[name] < 10
            robots.append(robot)
        return {"live": live, "ready": live and all(robot["online"] for robot in robots),
                "restarted": self.sim_restarted, "sim_time": state.get("step", 0) / PHYSICS_HZ,
                "run": RUN_ID, "can_reset": RUN_ID is not None, "resetting": self.reset_run is not None,
                "task_simulation": self.task_simulation.snapshot(), "performance": self.history.summary(),
                "actors": state.get("actors", {}), "people": state.get("people", []) if live else [],
                "robots": robots, "lifts": state.get("lifts", []), "doors": state.get("doors", {}),
                "tasks": [] if self.reset_run else list(reversed(list(self.tasks.values())))}

    def set_task_status(self, task, status):
        if task.get("status") not in TERMINAL or status in TERMINAL:
            task["status"] = status
        if task["status"] in TERMINAL:
            task.setdefault("ended_at", self.sim_time())
            self.history.record(task)

    def update_task(self, state, preset=None, room=None, source=None, requested_at=None):
        booking = state.get("booking", {})
        task_id = booking.get("id")
        if not task_id or self.reset_run:
            return None
        now = self.sim_time()
        item = self.tasks.setdefault(task_id, {"id": task_id, "time": now})
        request_millis = booking.get("unix_millis_request_time", requested_at)
        if isinstance(request_millis, int) and 0 <= request_millis <= now * 1000 + 1:
            item["time"] = request_millis / 1000
        labels = dict(label.split("=", 1) for label in booking.get("labels", [])
                      if isinstance(label, str) and "=" in label)
        preset = preset or labels.get("hotel_task") or item.get("preset")
        if preset in PRESETS:
            item.update(preset=preset, name=PRESETS[preset][0], route=PRESETS[preset][1])
            item["source"] = source or labels.get("hotel_source") or item.get("source", "manual")
        else:
            item.setdefault("name", state.get("category", "Task"))
            if isinstance(state.get("detail"), str):
                item.setdefault("route", state["detail"])
        room = room or labels.get("hotel_room") or item.get("room")
        if room in self.rooms and preset == "food-delivery":
            item.update(room=room, route=f"Lobby → Room {room}")

        assignment = state.get("assigned_to") or {}
        dispatched = (state.get("dispatch") or {}).get("assignment") or {}
        item["robot"] = assignment.get("name") or dispatched.get("expected_robot_name") or item.get("robot")
        self.set_task_status(item, state.get("status", "queued"))
        if len(self.tasks) > 100:
            expired = next((key for key, task in self.tasks.items()
                            if key != task_id and task["status"] in TERMINAL), None)
            if expired:
                self.tasks.pop(expired)
        return item

    def update_fleet(self, fleet):
        for name, robot in fleet["robots"].items():
            if name not in self.robots or self.robots[name]["fleet"] != fleet["name"]:
                continue
            self.fleet_seen[name] = time.monotonic()
            current = robot.get("task_id", "")
            previous = self.tasks.get(self.active_tasks.get(name))
            # Jazzy can omit a terminal task update if the last phase has
            # already disappeared. The fleet still reports that it ended.
            if previous and previous["id"] != current and previous["status"] not in TERMINAL:
                self.set_task_status(previous, "completed" if previous.get("dropoff_acknowledged") else "finished")
            self.active_tasks[name] = current

    async def submit(self, preset, room=None, source="manual"):
        if not self.ready():
            raise web.HTTPServiceUnavailable(text="The simulation and robots are still starting up.")
        request_id = f"hotel-{uuid4().hex}"
        response = asyncio.get_running_loop().create_future()
        self.responses[request_id] = response
        now = self.last_sim["step"] * 1000 // PHYSICS_HZ
        request = task_request(preset, now, self.rooms.get(room))
        labels = [f"hotel_task={preset}", f"hotel_source={source}"]
        if room:
            labels.append(f"hotel_room={room}")
        request["request"]["labels"] = labels
        self.request_pub.publish(ApiRequest(request_id=request_id, json_msg=json.dumps(request)))
        try:
            result = await asyncio.wait_for(response, timeout=8)
        except TimeoutError as error:
            raise web.HTTPGatewayTimeout(text="The task was not confirmed. Check the task list before retrying.") from error
        finally:
            self.responses.pop(request_id, None)
        if not result.get("success"):
            errors = result.get("errors", [])
            detail = errors[0].get("detail", "The task could not be scheduled.") if errors else "The task could not be scheduled."
            raise web.HTTPConflict(text=detail)
        return self.update_task(result["state"], preset, room, source, now)


def dashboard(bridge):
    async def page(_request):
        return web.FileResponse(ROOT / "web/index.html")

    async def map_data(_request):
        return web.FileResponse(ASSETS / "hotel.json")

    async def state(_request):
        return web.json_response(bridge.snapshot(), headers={"Cache-Control": "no-store"})

    async def presets(_request):
        options = []
        for key, (name, description, _) in PRESETS.items():
            option = {"id": key, "name": name, "description": description}
            if key == "food-delivery":
                if not bridge.rooms:
                    continue
                option["rooms"] = sorted(bridge.rooms)
            options.append(option)
        return web.json_response(options)

    async def json_form(request):
        if request.content_type != "application/json":
            raise web.HTTPUnsupportedMediaType(text="Expected a JSON task request.")
        try:
            form = await request.json()
        except ValueError as error:
            raise web.HTTPBadRequest(text="Invalid task request.") from error
        if not isinstance(form, dict):
            raise web.HTTPBadRequest(text="Expected a JSON object.")
        return form

    async def submit(request):
        form = await json_form(request)
        preset = form.get("preset")
        if not isinstance(preset, str) or preset not in PRESETS:
            raise web.HTTPBadRequest(text="Select a hotel task.")
        room = None
        if preset == "food-delivery":
            room = form.get("room")
            if not isinstance(room, str) or room not in bridge.rooms:
                raise web.HTTPBadRequest(text="Select a guest room.")
        return web.json_response(await bridge.submit(preset, room), status=202)

    async def infrastructure(request):
        bridge.operate(await json_form(request))
        return web.json_response({"accepted": True}, status=202)

    async def task_simulation(request):
        form = await json_form(request)
        enabled = form.get("enabled")
        if not isinstance(enabled, bool):
            raise web.HTTPBadRequest(text="Choose whether to simulate tasks.")
        if enabled and not bridge.ready():
            raise web.HTTPServiceUnavailable(text="Wait for the simulation before starting automatic tasks.")
        return web.json_response(bridge.task_simulation.set_enabled(enabled))

    async def reset(request):
        await json_form(request)
        return web.json_response(bridge.reset(), status=202)

    async def events(request):
        if request.remote != "127.0.0.1":
            raise web.HTTPForbidden()
        stream = web.WebSocketResponse(max_msg_size=2**20)
        await stream.prepare(request)
        async for message in stream:
            if message.type != WSMsgType.TEXT:
                continue
            try:
                event = message.json()
                data = event["data"]
                if event["type"] == "task_state_update":
                    bridge.update_task(data)
                elif event["type"] == "fleet_state_update":
                    bridge.update_fleet(data)
            except (ValueError, KeyError, TypeError, AttributeError):
                bridge.get_logger().warning("Ignored an invalid RMF update")
        return stream

    app = web.Application(client_max_size=4096)
    app.router.add_get("/", page)
    app.router.add_get("/api/layout", map_data)
    app.router.add_get("/api/state", state)
    app.router.add_get("/api/presets", presets)
    app.router.add_post("/api/tasks", submit)
    app.router.add_post("/api/task-simulation", task_simulation)
    app.router.add_post("/api/reset", reset)
    app.router.add_post("/api/infrastructure", infrastructure)
    app.router.add_get("/rmf/events", events)
    app.router.add_static("/static", ROOT / "web")
    return app


async def run():
    bridge = HotelBridge()
    executor = SingleThreadedExecutor()
    executor.add_node(bridge)
    runner = web.AppRunner(dashboard(bridge), shutdown_timeout=3)
    task_simulation = asyncio.create_task(bridge.task_simulation.run())
    try:
        await runner.setup()
        port = int(os.environ.get("HOTEL_DASHBOARD_PORT", "8787"))
        await web.TCPSite(runner, os.environ.get("HOTEL_DASHBOARD_HOST", "0.0.0.0"), port).start()
        bridge.get_logger().info(f"Hotel dashboard is listening on port {port}")
        while rclpy.ok():
            executor.spin_once(timeout_sec=0.0)
            await asyncio.sleep(0.003)
    finally:
        task_simulation.cancel()
        with suppress(asyncio.CancelledError):
            await task_simulation
        await runner.cleanup()
        executor.shutdown()
        bridge.link.close()
        bridge.history.close()
        bridge.destroy_node()


def main():
    rclpy.init()
    try:
        asyncio.run(run())
    except (KeyboardInterrupt, rclpy.executors.ExternalShutdownException):
        pass
    finally:
        rclpy.try_shutdown()


if __name__ == "__main__":
    main()
