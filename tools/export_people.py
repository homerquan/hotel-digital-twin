"""Prepare the native motion library and export the skinned characters for Three.js."""

import json

import numpy as np
from pxr import Sdf, Usd, UsdGeom, UsdSkel
import trimesh
from trimesh.exchange.gltf import export_glb

from export_web_scene import model_meshes, surface_value, texture_image
from fetch_people_assets import DESTINATION, LIBRARY_SOURCE, MOTION_GROUPS, MOTION_LIBRARY, ROOT


def avatar_material(material):
    shader, _, _ = material.ComputeSurfaceSource("mdl")
    paths = [surface_value(shader, name, None)
             for name in ("diffuse_texture", "normalmap_texture", "ORM_texture")]
    diffuse, normal, orm = [texture_image(path.resolvedPath) if path and path.path else None for path in paths]
    color = surface_value(shader, "diffuse_tint" if diffuse else "diffuse_color_constant", (1., 1., 1.))
    appearance = trimesh.visual.material.PBRMaterial(
        name=material.GetPrim().GetName(), baseColorFactor=(*color, 1.0), baseColorTexture=diffuse, normalTexture=normal,
        occlusionTexture=orm, metallicRoughnessTexture=orm,
        metallicFactor=1 if orm else 0, roughnessFactor=1 if orm else 0.65, doubleSided=True,
    )
    return appearance, (shader, (1, 1))


def vertex_skin(skin, point_count, joints):
    indices, weights = skin.ComputeVaryingJointInfluences(point_count)
    indices = np.asarray(indices).reshape(point_count, -1)
    weights = np.asarray(weights).reshape(point_count, -1)
    order = skin.GetJointOrder()
    if order is not None:
        indices = np.array([joints.index(joint) for joint in order])[indices]
    # glTF's first weight set has four influences per vertex.
    chosen = np.argsort(-weights, axis=1)[:, :4]
    indices = np.take_along_axis(indices, chosen, axis=1).astype("<u2")
    weights = np.take_along_axis(weights, chosen, axis=1).astype("<f4")
    weights /= weights.sum(axis=1, keepdims=True)
    pad = [(0, 0), (0, 4 - chosen.shape[1])]
    return {"JOINTS_0": np.pad(indices, pad), "WEIGHTS_0": np.pad(weights, pad)}


def export_avatar(source):
    stage = Usd.Stage.Open(str(source))
    root = next(UsdSkel.Root(prim) for prim in stage.Traverse() if prim.IsA(UsdSkel.Root))
    cache = UsdSkel.Cache()
    cache.Populate(root, Usd.PrimDefaultPredicate)
    binding, = cache.ComputeSkelBindings(root, Usd.PrimDefaultPredicate)
    skeleton = binding.GetSkeleton()
    joints = list(skeleton.GetJointsAttr().Get())
    parents = UsdSkel.Topology(joints).GetParentIndices()
    rest = skeleton.GetRestTransformsAttr().Get()
    bind = skeleton.GetBindTransformsAttr().Get()
    scene = trimesh.Scene()
    appearances = {}

    def material_for(material):
        key = material.GetPath()
        if key not in appearances:
            appearances[key] = avatar_material(material)
        return key, appearances[key][1]

    for skin in binding.GetSkinningTargets():
        prim = skin.GetPrim()
        count = len(UsdGeom.Mesh(prim).GetPointsAttr().Get())
        attributes = vertex_skin(skin, count, joints)
        for key, mesh, uv in model_meshes(prim, material_for, uv_name="st0", point_attributes=attributes):
            mesh.apply_transform(np.asarray(skin.GetGeomBindTransform()).T)
            mesh.visual = trimesh.visual.texture.TextureVisuals(uv=uv, material=appearances[key][0])
            scene.add_geometry(mesh, geom_name=f"part_{len(scene.geometry)}")

    # At runtime the native agent supplies the skel root pose separately from
    # its joints. Keep the skeleton and all geometry in that root's space.
    relative = UsdGeom.XformCache()
    skeleton_space = relative.GetLocalToWorldTransform(skeleton.GetPrim())
    root_space = relative.GetLocalToWorldTransform(root.GetPrim())
    rig_matrix = np.asarray(skeleton_space * root_space.GetInverse()).reshape(-1).tolist()

    def add_skin(buffers, tree):
        view = len(buffers)
        buffers["inverse_bind_matrices"] = np.asarray([matrix.GetInverse() for matrix in bind], dtype="<f4").tobytes()
        accessor = len(tree["accessors"])
        tree["accessors"]["inverse_bind_matrices"] = {
            "bufferView": view, "componentType": 5126, "type": "MAT4", "count": len(joints),
        }

        nodes = tree["nodes"]
        rig = nodes[tree["scenes"][0]["nodes"][0]]
        rig.update(name="CharacterRig", matrix=rig_matrix)
        for node in nodes:
            if "mesh" in node:
                node["skin"] = 0
        for mesh in tree["meshes"]:
            for primitive in mesh["primitives"]:
                attributes = primitive["attributes"]
                for name in ("JOINTS_0", "WEIGHTS_0"):
                    attributes[name] = attributes.pop(f"_{name}")

        first_joint = len(nodes)
        for joint, matrix in zip(joints, rest):
            nodes.append({"name": joint.rsplit("/", 1)[-1], "matrix": np.asarray(matrix).reshape(-1).tolist()})
        for index, parent in enumerate(parents):
            node = rig if parent < 0 else nodes[first_joint + parent]
            node.setdefault("children", []).append(first_joint + index)
        tree["skins"] = [{"inverseBindMatrices": accessor, "joints": list(range(first_joint, len(nodes)))}]

    return export_glb(scene, include_normals=True, buffer_postprocessor=add_skin)


def prepare_motion_library():
    source = Sdf.Layer.FindOrOpen(str(DESTINATION / LIBRARY_SOURCE))
    library = Sdf.Layer.CreateAnonymous(".usdc")
    library.TransferContent(source)
    edits = Sdf.BatchNamespaceEdit()
    actions = library.GetPrimAtPath(f"/{source.defaultPrim}/BuiltinActions")
    for action in actions.nameChildren.values():
        if action.name not in MOTION_GROUPS:
            edits.Add(action.path, Sdf.Path.emptyPath)
    if not library.Apply(edits) or not library.Export(str(DESTINATION / MOTION_LIBRARY)):
        raise RuntimeError("Could not prepare the hotel motion library")


def main():
    models = json.loads((ROOT / "assets/people.json").read_text())["models"]
    target = ROOT / "web/people"
    target.mkdir(parents=True, exist_ok=True)
    for name, source in models.items():
        path = target / f"{name}.glb"
        path.write_bytes(export_avatar(DESTINATION / source))
        print(f"Exported {path}")
    prepare_motion_library()


if __name__ == "__main__":
    main()
