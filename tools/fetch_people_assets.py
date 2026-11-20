"""Fetch the Isaac Sim 6.2 character assets and walking animations."""

from concurrent.futures import ThreadPoolExecutor
from functools import partial
import json
from pathlib import Path
from urllib.parse import quote, urlencode
from urllib.request import urlopen
from xml.etree import ElementTree

from fetch_furniture_assets import download

ROOT = Path(__file__).resolve().parents[1]
DESTINATION = ROOT / "assets/people"
STORAGE = "https://omniverse-content-production.s3-us-west-2.amazonaws.com"
PEOPLE_PREFIX = "Assets/Isaac/6.2/Isaac/People/"
MOTION_GROUPS = ("Idle", "MoveWalk", "MoveWalkSlow")
LIBRARY_SOURCE = "MotionLibrary/HumanMotionLibrary.usd"
MOTION_LIBRARY = "MotionLibrary/HotelMotionLibrary.usd"


def entries(folder):
    query = urlencode({"list-type": "2", "prefix": PEOPLE_PREFIX + folder})
    with urlopen(f"{STORAGE}/?{query}", timeout=30) as source:
        listing = ElementTree.parse(source)
    for item in listing.findall("{*}Contents"):
        key = item.findtext("{*}Key")
        size = int(item.findtext("{*}Size"))
        if size and "/.thumbs/" not in key:
            yield f"{STORAGE}/{quote(key)}", key.removeprefix(PEOPLE_PREFIX), size


def main():
    models = json.loads((ROOT / "assets/people.json").read_text())["models"]
    folders = [str(Path(source).parent) + "/" for source in models.values()]
    folders.extend(f"MotionLibrary/BuiltinActions/{group}/" for group in MOTION_GROUPS)
    folders.append(LIBRARY_SOURCE)
    fetch = partial(download, destination=DESTINATION)
    with ThreadPoolExecutor(max_workers=8) as pool:
        packages = list(pool.map(lambda folder: list(entries(folder)), folders))
        for folder, package in zip(folders, packages):
            if not package:
                raise RuntimeError(f"No published Isaac Sim assets found for {folder}")
        jobs = [pool.submit(fetch, *entry) for package in packages for entry in package]
        for job in jobs:
            job.result()
    print(f"Character assets available in {DESTINATION}")


if __name__ == "__main__":
    main()
