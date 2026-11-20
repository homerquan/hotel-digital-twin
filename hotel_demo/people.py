"""Run hotel pedestrians with Isaac Sim's native Behavior Simulation."""

import json
import math

from hotel_demo import ASSETS

CHARACTERS = ASSETS / "people"
MOTION_LIBRARY = CHARACTERS / "MotionLibrary/HotelMotionLibrary.usd"
PEOPLE_ROOT = "/World/People"


class Pedestrian:
    def __init__(self, skel_root, joint_count, spec, route, start_time):
        self.path = skel_root
        self.spec = spec
        self.route = route
        self.joint_count = joint_count
        self.agent = None
        self.task = None
        self.next_move = start_time
        self.stop = 1

    def step(self, behavior, now):
        if self.agent is None:
            self.agent = behavior.get_agent(self.path)
            if self.agent is None:
                return
            if len(self.route) == 1:
                self.agent.reset(self.route[0], tuple(self.spec["facing"]))
                return
            self.agent.set_speed(self.spec["speed"])

        if len(self.route) == 1:
            return
        if self.task is not None:
            if self.agent.is_task_running(self.task):
                return
            self.task = None
            self.next_move = now + self.spec["pause"]
        if now >= self.next_move:
            self.task = self.agent.move_to(self.route[self.stop])
            self.stop = (self.stop + 1) % len(self.route)

    def snapshot(self, translation, rotation):
        if self.agent is None:
            return None
        origin = tuple(self.agent.get_world_translation())
        if abs(origin[2] - self.route[0][2]) > .6:
            return None
        pose = [*origin, *self.agent.get_world_rotation()]
        for joint in range(self.joint_count):
            if not self.agent.get_joint_local_transform(joint, translation, rotation):
                return None
            pose.extend(translation)
            pose.extend(rotation)
        return {"name": self.spec["name"], "model": self.spec["model"], "level": self.spec["level"],
                "pose": [round(value, 5) for value in pose]}


class People:
    def __init__(self, stage, layout):
        import carb
        import omni.kit.app
        import omni.kit.commands
        from pxr import Gf, Sdf, Usd, UsdGeom, UsdSkel

        kit = omni.kit.app.get_app()
        manager = kit.get_extension_manager()
        if not manager.set_extension_enabled_immediate("omni.anim.behavior.core", True):
            raise RuntimeError("Could not start Isaac Sim Behavior Simulation")
        import omni.anim.behavior.core as behavior
        import omni.anim.navigation.core as navigation
        from omni.anim.navigation.schema import NAV_MESH_VOLUME

        self.behavior = behavior.acquire_interface()
        self.translation = carb.Float3()
        self.rotation = carb.Float4()
        self.pedestrians = []
        settings = json.loads((ASSETS / "people.json").read_text())
        UsdGeom.Scope.Define(stage, PEOPLE_ROOT)
        routes = []
        for spec in settings["people"]:
            z = layout["levels"][spec["level"]]["z"]
            route = [(x, y, z) for x, y in spec["stops"]]
            routes.append(route)
            # Bake separate walkable areas for guest and staff routes. The
            # robot schedule continues to use the hotel's existing lane graph.
            x, y, _ = zip(*route)
            volume = stage.DefinePrim(f"{PEOPLE_ROOT}/WalkArea{spec['name']}", NAV_MESH_VOLUME)
            volume.GetAttribute("nav:volume:type").Set("Include")
            margin = 1.5 if len(route) == 1 else .9
            UsdGeom.Boundable(volume).CreateExtentAttr([
                (min(x) - margin, min(y) - margin, z - .25),
                (max(x) + margin, max(y) + margin, z + 2.5),
            ])
        # Let the newly enabled navigation extension see the stage and volumes
        # before it enumerates the geometry for the bake.
        for _ in range(3):
            kit.update()
        nav = navigation.acquire_interface()
        if not nav.start_navmesh_baking_and_wait():
            raise RuntimeError("Could not bake the hotel pedestrian routes")
        mesh = nav.get_navmesh()
        if not mesh:
            raise RuntimeError("The pedestrian navigation mesh is empty")

        for index, (spec, route) in enumerate(zip(settings["people"], routes)):
            # Keep stops on a single connected island and reject a projection
            # that lands too far from the requested spot or on another floor.
            waypoints = []
            island = -1
            for point in route:
                closest, island = mesh.query_closest_point(target=point, search_island_id=island)
                if closest is None or math.dist(closest, point) > .6:
                    print(f"Could not place {spec['name']} near {point} on the navigation mesh", flush=True)
                    break
                waypoints.append(tuple(closest))
            if len(waypoints) != len(route):
                continue
            path = Sdf.Path(f"{PEOPLE_ROOT}/{spec['name']}")
            omni.kit.commands.execute(
                "CreateBehaviorAgentCommand",
                character_prim_path=path,
                character_asset_url=str(CHARACTERS / settings["models"][spec["model"]]),
                motion_library_prim_path=Sdf.Path(f"{PEOPLE_ROOT}/Motions"),
                motion_library_asset_url=str(MOTION_LIBRARY),
                motion_library_skeleton_rig="Human",
                position=Gf.Vec3d(*waypoints[0]),
            )
            root = next(prim for prim in Usd.PrimRange(stage.GetPrimAtPath(path)) if prim.IsA(UsdSkel.Root))
            skeleton = next(UsdSkel.Skeleton(prim) for prim in Usd.PrimRange(root) if prim.IsA(UsdSkel.Skeleton))
            self.pedestrians.append(Pedestrian(
                str(root.GetPath()), len(skeleton.GetJointsAttr().Get()), spec, waypoints, 1 + .7 * index,
            ))
        print(f"Behavior Simulation configured for {len(self.pedestrians)} hotel pedestrians", flush=True)

    def step(self, now):
        for person in self.pedestrians:
            person.step(self.behavior, now)

    def snapshot(self):
        return [pose for person in self.pedestrians if (pose := person.snapshot(self.translation, self.rotation))]
