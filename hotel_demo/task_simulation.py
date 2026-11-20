"""Dispatch a paced mix of hotel requests through the existing fleet scheduler."""

import asyncio
import os
import random

from aiohttp import web

WORKLOAD = (
    ("food-delivery", ("deliveryRobot",), 5),
    ("clean-lobby", ("cleanerBotA",), 3),
    ("lobby-patrol", ("tinyRobot",), 3),
    ("suite-delivery", ("deliveryRobot",), 1),
    ("suite-patrol", ("tinyRobot", "deliveryRobot"), 1),
)


class TaskSimulation:
    def __init__(self, bridge):
        self.bridge = bridge
        self.enabled = False
        self.next_time = None
        self.waiting = False
        self.autoplay = os.environ.get("HOTEL_AUTOPLAY") == "1"

    def set_enabled(self, enabled):
        self.autoplay = False
        if enabled != self.enabled:
            self.enabled = enabled
            self.next_time = self.bridge.sim_time() + random.uniform(5, 12) if enabled else None
            self.waiting = False
        return self.snapshot()

    def snapshot(self):
        return {"enabled": self.enabled, "next_time": self.next_time, "waiting": self.waiting}

    async def run(self):
        while True:
            await asyncio.sleep(.4)
            if not self.bridge.ready():
                continue
            if self.autoplay:
                self.set_enabled(True)
            now = self.bridge.sim_time()
            if not self.enabled or now < self.next_time:
                continue
            free = self.bridge.available_fleets()
            choices = [entry for entry in WORKLOAD
                       if free.intersection(entry[1]) and (entry[0] != "food-delivery" or self.bridge.rooms)]
            if not choices:
                self.waiting = True
                self.next_time = now + 20
                continue
            self.waiting = False
            self.next_time = now + random.uniform(90, 210)
            preset = random.choices(choices, weights=[entry[2] for entry in choices])[0][0]
            room = random.choice(tuple(self.bridge.rooms)) if preset == "food-delivery" else None
            try:
                await self.bridge.submit(preset, room, source="automatic")
            except web.HTTPException as error:
                self.bridge.get_logger().warning(f"Automatic {preset} request failed: {error.text}")
