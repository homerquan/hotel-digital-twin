"""Export the hotel USD meshes for interactive browser rendering."""

from collections import defaultdict
from functools import lru_cache
from hashlib import sha256
from io import BytesIO
import json
import math
from pathlib import Path

import numpy as np
from PIL import Image
import trimesh
from trimesh.exchange.gltf import export_glb
from pxr import Ar, Usd, UsdGeom, UsdPhysics, UsdShade

ROOT = Path(__file__).resolve().parents[1]


def visible(prim):
    image = UsdGeom.Imageable(prim)
    return image.ComputeVisibility() != UsdGeom.Tokens.invisible and image.ComputePurpose() not in ("guide", "proxy")


def triangle_corners(shape):
    counts = np.asarray(shape.GetFaceVertexCountsAttr().Get())
    if np.all(counts == 3):
        triangles = np.arange(counts.size * 3).reshape(-1, 3)
    else:
        triangles = []
        start = 0
        for count in counts:
            triangles.extend((start, start + i, start + i + 1) for i in range(1, count - 1))
            start += count
        triangles = np.asarray(triangles).reshape(-1, 3)
    return triangles[:, ::-1] if shape.GetOrientationAttr().Get() == UsdGeom.Tokens.leftHanded else triangles


def smooth_normals(mesh):
    points = mesh.vertices[mesh.faces]
    normals = np.cross(points[:, 1] - points[:, 0], points[:, 2] - points[:, 0])
    vertex_normals = np.zeros_like(mesh.vertices)
    for slot in range(3):
        np.add.at(vertex_normals, mesh.faces[:, slot], normals)
    norms = np.linalg.norm(vertex_normals, axis=1, keepdims=True)
    mesh.vertex_normals = vertex_normals / np.maximum(norms, 1e-9)


def mesh_color(prim):
    material, _ = UsdShade.MaterialBindingAPI(prim).ComputeBoundMaterial()
    if material:
        shader, _, _ = material.ComputeSurfaceSource()
        color = shader.GetInput("diffuseColor").Get() if shader else None
        if color is not None:
            return color
        name = material.GetPrim().GetName()
        for fragment, color in (("glass__blue", (.18, .40, .65)), ("glass__red", (.60, .14, .20)),
                                ("rubber", (.045, .05, .07)), ("copper", (.65, .36, .20)),
                                ("painted", (.61, .67, .78))):
            if fragment in name:
                return color
    colors = UsdGeom.Gprim(prim).GetDisplayColorAttr().Get()
    return colors[0] if colors else (0.76, 0.78, 0.83)


def shape_mesh(prim):
    if prim.IsA(UsdGeom.Cube):
        size = UsdGeom.Cube(prim).GetSizeAttr().Get()
        mesh = trimesh.creation.box(extents=[size] * 3)
        mesh.unmerge_vertices()
    elif prim.IsA(UsdGeom.Mesh):
        shape = UsdGeom.Mesh(prim)
        points = shape.GetPointsAttr().Get()
        indices = shape.GetFaceVertexIndicesAttr().Get()
        if not points or not indices:
            return None
        faces = np.asarray(indices)[triangle_corners(shape)]
        mesh = trimesh.Trimesh(vertices=points, faces=faces, process=False)
    else:
        return None
    color = [int(np.clip(c, 0, 1) * 255) for c in mesh_color(prim)]
    mesh.visual.vertex_colors = [*color, 255]
    return mesh


@lru_cache(maxsize=None)
def texture_image(resolved):
    data = Ar.GetResolver().OpenAsset(Ar.ResolvedPath(resolved))
    if data is None:
        raise FileNotFoundError(f"Cannot read USD texture {resolved}")
    with Image.open(BytesIO(data.GetBuffer())) as image:
        preview = image.convert("RGB")
        preview.thumbnail((1024, 1024))
        return preview


def image_paths(node):
    attr = input_source(node.GetInput("file"))
    path = attr.Get()
    if UsdShade.UdimUtils.IsUdimIdentifier(path.path):
        layer = attr.GetPropertyStack()[0].layer
        tiles = UsdShade.UdimUtils.ResolveUdimTilePaths(path.path, layer)
        if not tiles:
            raise FileNotFoundError(f"Cannot find tiles for {path.path}")
        return tuple((resolved, int(tile) - 1001) for resolved, tile in tiles)
    return ((path.resolvedPath, 0),)


@lru_cache(maxsize=None)
def texture_tiles(paths):
    if len(paths) == 1 and paths[0][1] == 0:
        return texture_image(paths[0][0]), (1, 1)
    # USD uses integer UV offsets for UDIM tiles. glTF samples one image,
    # so pack the source images in the same grid and scale the mesh UVs.
    columns = max(tile % 10 for _, tile in paths) + 1
    rows = max(tile // 10 for _, tile in paths) + 1
    width, height = texture_image(paths[0][0]).size
    atlas = Image.new("RGB", (columns * width, rows * height))
    for path, tile in paths:
        image = texture_image(path)
        if image.size != (width, height):
            image = image.resize((width, height), Image.Resampling.LANCZOS)
        atlas.paste(image, ((tile % 10) * width, (rows - 1 - tile // 10) * height))
    return atlas, (columns, rows)


def input_source(input_value):
    return next(iter(input_value.GetValueProducingAttributes()), None) if input_value else None


def connected_shader(input_value):
    source = input_source(input_value)
    if source and source.GetName().startswith("outputs:"):
        return UsdShade.Shader(source.GetPrim())
    return None


def texture_source(shader, key):
    node = connected_shader(shader.GetInput(key)) if shader else None
    if node and node.GetIdAttr().Get() == "UsdUVTexture":
        return node
    return None


def surface_value(shader, name, default):
    source = input_source(shader.GetInput(name)) if shader else None
    value = source.Get() if source else None
    return default if value is None else value


@lru_cache(maxsize=None)
def packed_surface_image(paths, roughness_channel, metallic_channel):
    image, grid = texture_tiles(paths)
    if (roughness_channel, metallic_channel) == ("g", "b"):
        return image, grid
    channels = dict(zip("rgb", image.split()))
    return Image.merge("RGB", (channels["r"], channels[roughness_channel], channels[metallic_channel])), grid


def preview_material(material):
    shader, _, _ = material.ComputeSurfaceSource() if material else (None, None, None)
    diffuse = texture_source(shader, "diffuseColor")
    normal = texture_source(shader, "normal")
    roughness = texture_source(shader, "roughness")
    metallic = texture_source(shader, "metallic")
    grid = (1, 1)
    orm = normal_map = color_map = None
    if roughness and metallic and roughness.GetPath() == metallic.GetPath():
        roughness_channel = input_source(shader.GetInput("roughness")).GetBaseName()
        metallic_channel = input_source(shader.GetInput("metallic")).GetBaseName()
        if roughness_channel in ("r", "g", "b") and metallic_channel in ("r", "g", "b"):
            orm, grid = packed_surface_image(image_paths(roughness), roughness_channel, metallic_channel)
    if normal:
        normal_map, grid = texture_tiles(image_paths(normal))
    if diffuse:
        color_map, grid = texture_tiles(image_paths(diffuse))
    color = (1, 1, 1) if diffuse else surface_value(shader, "diffuseColor", (.76, .78, .83))
    opacity = surface_value(shader, "opacity", 1.0)
    appearance = trimesh.visual.material.PBRMaterial(
        name=material.GetPrim().GetName() if material else "Default",
        baseColorFactor=(*color, opacity),
        baseColorTexture=color_map,
        normalTexture=normal_map,
        metallicRoughnessTexture=orm,
        occlusionTexture=orm,
        metallicFactor=1.0 if orm else surface_value(shader, "metallic", 0.0),
        roughnessFactor=1.0 if orm else surface_value(shader, "roughness", 0.6),
        emissiveFactor=surface_value(shader, "emissiveColor", None),
        alphaMode="BLEND" if opacity < 1 else "OPAQUE",
        doubleSided=True,
    )
    return appearance, (diffuse or normal or (roughness if orm else None), grid)


def corner_values(values, interpolation, shape, point_ids):
    values = np.asarray(values)
    if interpolation in ("vertex", "varying"):
        return values[point_ids]
    if interpolation == "uniform":
        return np.repeat(values, np.asarray(shape.GetFaceVertexCountsAttr().Get()), axis=0)
    if interpolation == "constant":
        return np.repeat(values[:1], len(point_ids), axis=0)
    return values


def texture_coordinates(prim, shape, texture, grid, point_ids):
    reader = connected_shader(texture.GetInput("st")) if texture else None
    transform = None
    if reader and reader.GetIdAttr().Get() == "UsdTransform2d":
        transform = reader
        reader = connected_shader(transform.GetInput("in"))
    name = surface_value(reader, "varname", "st")
    uv = UsdGeom.PrimvarsAPI(prim).FindPrimvarWithInheritance(name)
    if not uv or not uv.HasValue():
        return None
    coords = corner_values(uv.ComputeFlattened(), uv.GetInterpolation(), shape, point_ids).astype(float)
    if transform:
        angle = math.radians(surface_value(transform, "rotation", 0.0))
        c, s = math.cos(angle), math.sin(angle)
        coords *= surface_value(transform, "scale", (1.0, 1.0))
        coords = coords @ np.array([[c, s], [-s, c]]) + surface_value(transform, "translation", (0.0, 0.0))
    return coords / grid


def material_triangles(shape, corners):
    binding = UsdShade.MaterialBindingAPI(shape)
    default, _ = binding.ComputeBoundMaterial()
    subsets = binding.GetMaterialBindSubsets()
    if not subsets:
        yield default, corners
        return

    face_end = np.cumsum(np.asarray(shape.GetFaceVertexCountsAttr().Get()))
    face_ids = np.searchsorted(face_end, corners[:, 0], side="right")
    unassigned = np.ones(len(corners), dtype=bool)
    for subset in subsets:
        selected = np.isin(face_ids, np.asarray(subset.GetIndicesAttr().Get()))
        if selected.any():
            material, _ = UsdShade.MaterialBindingAPI(subset).ComputeBoundMaterial()
            yield material or default, corners[selected]
            unassigned &= ~selected
    if unassigned.any():
        yield default, corners[unassigned]


def model_meshes(prim, material_for):
    shape = UsdGeom.Mesh(prim)
    points = shape.GetPointsAttr().Get()
    indices = shape.GetFaceVertexIndicesAttr().Get()
    if not points or not indices:
        return
    points = np.asarray(points)
    point_ids = np.asarray(indices)
    corners = triangle_corners(shape)
    normals = shape.GetNormalsAttr().Get()
    if normals is None or not len(normals):
        mesh = trimesh.Trimesh(vertices=points, faces=point_ids[corners], process=False)
        smooth_normals(mesh)
        corner_normals = mesh.vertex_normals[point_ids]
    else:
        corner_normals = corner_values(normals, shape.GetNormalsInterpolation(), shape, point_ids)

    for material, triangles in material_triangles(shape, corners):
        key, (texture, grid) = material_for(material)
        selected = triangles.reshape(-1)
        fields = [point_ids[selected, None], corner_normals[selected]]
        uv = texture_coordinates(prim, shape, texture, grid, point_ids) if texture else None
        if uv is not None:
            fields.append(uv[selected])
        keys, face_ids = np.unique(np.column_stack(fields), axis=0, return_inverse=True)
        mesh = trimesh.Trimesh(vertices=points[keys[:, 0].astype(int)], faces=face_ids.reshape(-1, 3),
                               vertex_normals=keys[:, 1:4], process=False)
        yield key, mesh, keys[:, 4:] if uv is not None else None


def export_material_model(scene, root, frame, model_name):
    meshes = defaultdict(list)
    appearances = {}

    def material_for(material):
        key = material.GetPath() if material else None
        if key not in appearances:
            appearances[key] = preview_material(material)
        return key, appearances[key][1]

    origin = frame.GetLocalToWorldTransform(root).GetInverse()
    for prim in Usd.PrimRange(root, Usd.TraverseInstanceProxies()):
        if not prim.IsA(UsdGeom.Mesh) or not visible(prim):
            continue
        local = np.asarray(frame.GetLocalToWorldTransform(prim) * origin).T
        for key, mesh, uv in model_meshes(prim, material_for):
            mesh.apply_transform(local)
            meshes[key].append((mesh, uv))

    geometry = []
    for key, parts in meshes.items():
        mesh = parts[0][0] if len(parts) == 1 else trimesh.util.concatenate([part for part, _ in parts])
        uv = None
        if any(coords is not None for _, coords in parts):
            uv = np.concatenate([coords if coords is not None else np.zeros((len(part.vertices), 2))
                                 for part, coords in parts])
        mesh.visual = trimesh.visual.texture.TextureVisuals(uv=uv, material=appearances[key][0])
        name = f"{model_name}_Material{len(geometry)}"
        scene.geometry[name] = mesh
        geometry.append(name)
    return geometry


def place_model(scene, root, parent, matrix, geometry):
    scene.graph.update(frame_to=str(root), frame_from=str(parent), matrix=np.asarray(matrix).T)
    for mesh in geometry:
        scene.graph.update(frame_to=f"{root}/{mesh}", frame_from=str(root), geometry=mesh)


def main():
    stage = Usd.Stage.Open(str(ROOT / "assets/hotel.usda"))
    frame = UsdGeom.XformCache()
    movers = {prim.GetPath() for prim in stage.Traverse() if prim.HasAPI(UsdPhysics.RigidBodyAPI)}
    furnishings = [prim for prim in stage.Traverse() if prim.GetCustomDataByKey("furnishing")]
    textured_floors = [prim for prim in stage.Traverse() if prim.GetCustomDataByKey("texturedFloor")]
    scenery_models = furnishings + textured_floors
    layout = json.loads((ROOT / "assets/hotel.json").read_text())
    robot_visuals = {}
    for spec in layout["robots"]:
        body = stage.GetPrimAtPath(f"/World/Robots/{spec['name']}")
        visual = body.GetChild("Visual")
        if visual:
            robot_visuals[body.GetPath()] = (visual, spec["role"])
    model_paths = {prim.GetPath() for prim in scenery_models} | robot_visuals.keys()
    grouped_meshes = defaultdict(list)
    group_transforms = {}
    for prim in Usd.PrimRange(stage.GetDefaultPrim(), Usd.TraverseInstanceProxies()):
        if not prim.IsA(UsdGeom.Mesh) and not prim.IsA(UsdGeom.Cube):
            continue
        hierarchy = prim.GetPath().GetPrefixes()
        if any(path in model_paths for path in hierarchy) or not visible(prim):
            continue
        mesh = shape_mesh(prim)
        if mesh is None:
            continue
        group = next((path for path in reversed(hierarchy) if path in movers), hierarchy[2])
        if group not in group_transforms:
            group_transforms[group] = frame.GetLocalToWorldTransform(stage.GetPrimAtPath(group))
        local = frame.GetLocalToWorldTransform(prim) * group_transforms[group].GetInverse()
        mesh.apply_transform(np.asarray(local).T)
        grouped_meshes[group].append(mesh)

    scene = trimesh.Scene()
    geometry_keys = {}
    for group, meshes in grouped_meshes.items():
        mesh = trimesh.util.concatenate(meshes)
        digest = sha256()
        digest.update(np.round(mesh.vertices, 6).tobytes())
        digest.update(mesh.faces.tobytes())
        digest.update(mesh.visual.vertex_colors.tobytes())
        key = digest.digest()
        transform = np.asarray(group_transforms[group]).T
        if key in geometry_keys:
            scene.graph.update(frame_to=str(group), matrix=transform, geometry=geometry_keys[key])
            continue
        smooth_normals(mesh)
        mesh_name = f"{group}/PreviewMesh"
        scene.add_geometry(mesh, node_name=str(group), geom_name=mesh_name, transform=transform)
        geometry_keys[key] = mesh_name

    model_geometry = {}
    for root in scenery_models:
        path = root.GetPath()
        kind = root.GetCustomDataByKey("furnishing")
        key = kind or path
        if key not in model_geometry:
            model_geometry[key] = export_material_model(scene, root, frame, kind or f"{path.name}_Floor")
        floor = path.GetPrefixes()[2]
        placement = frame.GetLocalToWorldTransform(root) * group_transforms[floor].GetInverse()
        place_model(scene, path, floor, placement, model_geometry[key])

    for body, (visual, kind) in robot_visuals.items():
        group_transforms[body] = frame.GetLocalToWorldTransform(stage.GetPrimAtPath(body))
        scene.graph.update(frame_to=str(body), matrix=np.asarray(group_transforms[body]).T)
        if kind not in model_geometry:
            model_geometry[kind] = export_material_model(scene, visual, frame, kind)
        placement = frame.GetLocalToWorldTransform(visual) * group_transforms[body].GetInverse()
        place_model(scene, visual.GetPath(), body, placement, model_geometry[kind])

    tracked_nodes = {str(path) for path in group_transforms}

    def annotate_nodes(tree):
        for node in tree["nodes"]:
            if node.get("name") in tracked_nodes:
                node["extras"] = {"usdPath": node["name"]}

    output = ROOT / "web/hotel.glb"
    output.write_bytes(export_glb(scene, include_normals=True, tree_postprocessor=annotate_nodes))
    print(f"Exported {len(group_transforms)} USD groups and {len(furnishings)} furnishings "
          f"to {output.name} ({output.stat().st_size / 1e6:.1f} MB)")


if __name__ == "__main__":
    main()
