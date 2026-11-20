"""Download the pinned USD furnishings and their material dependencies."""

from concurrent.futures import ThreadPoolExecutor
from hashlib import file_digest
import json
from pathlib import Path
from shutil import copyfileobj
from tempfile import NamedTemporaryFile
from urllib.parse import quote
from urllib.request import Request, urlopen

HUB = "https://huggingface.co"
DESTINATION = Path(__file__).resolve().parents[1] / "assets/furniture"
NVIDIA_REPO = "nvidia/USDCraft-10K"
NVIDIA_REVISION = "9265e910275b28e1317893141e1a838da9e657c1"
FORGE_REVISION = "b94ea770666b6c0f82e09a78e9fcb375b223e3b1"
FORGE_REPO = "ForgeCAD/simready-assets"
BED_URL = ("https://www.innerscene.com/api/library/"
           "queen-bed-with-bedding-original-interior-furnishing-usdz-a6ffdeae/download")
PARQUET_URL = "https://dl.polyhaven.org/file/ph-assets/Textures/jpg/1k/herringbone_parquet"

NVIDIA_MODELS = {
    "nightstand": "living_bedroom_electronics_toys/R112_nightstand/05_nightstand_with_open_cubbies",
    "desk": "living_bedroom_electronics_toys/R121_desk_with_drawers/03_slim_two_drawer_writing_desk",
    "chair": "living_bedroom_electronics_toys/R162_accent_chair/01_molded_shell_accent_chair",
    "lamp": "living_bedroom_electronics_toys/R180_table_lamp/01_gourd_ceramic_bedside_lamp",
    "mirror": "living_bedroom_electronics_toys/R187_wall_mirror/05_oval_bathroom_mirror",
    "toilet": "bath_laundry_hardware/R228_toilet/05_one_piece_modern_toilet",
    "reception": "office_commercial_hospitality/R299_reception_desk/03_l_shaped_reception_counter",
    "sideboard": "kitchen_dining/R626_sideboard_buffet/09_rattan_front_sideboard_140_x_42_x_78_cm_in_ash_w",
    "sofa": "living_bedroom_electronics_toys/R159_sofa/03_mid_century_wooden_sofa",
    "coffee_table": "living_bedroom_electronics_toys/R163_coffee_table/02_round_marble_coffee_table",
    "dining_table": "kitchen_dining/R099_dining_table/07_farmhouse_dining_table_220_cm_long_100_cm_wide_a",
    "bistro_table": "kitchen_dining/R099_dining_table/10_bistro_dining_table_for_two_70_x_70_cm_and_74_cm",
    "bench": "kitchen_dining/R101_dining_bench/02_bench_with_backrest",
    "bookshelf": "living_bedroom_electronics_toys/R166_open_bookshelf/01_five_shelf_oak_bookshelf",
    "kitchen_cabinet": "kitchen_dining/R020_kitchen_base_cabinet/03_four_door_large_base_cabinet",
    "checkout": "office_commercial_hospitality/R333_checkout_counter/01_straight_checkout_counter",
    "fridge": "office_commercial_hospitality/R348_commercial_refrigerator/03_glass_door_display_fridge",
    "suite_sofa": "living_bedroom_electronics_toys/R159_sofa/10_english_roll_arm_sofa_200_x_92_x_85_cm_with_low",
    "suite_armchair": "living_bedroom_electronics_toys/R161_armchair/08_french_berg_re_armchair_68_x_70_x_92_cm_with_a_c",
    "wardrobe": "living_bedroom_electronics_toys/R110_wardrobe/03_sliding_door_wardrobe",
    "floor_lamp": "living_bedroom_electronics_toys/R184_floor_lamp/07_pharmacy_floor_lamp_in_antique_brass_115_to_145",
    "copper_tub": "bath_laundry_hardware/R236_bathtub/08_hammered_copper_freestanding_bathtub_165_x_75_x",
}
FORGE_MODELS = {"sink": "vanity_sink_faucet", "shower": "shower_cabin"}
PARQUET_MAPS = {
    "diff": (829046, "d6c66492a6c4cfd4e8ed18c9cdd4e0303f3de4e04613d2adca8bc2b8f2dd2350"),
    "nor_gl": (373438, "af6a822100fd6369bb75e43bb764f4a71ca41f521538ac796e92e681e3ad38f5"),
    "arm": (554910, "64ded181ebe84df02998929e95b5a9a4b613f268a5355f7ef56cd91da879b207"),
}


def valid(path, size, checksum):
    if not path.is_file() or path.stat().st_size != size:
        return False
    if checksum:
        with path.open("rb") as stream:
            return file_digest(stream, "sha256").hexdigest() == checksum
    return True


def download(url, relative, size, checksum=None, *, destination=DESTINATION):
    path = destination / relative
    if valid(path, size, checksum):
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = None
    try:
        request = Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with NamedTemporaryFile(dir=path.parent, delete=False) as output:
            temp = Path(output.name)
            with urlopen(request, timeout=60) as source:
                copyfileobj(source, output, length=1024 * 1024)
        if not valid(temp, size, checksum):
            raise RuntimeError(f"Download verification failed for {relative}")
        temp.chmod(0o644)
        temp.replace(path)
    finally:
        if temp is not None:
            temp.unlink(missing_ok=True)


def package_files(repo, revision, folder, kind):
    tree = f"{HUB}/api/datasets/{repo}/tree/{revision}/{folder}?recursive=true"
    with urlopen(tree, timeout=30) as response:
        entries = json.load(response)
    return [
        (f"{HUB}/datasets/{repo}/resolve/{revision}/{quote(entry['path'])}",
         f"{kind}/{entry['path'].removeprefix(folder + '/')}", entry["size"], entry.get("lfs", {}).get("oid"))
        for entry in entries if entry["type"] == "file"
        and not entry["path"].removeprefix(folder + "/").startswith(".")
    ]


def main():
    with ThreadPoolExecutor(max_workers=6) as pool:
        packages = [
            *(pool.submit(package_files, NVIDIA_REPO, NVIDIA_REVISION, f"rigid/{folder}/physx", kind)
              for kind, folder in NVIDIA_MODELS.items()),
            *(pool.submit(package_files, FORGE_REPO, FORGE_REVISION, f"{folder}/simready_usd", kind)
              for kind, folder in FORGE_MODELS.items()),
        ]
        downloads = [pool.submit(download, BED_URL, "bed/bed.usdz", 623819,
                                 "797d62bed9a2f0476f37cda2c4d216a16a0482f529a4381cebe8a3d7925914ef")]
        for channel, (size, checksum) in PARQUET_MAPS.items():
            name = f"herringbone_parquet_{channel}_1k.jpg"
            downloads.append(pool.submit(download, f"{PARQUET_URL}/{name}", f"parquet/{name}", size, checksum))
        for package in packages:
            downloads.extend(pool.submit(download, *entry) for entry in package.result())
        for task in downloads:
            task.result()
    print(f"USD furnishings available in {DESTINATION}")


if __name__ == "__main__":
    main()
