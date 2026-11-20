# Open-RMF hotel in Isaac Sim

This demo runs the three-floor Open-RMF hotel in Isaac Sim 6.2.0. It uses the hotel navigation graphs, four robot spawns, and three fleet configurations from `rmf_demos` 2.3.0, with added food handoff stops on the delivery fleet's graph. Open-RMF handles dispatch, traffic coordination, door requests, and lift sessions over ROS 2 Jazzy.

The dashboard shows live robot poses, battery levels, tasks, and lift state on the hotel floor plan. Batteries draw power for onboard systems and wheel motion, and recharge when robots park at their assigned chargers. Use **New task** to dispatch patrols, cleaning, and deliveries. **Food delivery** sends the delivery robot to the lobby to pick up an order, then to the selected guest-room door on L2 or L3. Use **Building → Operate** to open or close a door or call a lift. A lift call goes through the RMF supervisor and releases the lift five simulation seconds after its doors open at the destination.

Select **Live 3D** to see the hotel scene while the simulation runs. Each browser renders meshes exported from the OpenUSD stage and receives the live world transforms of robots, doors, and lifts from Isaac Sim. L1 includes the lobby, restaurant, kitchen, and shop. L2 has standard guest rooms. L3 has seven suites with separate bedrooms and lounges, as well as two deluxe guest rooms. All numbered hallway delivery stops are available on both guest floors. Drag to orbit and right-drag to pan.

The browser export preserves the furnishings and parquet material, the OmniPicker's authored colors and finishes, and the Small Bots' tiled texture maps.

## Run on Spark

Run these commands from the project directory on an NVIDIA Linux host with Python 3.11 or newer, Docker Compose, and GPU container support. [Isaac Sim host requirements](https://docs.isaacsim.omniverse.nvidia.com/6.2.0/installation/requirements.html)

```bash
python3 tools/fetch_robot_assets.py
python3 tools/fetch_furniture_assets.py
ACCEPT_EULA=Y docker compose up --build -d
```

The asset download is about 415 MB and reuses files on subsequent runs. The NVIDIA image and the ROS image are cached by Docker. The browser server starts before Isaac Sim finishes loading. Wait for **Simulation live** before dispatching a task.

From your workstation, forward Spark's dashboard port and open [Hotel Fleet](http://localhost:8787/).

```bash
ssh -NL 8787:127.0.0.1:8787 spark
```

Run `docker compose logs -f isaac rmf` to see startup and task execution. Run `docker compose down` in the project directory on Spark to stop the demo. Use `HOTEL_PORT` to change the published loopback port. `HOTEL_SIM_RATE` defaults to `1`. Set it to `4` on startup for a faster walkthrough.

## Walk through the hotel

1. From **New task**, dispatch **Food delivery** to a room such as L3-14. Dispatch **Lobby cleaning** and **Lobby patrol** to use the other two fleets.
2. Watch `deliveryBot_1` pick up in the lobby, take a lift, and reach the selected room. Select a robot in the sidebar to open its floor and see its current navigation segment.
3. Click a lift or door on the map, or use **Building → Operate**. The dashboard shows the live response to each request. RMF retains control of resources reserved by robot tasks.

The delivery tasks use simulated pickup and drop-off station acknowledgements. The G2 OmniPicker and NVIDIA Small Bots follow RMF paths as kinematic bodies. The OmniPicker model is scaled to fit the hotel fleet's footprint and keeps its arms in a fixed transport pose. The furnishings are static. The Isaac stage contains collision geometry and animated door leaves, lift cars, and landing doors.

## Edit the scene

Open `assets/hotel.usda` in Isaac Sim after fetching the assets. `tools/export_layout.py` uses `rmf_building_map_tools` to generate the graphs and metric layout from the vendored hotel. `tools/export_usd.py` builds the stage and its furniture reference layers using the `usd-core` Python package.
`tools/export_web_scene.py` refreshes the browser scene from the stage and also uses `numpy`, `trimesh`, and Pillow.

```bash
docker run --rm --user "$(id -u):$(id -g)" -v "$PWD:/workspace" \
  building-twin-hotel-rmf:jazzy python3 tools/export_layout.py
python3 tools/export_usd.py
python3 tools/export_web_scene.py
```

Isaac Sim steps the USD scene and sends its clock and device state to the ROS bridge over the containers' shared loopback interface. The bridge translates RMF path and equipment commands back to the simulator. Restart both services after stopping Isaac Sim so the RMF schedule and simulation clock start together.

## Sources and asset credits

- The hotel map and fleet configurations come from Open Robotics' Apache-2.0 release. [Open-RMF hotel source](https://github.com/open-rmf/rmf_demos/tree/2.3.0)
- `deliveryBot_1` uses AgiBot's G2 OmniPicker under Apache-2.0. The stage adjusts its scale and composes the model with the fleet body's transforms. [G2 OmniPicker USD](https://huggingface.co/datasets/agibot-assets/simready-assets/blob/b0628c2800bb1d64559489d6b2b8643b0d502fcc/g2_omnipicker/simready_usd/g2_omnipicker.usda)
- `cleanerBotA_1` and `cleanerBotA_2` use NVIDIA's Small Bot under CC BY 4.0. The stage composes the models with the fleet bodies' transforms. [NVIDIA Small Bot USD](https://huggingface.co/datasets/nvidia/simready-assets/blob/653d91e182011f357cf1644013cc94ddf194d349/robotics_small_bot_a01/simready_usd/sm_robotics_small_bot_a01_01.usd) · [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/)
- The guest beds and bedding use Innerscene's CC0 queen bed. The same model is resized for twin beds. [Innerscene queen bed](https://www.innerscene.com/tools/library/3d-parts/queen-bed-with-bedding-original-interior-furnishing-usdz-a6ffdeae)
- The reception counter, cabinetry, dining sets, seating, shelves, and other furnishings come from NVIDIA USDCraft-10K under Apache-2.0. The vanities and showers come from ForgeCAD under CC BY 4.0. The hotel fits the models to the floor plan and uses them in static poses. [NVIDIA USDCraft-10K](https://huggingface.co/datasets/nvidia/USDCraft-10K) · [ForgeCAD SimReady assets](https://huggingface.co/datasets/ForgeCAD/simready-assets)
- The L3 parquet surface uses Poly Haven's CC0 Herringbone Parquet by Jenelle van Heerden and Sergej Majboroda. [Herringbone Parquet](https://polyhaven.com/a/herringbone_parquet)
- The browser viewer bundles Three.js r180 and its MIT license. [Three.js](https://github.com/mrdoob/three.js/tree/r180)
