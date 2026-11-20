"""Export the hotel in the same metric frame as the RMF traffic graphs."""

import json
from pathlib import Path

import yaml
from building_map.building import Building
from shapely.geometry import Polygon
from shapely.geometry.polygon import orient
from shapely.ops import triangulate, unary_union

from food_delivery import add_food_delivery_routes
from furnishings import hotel_furnishings
from suites import bathroom_floors, plan_walls

ASSETS = Path(__file__).resolve().parents[1] / "assets"
UPSTREAM = ASSETS / "upstream"
FLEETS = {
    "tinyRobot": (0, "#02887e", "Service"),
    "cleanerBotA": (1, "#6756d8", "Cleaning"),
    "deliveryRobot": (2, "#bc551b", "Delivery"),
}
MODELS = {"tinyRobot": "Service robot", "cleanerBotA": "NVIDIA Small Bot", "deliveryRobot": "G2 OmniPicker"}
FLOORS = {"L1": "Lobby & dining", "L2": "Guestrooms", "L3": "Suites"}
def polygons(shape):
    if shape.geom_type == "Polygon" and not shape.is_empty:
        yield shape
    elif hasattr(shape, "geoms"):
        for part in shape.geoms:
            yield from polygons(part)


def triangles(polygon):
    for triangle in triangulate(polygon):
        for part in polygons(triangle.intersection(polygon)):
            if part.area < 1e-8:
                continue
            if len(part.exterior.coords) == 4 and not part.interiors:
                yield list(orient(part).exterior.coords)[:-1]
            else:
                yield from triangles(part)


def export_level(level, graphs, rooms):
    vertices = [vertex.xy() for vertex in level.transformed_vertices]
    cuts = unary_union([
        *(Polygon(corners) for corners in level.lift_vert_lists.values()),
        *(Polygon([vertices[index] for index in hole.vertex_indices]) for hole in level.holes),
    ])
    floors = []
    bath_tile = bathroom_floors(level) if level.name == "L3" else None
    for floor in level.floors:
        shape = Polygon([vertices[i] for i in floor.vertex_indices]).buffer(0).difference(cuts)
        material = floor.params["texture_name"].value
        surfaces = [(shape, material)]
        if bath_tile is not None:
            if material == "brown_marble":
                surfaces = [(shape, "suite_corridor")]
            else:
                surfaces = [(shape.difference(bath_tile), "parquet"), (shape.intersection(bath_tile), "suite_bath_tile")]
        for region, finish in surfaces:
            for part in polygons(region):
                if part.area < 1e-6:
                    continue
                floors.append({
                    "rings": [list(orient(part).exterior.coords), *[list(ring.coords) for ring in part.interiors]],
                    "triangles": list(triangles(part)),
                    "material": finish,
                })

    doors = []
    for edge in level.doors:
        params = {key: arg.value for key, arg in edge.params.items()}
        doors.append({
            "name": params["name"], "type": params["type"],
            "a": vertices[edge.start_idx], "b": vertices[edge.end_idx],
            "swing": params.get("motion_degrees", 90) * params.get("motion_direction", 1),
        })

    lanes = set()
    for graph in graphs.values():
        nav = graph["levels"][level.name]
        for a, b, _ in nav["lanes"]:
            ends = (tuple(nav["vertices"][a][:2]), tuple(nav["vertices"][b][:2]))
            if ends[0] != ends[1]:
                lanes.add(tuple(sorted(ends)))

    return {
        "name": FLOORS[level.name], "z": level.elevation, "floors": floors,
        "walls": plan_walls(level, vertices),
        "doors": doors, "lanes": sorted(lanes), "props": [],
        "furnishings": hotel_furnishings(level), "rooms": rooms,
        "places": {v.name: v.xy() for v in level.transformed_vertices if v.name},
    }


def main():
    building = Building(yaml.safe_load((UPSTREAM / "hotel.building.yaml").read_text()))
    graphs = {int(index): graph for index, graph in building.generate_nav_graphs().items()}
    rooms = add_food_delivery_routes(building.levels, graphs[FLEETS["deliveryRobot"][0]])
    graph_dir = ASSETS / "nav_graphs"
    graph_dir.mkdir(exist_ok=True)
    for index, graph in graphs.items():
        (graph_dir / f"{index}.yaml").write_text(yaml.safe_dump(graph, sort_keys=False))

    robots = []
    for config, (index, color, role) in FLEETS.items():
        fleet = yaml.safe_load((UPSTREAM / "fleets" / f"{config}_config.yaml").read_text())["rmf_fleet"]
        for name, robot in fleet["robots"].items():
            level, vertex = next(
                (level, vertex)
                for level, graph in graphs[index]["levels"].items()
                for vertex in graph["vertices"] if vertex[2].get("name") == robot["charger"]
            )
            robots.append({
                "name": name, "fleet": fleet["name"], "role": role, "color": color,
                "model": MODELS[config], "reversible": fleet["reversible"],
                "level": level, "x": vertex[0], "y": vertex[1], "yaw": 0.0,
                "radius": fleet["profile"]["footprint"], "limits": fleet["limits"],
                "battery": fleet["battery_system"], "mechanical": fleet["mechanical_system"],
                "watts": fleet["ambient_system"]["power"],
            })
    layout = {
        "levels": {name: export_level(level, graphs, rooms.get(name, {})) for name, level in building.levels.items()},
        "lifts": [
            {"name": lift.name, "x": lift.x, "y": lift.y, "width": lift.width, "depth": lift.depth,
             "yaw": lift.yaw, "levels": lift.level_elevation, "level": lift.initial_floor_name,
             "door": {"x": lift.doors[0].x, "y": lift.doors[0].y, "width": lift.doors[0].width}}
            for lift in building.lifts.values()
        ],
        "robots": robots,
    }
    (ASSETS / "hotel.json").write_text(json.dumps(layout, separators=(",", ":")) + "\n")


if __name__ == "__main__":
    main()
