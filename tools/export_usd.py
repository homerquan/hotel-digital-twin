"""Build the Isaac Sim stage from the hotel geometry and robot spawns."""

import json
import math
from pathlib import Path
from tempfile import NamedTemporaryFile

from pxr import Gf, Sdf, Usd, UsdGeom, UsdLux, UsdPhysics, UsdShade

ASSETS = Path(__file__).resolve().parents[1] / "assets"
FLOOR_COLORS = {
    "concrete2": (0.43, 0.51, 0.56),
    "red_tiles": (0.56, 0.33, 0.27),
    "white_beige_tiles": (0.70, 0.66, 0.56),
    "suite_corridor": (0.67, 0.63, 0.55),
    "suite_bath_tile": (0.69, 0.71, 0.70),
}
ROBOT_ASSETS = {
    "Cleaning": ("robots/robotics_small_bot_a01/simready_usd/sm_robotics_small_bot_a01_01.usd",
                 {"PhysX": "Disabled", "MuJoCo": "Disabled", "Newton": "Disabled"}),
    "Delivery": ("robots/g2_omnipicker/simready_usd/g2_omnipicker.usda", {"Physics": "none"}),
}
FURNITURE_FILES = {
    "bed": "bed.usdz",
    "sink": "vanity_sink_faucet.usda",
    "shower": "shower_cabin.usda",
}
# The OmniPicker's arm links rotate about their local Z axis, in degrees.
OMNIPICKER_REST = {
    "arm_l_link1": 90.0, "arm_l_link2": -83.0, "arm_l_link4": -10.0,
    "arm_r_link1": 90.0, "arm_r_link2": 83.0, "arm_r_link4": 10.0,
}


def pose(prim, position, yaw=0):
    xform = UsdGeom.Xformable(prim)
    xform.AddTranslateOp().Set(Gf.Vec3d(*position))
    xform.AddRotateZOp().Set(float(yaw))
    return xform


def mover(stage, path, position, yaw=0):
    prim = UsdGeom.Xform.Define(stage, path).GetPrim()
    pose(prim, position, yaw)
    body = UsdPhysics.RigidBodyAPI.Apply(prim)
    body.CreateKinematicEnabledAttr(True)
    return prim


def box(stage, path, center, size, color, yaw=0, collision=True):
    cube = UsdGeom.Cube.Define(stage, path)
    cube.CreateSizeAttr(1.0)
    pose(cube.GetPrim(), center, yaw).AddScaleOp().Set(Gf.Vec3f(*size))
    cube.CreateDisplayColorPrimvar().Set([Gf.Vec3f(*color)])
    if collision:
        UsdPhysics.CollisionAPI.Apply(cube.GetPrim())
    return cube


def parquet_material(stage, path):
    material = UsdShade.Material.Define(stage, path)
    shade = UsdShade.Shader.Define(stage, f"{path}/Surface")
    shade.CreateIdAttr("UsdPreviewSurface")
    material.CreateSurfaceOutput().ConnectToSource(shade.CreateOutput("surface", Sdf.ValueTypeNames.Token))
    reader = UsdShade.Shader.Define(stage, f"{path}/UV")
    reader.CreateIdAttr("UsdPrimvarReader_float2")
    reader.CreateInput("varname", Sdf.ValueTypeNames.String).Set("st")
    st = reader.CreateOutput("result", Sdf.ValueTypeNames.Float2)

    for name, channel, space in (("Color", "diff", "sRGB"), ("Normal", "nor_gl", "raw"), ("ORM", "arm", "raw")):
        relative = f"furniture/parquet/herringbone_parquet_{channel}_1k.jpg"
        if not (ASSETS / relative).is_file():
            raise FileNotFoundError("Run tools/fetch_furniture_assets.py to obtain the parquet maps.")
        node = UsdShade.Shader.Define(stage, f"{path}/{name}")
        node.CreateIdAttr("UsdUVTexture")
        node.CreateInput("st", Sdf.ValueTypeNames.Float2).ConnectToSource(st)
        node.CreateInput("file", Sdf.ValueTypeNames.Asset).Set(Sdf.AssetPath(f"./{relative}"))
        node.CreateInput("sourceColorSpace", Sdf.ValueTypeNames.Token).Set(space)
        node.CreateInput("wrapS", Sdf.ValueTypeNames.Token).Set("repeat")
        node.CreateInput("wrapT", Sdf.ValueTypeNames.Token).Set("repeat")
        if channel == "arm":
            for key, component in (("occlusion", "r"), ("roughness", "g"), ("metallic", "b")):
                shade.CreateInput(key, Sdf.ValueTypeNames.Float).ConnectToSource(
                    node.CreateOutput(component, Sdf.ValueTypeNames.Float))
        elif channel == "nor_gl":
            node.CreateInput("scale", Sdf.ValueTypeNames.Float4).Set(Gf.Vec4f(2, 2, 2, 1))
            node.CreateInput("bias", Sdf.ValueTypeNames.Float4).Set(Gf.Vec4f(-1, -1, -1, 0))
            shade.CreateInput("normal", Sdf.ValueTypeNames.Normal3f).ConnectToSource(
                node.CreateOutput("rgb", Sdf.ValueTypeNames.Float3))
        else:
            shade.CreateInput("diffuseColor", Sdf.ValueTypeNames.Color3f).ConnectToSource(
                node.CreateOutput("rgb", Sdf.ValueTypeNames.Float3))
    return material


def slab(stage, path, triangles, z, color, finish=None):
    mesh = UsdGeom.Mesh.Define(stage, path)
    points = [Gf.Vec3f(x, y, z) for triangle in triangles for x, y in triangle]
    mesh.CreatePointsAttr(points)
    mesh.CreateFaceVertexCountsAttr([3] * len(triangles))
    mesh.CreateFaceVertexIndicesAttr(list(range(len(points))))
    mesh.CreateSubdivisionSchemeAttr(UsdGeom.Tokens.none)
    mesh.CreateDisplayColorPrimvar().Set([Gf.Vec3f(*color)])
    mesh.CreateDoubleSidedAttr(True)
    UsdPhysics.CollisionAPI.Apply(mesh.GetPrim())
    if finish:
        prim = mesh.GetPrim()
        uv = UsdGeom.PrimvarsAPI(prim).CreatePrimvar("st", Sdf.ValueTypeNames.TexCoord2fArray, UsdGeom.Tokens.vertex)
        uv.Set([Gf.Vec2f(point[0] / 3.4, point[1] / 3.4) for point in points])
        UsdShade.MaterialBindingAPI.Apply(prim).Bind(finish)
        prim.SetCustomDataByKey("texturedFloor", True)


def door(stage, path, details, z):
    (ax, ay), (bx, by) = details["a"], details["b"]
    width = math.hypot(bx - ax, by - ay)
    yaw = math.degrees(math.atan2(by - ay, bx - ax))
    double = details["type"].startswith("double")
    count = 2 if double else 1
    root = UsdGeom.Xform.Define(stage, path)
    pose(root.GetPrim(), (ax, ay, z), yaw)
    for index in range(count):
        direction = 1 if index == 0 else -1
        mover(stage, f"{path}/Panel{index}", (width * index, 0, 0))
        box(stage, f"{path}/Panel{index}/Leaf", (direction * width / (2 * count), 0, 1.05),
            (width / count - 0.015, 0.06, 2.1), (0.24, 0.38, 0.48))


def lift(stage, details):
    name, w, d = details["name"], details["width"], details["depth"]
    x, y = details["x"], details["y"]
    yaw = math.degrees(details["yaw"])
    path = f"/World/Lifts/{name}"
    mover(stage, path, (x, y, details["levels"][details["level"]]), yaw)
    metal = (0.34, 0.43, 0.52)
    box(stage, f"{path}/Deck", (0, 0, -0.06), (w, d, 0.12), metal)
    entry = details["door"]
    side = math.copysign(1.0, entry["y"])
    for label, panel_x in [("Left", -w / 2), ("Right", w / 2)]:
        box(stage, f"{path}/{label}", (panel_x, 0, 1.1), (0.07, d, 2.2), metal)
    box(stage, f"{path}/Back", (0, -side * d / 2, 1.1), (w, 0.07, 2.2), metal)
    # Landing doors use independent rigid bodies on each floor.
    for level, z in details["levels"].items():
        half = entry["width"] / 2
        ends = []
        for offset in (-half, half):
            dx, dy = entry["x"] + offset, entry["y"]
            ends.append((x + dx * math.cos(details["yaw"]) - dy * math.sin(details["yaw"]),
                         y + dx * math.sin(details["yaw"]) + dy * math.cos(details["yaw"])))
        door(stage, f"/World/Landings/{name}_{level}",
             {"a": ends[0], "b": ends[1], "type": "double_sliding"}, z)


def open_asset(path, variants, fetch_script):
    if not path.is_file():
        raise FileNotFoundError(f"Run tools/{fetch_script} to obtain {path}")
    source = Usd.Stage.Open(str(path), load=Usd.Stage.LoadNone)
    source.SetEditTarget(source.GetSessionLayer())
    root = source.GetDefaultPrim()
    for name, selection in variants.items():
        root.GetVariantSet(name).SetVariantSelection(selection)
    source.Load()
    return source


def visual_reference(stage, path, source, asset_path, variants, rotations=None):
    model = UsdGeom.Xform.Define(stage, path).GetPrim()
    for name, selection in variants.items():
        model.GetVariantSet(name).SetVariantSelection(selection)
    model.GetReferences().AddReference(asset_path)

    # Keep the imported meshes attached to their hotel prim, including links
    # whose source transform stacks reset to the original stage coordinates.
    root = source.GetDefaultPrim()
    transform = UsdGeom.XformCache()
    for prim in Usd.PrimRange(root):
        relative = prim.GetPath().MakeRelativePath(root.GetPath())
        composed = stage.GetPrimAtPath(model.GetPath().AppendPath(relative))
        if not composed:
            continue
        if prim.IsA(UsdPhysics.Joint) or prim.IsA(UsdPhysics.Scene):
            composed.SetActive(False)
            continue
        for api in (UsdPhysics.RigidBodyAPI, UsdPhysics.CollisionAPI, UsdPhysics.ArticulationRootAPI):
            if prim.HasAPI(api):
                composed.RemoveAPI(api)
        xform = UsdGeom.Xformable(prim)
        angle = (rotations or {}).get(prim.GetName(), 0.0)
        if xform and (xform.GetResetXformStack() or angle):
            local = transform.GetLocalToWorldTransform(prim) * transform.GetParentToWorldTransform(prim).GetInverse()
            if angle:
                local = Gf.Matrix4d().SetRotate(Gf.Rotation(Gf.Vec3d(0, 0, 1), angle)) * local
            result = UsdGeom.Xformable(composed)
            result.MakeMatrixXform().Set(local)
            result.SetResetXformStack(False)


def asset_bounds(prim):
    return UsdGeom.BBoxCache(Usd.TimeCode.Default(), ["default", "render"]).ComputeWorldBound(prim).ComputeAlignedRange()


def robot_asset(stage, path, role, radius):
    relative_path, variants = ROBOT_ASSETS[role]
    source = open_asset(ASSETS / relative_path, variants, "fetch_robot_assets.py")
    bounds = asset_bounds(source.GetDefaultPrim())
    center = bounds.GetMidpoint()
    dx, dy, _ = bounds.GetSize()
    scale = min(1.0, 1.8 * radius / max(dx, dy))
    visual = UsdGeom.Xform.Define(stage, f"{path}/Visual")
    offset = (-center[0] * scale, -center[1] * scale, -bounds.GetMin()[2] * scale)
    pose(visual.GetPrim(), offset).AddScaleOp().Set(Gf.Vec3f(scale))
    rotations = OMNIPICKER_REST if role == "Delivery" else None
    visual_reference(stage, f"{path}/Visual/Model", source, f"./{relative_path}", variants, rotations)


def furniture_template(kind):
    asset = "sink" if kind == "suite_vanity" else kind
    folder = ASSETS / "furniture" / asset
    filename = FURNITURE_FILES.get(asset, "model.usdc")
    variants = {"physics": "none"} if asset in ("sink", "shower") else {}
    if kind == "suite_vanity":
        variants["finish"] = "marble_brass"
    source = open_asset(folder / filename, variants, "fetch_furniture_assets.py")
    output = ASSETS / "furniture_visuals" / f"{kind}.usda"
    output.parent.mkdir(exist_ok=True)
    with NamedTemporaryFile(suffix=".usda", dir=output.parent) as temporary:
        stage = Usd.Stage.CreateNew(temporary.name)
        root = UsdGeom.Xform.Define(stage, "/Furniture").GetPrim()
        stage.SetDefaultPrim(root)
        UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.z)
        UsdGeom.SetStageMetersPerUnit(stage, 1.0)
        anchor = UsdGeom.Xform.Define(stage, "/Furniture/Center").GetPrim()
        visual_reference(stage, "/Furniture/Center/Model", source, f"../furniture/{asset}/{filename}", variants)
        bounds = asset_bounds(root)
        center = bounds.GetMidpoint()
        # The shower's drain is below the tray and sinks into the floor.
        bottom = 0.0 if kind == "shower" else bounds.GetMin()[2]
        pose(anchor, (-center[0], -center[1], -bottom))
        stage.GetRootLayer().Export(str(output))
    dx, dy, _ = bounds.GetSize()
    return f"./{output.relative_to(ASSETS).as_posix()}", (dx, dy, bounds.GetMax()[2] - bottom)


def furnishings(stage, path, items, z, templates):
    for index, item in enumerate(items):
        kind = item["kind"]
        asset, extents = templates[kind]
        prim = UsdGeom.Xform.Define(stage, f"{path}/{kind}_{index:02d}").GetPrim()
        x, y = item["position"]
        dimensions = [size / original for size, original in zip(item["size"], extents)]
        pose(prim, (x, y, z + item["height"]), math.degrees(item["yaw"])).AddScaleOp().Set(Gf.Vec3f(*dimensions))
        prim.GetReferences().AddReference(asset)
        prim.SetInstanceable(True)
        prim.SetCustomDataByKey("furnishing", kind)


def reception_sign(stage, path, z):
    width, height = 1.74, 0.48
    root = UsdGeom.Xform.Define(stage, path)
    pose(root.GetPrim(), (19.797, -26.241, z + 1.79), 1.06)
    box(stage, f"{path}/BrassEdge", (0, 0, 0), (width, 0.037, height),
        (0.34, 0.21, 0.098), collision=False)

    face = UsdGeom.Mesh.Define(stage, f"{path}/EngravedFace")
    prim = face.GetPrim()
    prim.SetCustomDataByKey("texturedDecoration", True)
    x, h, y = width / 2 - .005, height / 2 - .005, -.019
    face.CreatePointsAttr([(-x, y, -h), (x, y, -h), (x, y, h), (-x, y, h)])
    face.CreateFaceVertexCountsAttr([4])
    face.CreateFaceVertexIndicesAttr([0, 1, 2, 3])
    face.CreateSubdivisionSchemeAttr(UsdGeom.Tokens.none)
    uv = UsdGeom.PrimvarsAPI(prim).CreatePrimvar("st", Sdf.ValueTypeNames.TexCoord2fArray, UsdGeom.Tokens.vertex)
    uv.Set([(0, 0), (1, 0), (1, 1), (0, 1)])

    material = UsdShade.Material.Define(stage, f"{path}/BrushedBrass")
    surface = UsdShade.Shader.Define(stage, f"{path}/BrushedBrass/Surface")
    surface.CreateIdAttr("UsdPreviewSurface")
    surface.CreateInput("metallic", Sdf.ValueTypeNames.Float).Set(.28)
    surface.CreateInput("roughness", Sdf.ValueTypeNames.Float).Set(.51)
    material.CreateSurfaceOutput().ConnectToSource(surface.CreateOutput("surface", Sdf.ValueTypeNames.Token))
    reader = UsdShade.Shader.Define(stage, f"{path}/BrushedBrass/UV")
    reader.CreateIdAttr("UsdPrimvarReader_float2")
    reader.CreateInput("varname", Sdf.ValueTypeNames.String).Set("st")
    texture = UsdShade.Shader.Define(stage, f"{path}/BrushedBrass/Engraving")
    texture.CreateIdAttr("UsdUVTexture")
    texture.CreateInput("st", Sdf.ValueTypeNames.Float2).ConnectToSource(
        reader.CreateOutput("result", Sdf.ValueTypeNames.Float2))
    texture.CreateInput("file", Sdf.ValueTypeNames.Asset).Set(Sdf.AssetPath("./reception_sign.jpg"))
    texture.CreateInput("sourceColorSpace", Sdf.ValueTypeNames.Token).Set("sRGB")
    for axis in ("wrapS", "wrapT"):
        texture.CreateInput(axis, Sdf.ValueTypeNames.Token).Set("clamp")
    surface.CreateInput("diffuseColor", Sdf.ValueTypeNames.Color3f).ConnectToSource(
        texture.CreateOutput("rgb", Sdf.ValueTypeNames.Float3))
    UsdShade.MaterialBindingAPI.Apply(prim).Bind(material)


def robot(stage, spec, z):
    path = f"/World/Robots/{spec['name']}"
    mover(stage, path, (spec["x"], spec["y"], z), math.degrees(spec["yaw"]))
    diameter = spec["radius"] * 1.25
    rgb = tuple(int(spec["color"][index:index + 2], 16) / 255 for index in (1, 3, 5))
    height = {"Service": 0.28, "Cleaning": 0.46, "Delivery": 0.78}[spec["role"]]
    chassis = box(stage, f"{path}/Chassis", (0, 0, height / 2 + 0.06), (diameter, diameter * 0.8, height), rgb)
    if spec["role"] in ROBOT_ASSETS:
        chassis.MakeInvisible()
        robot_asset(stage, path, spec["role"], spec["radius"])
        return
    box(stage, f"{path}/Bumper", (diameter / 2, 0, 0.14), (0.07, diameter * 0.8, 0.13), (0.11, 0.14, 0.19))
    box(stage, f"{path}/StatusLight", (diameter * 0.25, 0, height + 0.08),
        (diameter * 0.2, diameter * 0.48, 0.035), (0.48, 0.95, 0.81), collision=False)


def build(stage):
    layout = json.loads((ASSETS / "hotel.json").read_text())
    world = UsdGeom.Xform.Define(stage, "/World")
    stage.SetDefaultPrim(world.GetPrim())
    UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.z)
    UsdGeom.SetStageMetersPerUnit(stage, 1.0)
    stage.SetTimeCodesPerSecond(60)
    stage.SetStartTimeCode(0)
    stage.SetEndTimeCode(60 * 60 * 60 * 24)
    scene = UsdPhysics.Scene.Define(stage, "/World/Physics")
    scene.CreateGravityDirectionAttr(Gf.Vec3f(0, 0, -1))
    scene.CreateGravityMagnitudeAttr(9.81)
    UsdLux.DomeLight.Define(stage, "/World/Ambient").CreateIntensityAttr(700)
    sun = UsdLux.DistantLight.Define(stage, "/World/KeyLight")
    sun.CreateIntensityAttr(2500)
    UsdGeom.Xformable(sun).AddRotateXYZOp().Set(Gf.Vec3f(36, -24, -18))

    kinds = {item["kind"] for data in layout["levels"].values() for item in data.get("furnishings", [])}
    templates = {kind: furniture_template(kind) for kind in sorted(kinds)}
    for level, data in layout["levels"].items():
        path = f"/World/Hotel/{level}"
        UsdGeom.Scope.Define(stage, path)
        z = data["z"]
        finish = parquet_material(stage, f"{path}/Materials/Parquet") if any(
            floor["material"] == "parquet" for floor in data["floors"]) else None
        for index, floor in enumerate(data["floors"]):
            color = FLOOR_COLORS.get(floor["material"], (0.63, 0.65, 0.66))
            slab(stage, f"{path}/Floors/Slab{index}", floor["triangles"], z, color,
                 finish if floor["material"] == "parquet" else None)
        wall_color = (0.89, 0.86, 0.81) if level == "L3" else (0.85, 0.86, 0.9)
        for index, (ax, ay, bx, by, height) in enumerate(data["walls"]):
            length = math.hypot(bx - ax, by - ay)
            if length < 0.02:
                continue
            box(stage, f"{path}/Walls/Wall{index}", ((ax + bx) / 2, (ay + by) / 2, z + height / 2),
                (length + 0.09, 0.13, height), wall_color, math.degrees(math.atan2(by - ay, bx - ax)))
        for index, prop in enumerate(data["props"]):
            x, y = prop["position"]
            box(stage, f"{path}/Furniture/Prop{index}", (x, y, z + prop["size"][2] / 2), prop["size"],
                (0.40, 0.46, 0.42), math.degrees(prop["yaw"]), collision=False)
        furnishings(stage, f"{path}/Furnishings", data.get("furnishings", []), z, templates)
        if level == "L1":
            reception_sign(stage, f"{path}/ReceptionSign", z)
        for entry in data["doors"]:
            door(stage, f"/World/Doors/{entry['name']}", entry, z)
    for entry in layout["lifts"]:
        lift(stage, entry)
    for entry in layout["robots"]:
        robot(stage, entry, layout["levels"][entry["level"]]["z"])


def main():
    with NamedTemporaryFile(suffix=".usda", dir=ASSETS) as temporary:
        stage = Usd.Stage.CreateNew(temporary.name)
        build(stage)
        stage.GetRootLayer().Export(str(ASSETS / "hotel.usda"))


if __name__ == "__main__":
    main()
