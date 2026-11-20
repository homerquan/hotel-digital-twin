"""Place USD furnishings at room and fixture coordinates on the hotel plan."""

import math

from suites import BATHROOMS, furnish_bath, furnish_suites

# Coordinates are in the original hotel plan. L2 and L3 share the same walls.
BEDSIDE_TABLES = (
    (155, 139, 180), (335, 139, 180), (153, 223, 180), (336, 226, 180),
    (154, 307, 180), (152, 392, 180), (151, 479, 180), (332, 478, 180),
    (463, 483, 180), (47, 611, 90), (165, 620, 90), (252, 621, 90),
    (336, 620, 90), (421, 623, 90), (508, 623, 90),
)
WRITING_DESKS = (
    (168, 151), (313, 154), (167, 236), (314, 242), (167, 322),
    (166, 408), (166, 495), (310, 495), (445, 495),
)
def hotel_furnishings(level):
    items = []

    def add(kind, x, y, yaw, size, height=0.0):
        items.append({
            "kind": kind,
            "position": level.transform.transform_point([x, -y]),
            "yaw": yaw + level.transform.rotation,
            "size": size,
            "height": height,
        })

    if level.name == "L1":
        ground_floor(level.models, add)
    elif level.name == "L3":
        furnish_suites(add)
    else:
        guest_floors(level.models, add)
    return items


def ground_floor(models, add):
    tables = [m for m in models if m.model_name.split("/")[-1] == "FoodCourtTable1"]
    for model in models:
        name = model.model_name.split("/")[-1]
        x, y = model.original_x, model.original_y
        yaw = model.yaw
        if name == "NurseDesk":
            add("reception", x, y, 0.0, (2.60, 0.97, 1.08))
            add("sideboard", x, 477, 0.0, (1.40, 0.42, 0.78))
        elif name == "DeskChair":
            add("chair", x, 486.5, math.pi, (0.48, 0.49, 0.78))
        elif name == "Sofa":
            yaw = math.pi / 2 if x < 160 else math.pi
            add("sofa", x + 2, y, yaw, (1.82, 0.75, 0.80))
        elif name == "Cafe table":
            add("coffee_table", x, y, 0.0, (0.73, 0.73, 0.45))
        elif name == "FoodCourtTable1":
            add("dining_table", x, y, yaw, (0.96, 0.66, 0.76))
        elif name == "FoodCourtBenchShort":
            table = min(tables, key=lambda item: math.hypot(item.original_x - x, item.original_y - y))
            distance = y - table.original_y
            yaw = 0.0 if distance < 0.0 else math.pi
            y = table.original_y + math.copysign(max(abs(distance), 12.2), distance)
            add("bench", x, y, yaw, (0.77, 0.31, 0.76))
        elif name == "CoffeeTable":
            add("bistro_table", x, y, 0.0, (0.68, 0.68, 0.74))
            add("chair", x - 12.5, y, math.pi / 2, (0.46, 0.46, 0.76))
            if x < 240:
                add("chair", x + 12.5, y, -math.pi / 2, (0.46, 0.46, 0.76))
        elif name == "KitchenCountertop":
            west = x < 350
            add("kitchen_cabinet", 327 if west else 376, y - 2,
                math.pi / 2 if west else -math.pi / 2, (1.43, 0.55, 0.90))
        elif name == "MainTable":
            if y < 400:
                add("sideboard", x, 295, math.pi, (1.42, 0.42, 0.82))
            else:
                add("checkout", x, y, yaw, (1.40, 0.58, 1.00))
        elif name in ("Bookshelf", "StorageRack"):
            add("bookshelf", x, y, yaw, (0.86, 0.33, 1.52))
        elif name == "Fridge":
            add("fridge", x, y, yaw, (0.72, 0.66, 1.64))
        elif name == "Toilet":
            add("toilet", x, y, math.pi, (0.37, 0.65, 0.73))


def guest_floors(models, add):
    for model in models:
        if model.model_name.split("/")[-1] != "NormalBed":
            continue
        x, y = model.original_x, model.original_y
        width = 0.86 if x < 70 or (x > 460 and y < 510) else 1.48
        if x < 70:
            x += 3.0
        elif y > 600:
            x += 1.0
        if model.yaw > 1:
            y -= 3.0
        add("bed", x, y, model.yaw + math.pi / 2, (width, 2.10, 1.16))

    for x, y, degrees in BEDSIDE_TABLES:
        yaw = math.radians(degrees)
        add("nightstand", x, y, yaw, (0.38, 0.34, 0.56))
        add("lamp", x, y, yaw, (0.22, 0.22, 0.39), height=0.56)

    for x, y in WRITING_DESKS:
        add("desk", x, y, math.pi, (0.98, 0.46, 0.74))
        add("chair", x, y - 11.5, 0.0, (0.48, 0.49, 0.78))
    add("desk", 147, 623, -math.pi / 2, (0.98, 0.46, 0.74))
    add("chair", 135.5, 623, math.pi / 2, (0.48, 0.49, 0.78))

    for left, right, back in BATHROOMS:
        furnish_bath(add, left, right, back)
