"""Add the lobby pickup and numbered guest-room stops to the delivery graph."""

from shapely.geometry import LineString, Point

# Room label and handoff coordinates on the L2 and L3 source floor plans.
# The handoff coordinates sit on the corridor side of each doorway.
ROOMS = (
    ((173, 117), (242, 97)), ((320, 117), (250, 97)),
    ((173, 205), (241, 182)), ((320, 205), (250, 184)),
    ((173, 289), (241, 265)), ((173, 375), (242, 354)),
    ((173, 457), (241, 438)), ((320, 458), (252, 434)),
    ((446, 458), (397, 518)), ((78, 578), (130, 535)),
    ((186, 611), (172, 542)), ((272, 612), (258, 542)),
    ((357, 612), (344, 542)), ((443, 613), (430, 542)),
    ((529, 612), (514, 542)),
)


def add_stops(nav, stops):
    vertices, lanes = nav["vertices"], nav["lanes"]
    # The hotel's room corridors use bidirectional lanes. Keep lift and
    # door transitions intact and attach each stop to the nearest corridor.
    directions = {(a, b) for a, b, properties in lanes if not properties.get("door_name")}
    corridors = sorted((a, b) for a, b in directions if a < b and (b, a) in directions
                       and not any(vertices[i][2].get("lift_cabin") for i in (a, b)))
    for name, position in stops:
        point = Point(position)
        a, b = min(corridors, key=lambda ends: LineString([vertices[i][:2] for i in ends]).distance(point))
        lane = LineString([vertices[a][:2], vertices[b][:2]])
        offset = lane.project(point)
        if offset < 0.10:
            junction = a
        elif lane.length - offset < 0.10:
            junction = b
        else:
            junction = len(vertices)
            projected = lane.interpolate(offset)
            vertices.append([projected.x, projected.y, {"name": ""}])
            corridors.remove((a, b))
            corridors.extend(((a, junction), (junction, b)))
            for index in reversed(range(len(lanes))):
                start, end, properties = lanes[index]
                if (start, end) in ((a, b), (b, a)):
                    lanes[index:index + 1] = [[start, junction, properties], [junction, end, properties]]

        stop = len(vertices)
        vertices.append([*position, {"name": name, "is_holding_point": True}])
        lanes.extend(([junction, stop, {}], [stop, junction, {}]))


def add_food_delivery_routes(levels, delivery_graph):
    lobby = next(vertex.xy() for vertex in levels["L1"].transformed_vertices if vertex.name == "lobby")
    add_stops(delivery_graph["levels"]["L1"], [("lobby", lobby)])

    rooms_by_level = {}
    for name in ("L2", "L3"):
        transform = levels[name].transform
        rooms = {}
        for number, ((x, y), (door_x, door_y)) in enumerate(ROOMS, 1):
            room = f"{name}-{number:02d}"
            rooms[room] = {
                "position": transform.transform_point([x, -y]),
                "waypoint": f"{name}_food_room_{number:02d}",
                "dropoff": transform.transform_point([door_x, -door_y]),
            }
        add_stops(delivery_graph["levels"][name], ((room["waypoint"], room["dropoff"]) for room in rooms.values()))
        rooms_by_level[name] = rooms
    return rooms_by_level
