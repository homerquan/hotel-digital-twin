import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "assets"
SIM_ADDRESS = ("127.0.0.1", int(os.environ.get("HOTEL_SIM_UDP_PORT", "18760")))
PHYSICS_HZ = 60
