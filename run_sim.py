"""Isaac Sim standalone entry point for the Open-RMF hotel."""

import argparse
import json
import math
import os
import socket
import time
import traceback
from uuid import uuid4

from hotel_demo import ASSETS, PHYSICS_HZ, SIM_ADDRESS
from hotel_demo.motion import Hotel


def run(app, gui, rate):
    # The Kit runtime loads its USD and PhysX extensions during SimulationApp startup.
    from isaacsim.core.api import World
    from isaacsim.core.utils.stage import open_stage
    from pxr import Gf, UsdGeom, UsdPhysics

    layout = json.loads((ASSETS / "hotel.json").read_text())
    hotel = Hotel(layout)
    stage_path = ASSETS / "hotel.usda"
    if not open_stage(str(stage_path)):
        raise RuntimeError(f"Could not open {stage_path}")
    dt = 1 / PHYSICS_HZ
    world = World(physics_dt=dt, rendering_dt=dt, stage_units_in_meters=1.0)
    stage = world.stage
    for robot in layout["robots"]:
        if robot["role"] in ("Cleaning", "Delivery"):
            model = stage.GetPrimAtPath(f"/World/Robots/{robot['name']}/Visual/Model")
            if not model or not model.GetChildren():
                raise RuntimeError(f"Missing {robot['model']} USD. Run python3 tools/fetch_robot_assets.py.")
    # Keep the generated .usda reusable and write simulation transforms to the session layer.
    stage.SetEditTarget(stage.GetSessionLayer())
    if gui:
        from isaacsim.core.utils.viewports import set_camera_view
        set_camera_view(eye=[47, -66, 57], target=[16, -23, 7])

    translations = {}
    rotations = {}

    def translate(path, x, y, z):
        position = (x, y, z)
        if translations.get(path) != position:
            stage.GetPrimAtPath(path).GetAttribute("xformOp:translate").Set(Gf.Vec3d(*position))
            translations[path] = position

    def rotate(path, yaw):
        if rotations.get(path) != yaw:
            stage.GetPrimAtPath(path).GetAttribute("xformOp:rotateZ").Set(float(yaw))
            rotations[path] = yaw

    def move_door(path, width, kind, swing, fraction):
        count = 2 if kind.startswith("double") else 1
        for index in range(count):
            panel = f"{path}/Panel{index}"
            sign = 1 if index == 0 else -1
            if kind.endswith("sliding"):
                translate(panel, width * index - sign * width * fraction / count, 0, 0)
            else:
                rotate(panel, sign * swing * fraction)

    doors = [door for data in layout["levels"].values() for door in data["doors"]]
    door_widths = {door["name"]: math.dist(door["a"], door["b"]) for door in doors}
    actors = {str(prim.GetPath()): prim for prim in stage.Traverse() if prim.HasAPI(UsdPhysics.RigidBodyAPI)}
    world.reset()
    session = uuid4().hex
    next_step = time.monotonic()
    delay = dt / rate
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as link:
        link.bind(SIM_ADDRESS)
        link.setblocking(False)
        print(f"Isaac Sim loaded {stage_path.name} at {rate:g}× real time", flush=True)
        while app.is_running():
            address = None
            try:
                while True:
                    data, address = link.recvfrom(65535)
                    incoming = json.loads(data)
                    if incoming.get("session") == session:
                        hotel.request(incoming)
            except BlockingIOError:
                pass
            hotel.step(dt)
            for name, robot in hotel.robots.items():
                path = f"/World/Robots/{name}"
                translate(path, robot.x, robot.y, robot.z)
                rotate(path, math.degrees(robot.yaw))
            for door in doors:
                name = door["name"]
                move_door(f"/World/Doors/{name}", door_widths[name], door["type"],
                          door["swing"], hotel.doors[name])
            for name, lift in hotel.lifts.items():
                translate(f"/World/Lifts/{name}", lift.spec["x"], lift.spec["y"], lift.z)
                for level in lift.spec["levels"]:
                    move_door(f"/World/Landings/{name}_{level}", lift.spec["door"]["width"],
                              "double_sliding", 0, lift.door if level == lift.level else 0)

            world.step(render=gui)
            if address is not None:
                cache = UsdGeom.XformCache()
                transforms = {}
                for path, prim in actors.items():
                    matrix = cache.GetLocalToWorldTransform(prim)
                    transforms[path] = [round(value, 6) for row in matrix for value in row]
                state = {"session": session, "step": world.current_time_step_index, "actors": transforms,
                         "robots": [robot.snapshot() for robot in hotel.robots.values()],
                         "lifts": [lift.snapshot() for lift in hotel.lifts.values()], "doors": hotel.doors}
                link.sendto(json.dumps(state, separators=(",", ":")).encode(), address)
            now = time.monotonic()
            next_step = max(next_step + delay, now - delay)
            time.sleep(max(0.0, next_step - now))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gui", action="store_true", help="Show the Isaac Sim viewport on the workstation")
    args = parser.parse_args()
    rate = float(os.environ.get("HOTEL_SIM_RATE", "1"))
    if not math.isfinite(rate) or rate <= 0:
        parser.error("HOTEL_SIM_RATE must be a positive number")

    from isaacsim import SimulationApp

    app = SimulationApp({"headless": not args.gui, "fast_shutdown": False})
    try:
        run(app, args.gui, rate)
    except Exception:
        traceback.print_exc()
        raise
    finally:
        app.close()


if __name__ == "__main__":
    main()
