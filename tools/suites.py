"""Interior changes and furnishings for the hotel's L3 suites."""

import math

from shapely.geometry import LineString, Point, Polygon
from shapely.ops import unary_union

# These wall indices are from the pinned L3 hotel plan. Each entry records
# the room pair and the center of its new interior opening in plan pixels.
SUITE_OPENINGS = {
    79: (174, 161.4),  # Rooms 01 and 03
    17: (310, 163.0),  # Rooms 02 and 04
    77: (174, 330.2),  # Rooms 05 and 06
    34: (374.75, 455),  # Rooms 08 and 09
    46: (239.9, 628),  # Rooms 11 and 12
    60: (411, 628),  # Rooms 13 and 14
}

# Original bathrooms in rooms 10–15. Unlike the original open bedrooms,
# the L3 north wing also has baths within its living rooms and room 07.
BATHROOMS = (
    (114.5, 154.0, 608.8), (199.9, 240.0, 602.65), (287.0, 325.0, 602.65),
    (373.5, 410.6, 595.3), (457.4, 496.4, 595.5), (542.8, 582.1, 597.0),
)
# Left, right, front and back boundaries and the side facing the lounge.
PRIVATE_BATHS = (
    (117, 162, 206, 245.45, "right"),
    (330.5, 374.4, 212, 250.45, "left"),
    (117.6, 162, 376, 414.9, "right"),
    (117.7, 162, 462, 502.2, "right"),
    (473, 517.1, 462, 502.2, "left"),
)

# Bedroom centers, headboard orientation, and bedside table coordinates.
BEDROOMS = (
    ((143, 139, 180), ((122, 144), (164, 144))),
    ((347, 140, 180), ((325, 145), (368, 145))),
    ((143, 308, 180), ((122, 313), (164, 313))),
    ((144, 437, 0), ((122.5, 428), (165, 428))),
    ((330, 479, 180), ((308, 486), (352, 486))),
    ((60, 638, 90), ((45, 616.5), (45, 660))),
    ((180, 644, 90), ((163, 620),)),
    ((349, 644, 90), ((336, 620),)),
    ((521, 644, 90), ((508, 620),)),
)

# Each lounge has a sofa, a coffee table, a chair, a cabinet and a lamp.
LOUNGES = (
    ((209, 216, -90), (190, 216), (195, 187, 0), (187, 237, 180), (218, 237)),
    ((279, 216, 90), (299, 216), (293, 188, 0), (303, 241, 180), (278, 240)),
    ((209, 385, -90), (190, 385), (195, 357, 0), (187, 407, 180), (218, 407)),
    ((454, 451, -90), (434, 451), (418, 432, 0), (462, 424, 0), (452, 480)),
    ((42.2, 570, 90), (56.5, 570), (96, 570, -90), (101, 596.8, -90), (43, 593)),
    ((280, 657, 180), (280, 638), (303, 627, -90), (318, 650, -90), (258, 656)),
    ((451, 658, 180), (451, 639), (474, 628, -90), (489, 651, -90), (429, 657)),
)


def plan_walls(level, vertices):
    def transform(x, y):
        return level.transform.transform_point([x, -y])

    walls = []
    for index, edge in enumerate(level.walls):
        a, b = vertices[edge.start_idx], vertices[edge.end_idx]
        height = edge.params["texture_height"].value
        if level.name != "L3" or index not in SUITE_OPENINGS:
            walls.append([*a, *b, height])
            continue
        line = LineString([a, b])
        center = line.project(Point(transform(*SUITE_OPENINGS[index])))
        start = line.interpolate(center - 0.73)
        end = line.interpolate(center + 0.73)
        walls.extend(([*a, start.x, start.y, height], [end.x, end.y, *b, height]))

    if level.name == "L3":
        # Separate the original master suite's bedroom from its living room.
        partitions = [((34.1, 608.8), (66, 608.8)), ((96, 608.8), (114.52, 608.78))]
        for left, right, front, back, opening_side in PRIVATE_BATHS:
            side = right if opening_side == "right" else left
            partitions.extend((
                ((left, front), (right, front)),
                ((side, front), (side, front + 9)),
                ((side, back - 9), (side, back)),
            ))
        walls.extend([*transform(*a), *transform(*b), 2.5] for a, b in partitions)
    return walls


def bathroom_floors(level):
    existing = [(left, right, 557.5, back) for left, right, back in BATHROOMS]
    added = [bath[:4] for bath in PRIVATE_BATHS]
    return unary_union([
        Polygon([level.transform.transform_point([x, -y]) for x, y in
                 ((left, front), (right, front), (right, back), (left, back))])
        for left, right, front, back in existing + added
    ])


def furnish_bath(add, left, right, back, front=557.5, vanity="sink", tub=False):
    x = right - 10.2
    add(vanity, x, front + 8, 0.0, (0.62, 0.48, 1.0))
    add("mirror", x, front + 2.7, 0.0, (0.49, 0.025, 0.65), height=1.13)
    add("toilet", left + 8.5, back - 8.7, math.pi, (0.37, 0.65, 0.73))
    if tub:
        add("copper_tub", x, back - 20, math.pi / 2, (1.54, 0.72, 0.61))
    else:
        add("shower", x, back - 10.4, math.pi, (0.88, 0.82, 2.05))


def furnish_suites(add):
    for (x, y, degrees), nightstands in BEDROOMS:
        yaw = math.radians(degrees)
        add("bed", x, y, yaw, (1.84, 2.15, 1.24))
        for sx, sy in nightstands:
            add("nightstand", sx, sy, yaw, (0.38, 0.34, 0.56))
            add("lamp", sx, sy, yaw, (0.22, 0.22, 0.39), height=0.56)

    for x, y, degrees in (
        (185, 83, 0), (303, 84, 0), (182, 254, 0), (206, 488, 180), (300, 423, 0),
        (138, 656, 180), (219, 661, 180), (390, 662, 180), (562, 663, 180),
    ):
        add("wardrobe", x, y, math.radians(degrees), (1.21, 0.45, 1.94))

    for sofa, table, chair, cabinet, lamp in LOUNGES:
        x, y, degrees = sofa
        add("suite_sofa", x, y, math.radians(degrees), (1.72, 0.78, 0.84))
        add("coffee_table", *table, 0.0, (0.72, 0.72, 0.46))
        x, y, degrees = chair
        add("suite_armchair", x, y, math.radians(degrees), (0.70, 0.72, 0.90))
        x, y, degrees = cabinet
        add("sideboard", x, y, math.radians(degrees), (1.15, 0.40, 0.78))
        add("floor_lamp", *lamp, 0.0, (0.36, 0.36, 1.40))

    for index, (left, right, back) in enumerate(BATHROOMS):
        furnish_bath(add, left, right, back, vanity="suite_vanity", tub=index == 0)
    for left, right, front, back, _ in PRIVATE_BATHS:
        furnish_bath(add, left, right, back, front, vanity="suite_vanity")
