"""Fetch the pinned SimReady USD layers, materials and textures for the hotel."""

from concurrent.futures import ThreadPoolExecutor
from hashlib import file_digest
import json
from pathlib import Path
from shutil import copyfileobj
from tempfile import NamedTemporaryFile
from urllib.parse import quote
from urllib.request import urlopen

HUB = "https://huggingface.co"
DESTINATION = Path(__file__).resolve().parents[1] / "assets/robots"
PACKAGES = (
    ("agibot-assets/simready-assets", "b0628c2800bb1d64559489d6b2b8643b0d502fcc", "g2_omnipicker"),
    ("nvidia/simready-assets", "653d91e182011f357cf1644013cc94ddf194d349", "robotics_small_bot_a01"),
)


def required(path):
    relative = Path(path)
    if relative.name in ("LICENSE.txt", "com.nvidia.simready.packaging.json"):
        return True
    if "simready_usd" not in relative.parts or ".thumbs" in relative.parts:
        return False
    # The NVIDIA package duplicates its textures in source_assets.
    if "source_assets" in relative.parts:
        return relative.suffix == ".mdl"
    return relative.name not in ("instances_mujoco.usda", "instances_newton.usda")


def download(remote, entry):
    path = DESTINATION / entry["path"]
    size = entry["size"]
    if path.is_file() and path.stat().st_size == size:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = None
    try:
        with NamedTemporaryFile(dir=path.parent, delete=False) as output:
            temp_path = Path(output.name)
            with urlopen(f"{remote}/{quote(entry['path'])}", timeout=60) as source:
                copyfileobj(source, output, length=1024 * 1024)
        if temp_path.stat().st_size != size:
            raise RuntimeError(f"Incomplete download for {path.name}")
        if entry.get("lfs"):
            with temp_path.open("rb") as content:
                if file_digest(content, "sha256").hexdigest() != entry["lfs"]["oid"]:
                    raise RuntimeError(f"Checksum mismatch for {path.name}")
        temp_path.chmod(0o644)
        temp_path.replace(path)
    finally:
        if temp_path and temp_path.exists():
            temp_path.unlink()


def main():
    with ThreadPoolExecutor(max_workers=6) as pool:
        for repo, revision, package in PACKAGES:
            tree = f"{HUB}/api/datasets/{repo}/tree/{revision}/{package}?recursive=true&expand=false"
            with urlopen(tree, timeout=30) as response:
                files = [entry for entry in json.load(response)
                         if entry["type"] == "file" and required(entry["path"])]
            if not files:
                raise RuntimeError(f"No asset files found for {package}")
            print(f"Preparing {package} ({sum(item['size'] for item in files) / 1e6:.0f} MB)", flush=True)
            remote = f"{HUB}/datasets/{repo}/resolve/{revision}"
            list(pool.map(lambda entry: download(remote, entry), files))
    print(f"SimReady assets available in {DESTINATION}")


if __name__ == "__main__":
    main()
