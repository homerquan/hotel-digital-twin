import { HotelMap } from "./map.js";

const $ = (id) => document.getElementById(id);
const terminal = new Set(["completed", "finished", "failed", "canceled", "killed", "skipped"]);
const labels = { idle: "Idle", moving: "Moving", charging: "Charging", waiting: "Waiting", offline: "Offline",
  underway: "In progress", queued: "Queued", completed: "Completed", finished: "Ended", failed: "Failed" };
let layout, map, presets, selected, latest, camera, loadingCamera;
let show3D = false;
const robotRows = new Map();

function element(tag, className, text) {
  const item = document.createElement(tag);
  if (className) item.className = className;
  if (text != null) item.textContent = text;
  return item;
}

function pill(status) {
  return element("span", `pill ${status}`, labels[status] || status.replaceAll("_", " ").replace(/^./, (c) => c.toUpperCase()));
}

function liftLabel(name) {
  return name.replace(/(\D)(\d)/, "$1 $2");
}

function liftStatus(lift) {
  if (!lift) return "—";
  if (lift.motion !== "stopped") return `Moving to ${lift.target}`;
  return `${lift.level} · ${lift.door === 1 ? "Open" : lift.door ? "Moving doors" : "Closed"}`;
}

function timeText(seconds) {
  const secondsFloor = Math.max(0, Math.floor(seconds));
  return [Math.floor(secondsFloor / 3600), Math.floor(secondsFloor / 60) % 60, secondsFloor % 60]
    .map((value) => String(value).padStart(2, "0")).join(":");
}

async function getJson(url, options = {}) {
  const response = await fetch(url, { cache: "no-store", signal: AbortSignal.timeout(12000), ...options });
  if (!response.ok) throw new Error((await response.text()).trim() || `Request failed (${response.status})`);
  return response.json();
}

function selectFloor(level) {
  map.setFloor(level);
  $("floor-name").textContent = layout.levels[level].name;
  for (const button of $("floors").children) button.setAttribute("aria-pressed", button.textContent === level);
  camera?.setFloor(level, layout.levels[level].z);
  syncCamera();
}

function cameraStatus(message, live = false) {
  const status = $("camera-status");
  if (status.textContent !== message) status.textContent = message;
  status.classList.toggle("has-frame", live);
}

function syncCamera() {
  if (!show3D || !camera || !latest) return;
  camera.sync(latest.actors || {});
  const live = latest.live && Object.keys(latest.actors || {}).length > 0;
  cameraStatus(live ? "Live" : "Waiting for simulation", live);
}

async function toggleCamera() {
  show3D = !show3D;
  $("camera-pane").hidden = !show3D;
  for (const id of ["floor-map", "map-orientation", "map-scale", "map-controls", "map-layers"]) $(id).hidden = show3D;
  $("view-toggle").textContent = show3D ? "Floor plan" : "Live 3D";
  $("view-toggle").setAttribute("aria-pressed", show3D);
  $("plan-heading").textContent = show3D ? "Hotel 3D view" : "Hotel floor plan";
  if (!show3D) {
    camera?.setActive(false);
    map.draw();
    return;
  }
  if (!camera) {
    cameraStatus("Loading 3D view");
    try {
      loadingCamera ||= import("./camera.js").then(async ({ CameraView }) => {
        const view = new CameraView($("scene-surface"));
        await view.load();
        return view;
      });
      camera = await loadingCamera;
    } catch {
      loadingCamera = null;
      $("scene-surface").replaceChildren();
      cameraStatus("3D view unavailable");
      return;
    }
  }
  camera.setActive(show3D);
  camera.setFloor(map.level, layout.levels[map.level].z);
  syncCamera();
}

function selectRobot(name) {
  selected = selected === name ? null : name;
  map.selected = selected;
  const robot = latest?.robots.find((robot) => robot.name === selected);
  if (robot && robot.level !== map.level) selectFloor(robot.level);
  if (latest) render(latest);
  if (show3D && robot) camera?.focusRobot(robot.name);
}

function initialize(geometry, options) {
  layout = geometry;
  presets = options;
  map = new HotelMap($("floor-map"), layout, selectRobot, showBuilding);
  for (const [level, floor] of Object.entries(layout.levels)) {
    const button = element("button", "", level);
    button.title = floor.name;
    button.addEventListener("click", () => selectFloor(level));
    $("floors").append(button);
  }
  selectFloor("L1");
  for (const [role, color] of new Map(layout.robots.map(({ role, color }) => [role, color]))) {
    const entry = element("span", "", role);
    const dot = element("i", "dot");
    dot.style.background = color;
    entry.prepend(dot);
    $("legend").append(entry);
  }
  for (const robot of layout.robots) {
    const li = element("li");
    const button = element("button", "robot-row");
    button.style.setProperty("--fleet-color", robot.color);
    button.addEventListener("click", () => selectRobot(robot.name));
    const name = element("span");
    const meta = element("span", "robot-meta");
    const health = element("span", "robot-health");
    name.append(element("span", "robot-label", robot.name), meta);
    button.append(element("span", "robot-icon", "◈"), name, health);
    li.append(button);
    $("robots").append(li);
    robotRows.set(robot.name, { button, meta, health });
  }
  $("fleet-count").textContent = layout.robots.length;
  for (const task of options) {
    const option = element("option", "", task.name);
    option.value = task.id;
    $("preset").append(option);
  }
  const rooms = options.find((task) => task.rooms)?.rooms || [];
  for (const [level, floor] of Object.entries(layout.levels)) {
    const group = element("optgroup");
    group.label = `${level} · ${floor.name}`;
    for (const room of rooms.filter((number) => number.startsWith(`${level}-`))) {
      group.append(new Option(room, room));
    }
    if (group.childElementCount) $("room-number").append(group);
  }
  const lifts = element("optgroup");
  lifts.label = "Lifts";
  for (const lift of layout.lifts) lifts.append(new Option(liftLabel(lift.name), lift.name));
  $("equipment").append(lifts);
  for (const [level, floor] of Object.entries(layout.levels)) {
    $("lift-floor").append(new Option(`${level} · ${floor.name}`, level));
    const doors = element("optgroup");
    doors.label = `${level} doors`;
    for (const door of floor.doors) doors.append(new Option(`${level} · Door ${door.name.split("door")[1]}`, door.name));
    $("equipment").append(doors);
  }
}

function render(state) {
  latest = state;
  const online = state.robots.filter((robot) => robot.online).length;
  $("online-count").replaceChildren(String(online), element("small", "", ` / ${state.robots.length}`));
  $("task-count").textContent = state.tasks.filter((task) => !terminal.has(task.status)).length;
  $("sim-time").textContent = state.sim_time ? timeText(state.sim_time) : "—";
  $("connection").classList.toggle("is-live", state.ready);
  $("connection").classList.toggle("is-offline", !state.live);
  const connection = state.ready ? "Simulation live" : state.restarted ? "Restart required" :
    state.live ? "Connecting to robots" : "Waiting for simulation";
  if ($("connection-text").textContent !== connection) $("connection-text").textContent = connection;
  $("new-task").disabled = !state.ready;
  $("building-controls").disabled = !state.live;
  $("view-toggle").disabled = !layout;
  map.state = state;
  map.draw();
  syncCamera();

  for (const robot of state.robots) {
    const { button, meta, health } = robotRows.get(robot.name);
    button.setAttribute("aria-pressed", robot.name === selected);
    const spec = layout.robots.find(({name}) => name === robot.name);
    meta.textContent = `${spec.model || robot.role} · ${robot.lift || robot.level}`;
    health.replaceChildren(pill(robot.online ? robot.mode : "offline"),
      element("strong", "", robot.battery == null ? "—" : `${robot.battery.toFixed(1)}%`));
  }
  $("lifts").replaceChildren(...layout.lifts.map((lift) => {
    const status = state.lifts.find((item) => item.name === lift.name);
    const item = element("li");
    item.append(element("strong", "", liftLabel(lift.name)), element("span", "", state.live ? liftStatus(status) : "—"));
    return item;
  }));

  $("no-tasks").hidden = state.tasks.length > 0;
  $("task-table").hidden = !state.tasks.length;
  $("tasks").replaceChildren(...state.tasks.map((task) => {
    const row = element("tr");
    row.title = task.id;
    const name = element("td"), status = element("td");
    name.append(element("strong", "", task.name));
    if (task.route) name.append(element("span", "", task.route));
    status.append(pill(task.status));
    row.append(name, status, element("td", "", task.robot || "—"), element("td", "", timeText(task.time)));
    return row;
  }));
  if ($("building-dialog").open) showEquipmentState();
}

const taskDialog = $("task-dialog");
const submitTask = $("submit-task");
function showPresetDetail() {
  const task = presets.find((task) => task.id === $("preset").value);
  $("preset-detail").textContent = task?.description || "";
  $("room-options").hidden = $("room-number").disabled = !task?.rooms?.length;
}
$("preset").addEventListener("change", showPresetDetail);
$("new-task").addEventListener("click", () => {
  $("task-error").hidden = true;
  showPresetDetail();
  taskDialog.showModal();
});
for (const id of ["close-task", "cancel-task"]) $(id).addEventListener("click", () => taskDialog.close());
$("task-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  submitTask.disabled = true;
  submitTask.textContent = "Dispatching…";
  $("task-error").hidden = true;
  try {
    const task = { preset: $("preset").value };
    if (!$("room-number").disabled) task.room = $("room-number").value;
    await getJson("/api/tasks", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(task),
    });
    taskDialog.close();
  } catch (error) {
    $("task-error").textContent = error.message;
    $("task-error").hidden = false;
  } finally {
    submitTask.disabled = false;
    submitTask.textContent = "Dispatch task";
  }
});

const buildingDialog = $("building-dialog");
let operating = false;
function showEquipmentState() {
  const name = $("equipment").value;
  const hasLift = layout.lifts.some((lift) => lift.name === name);
  const lift = latest?.lifts.find((lift) => lift.name === name);
  const fraction = latest?.doors[name];
  $("lift-options").hidden = $("call-lift").hidden = !hasLift;
  $("open-door").hidden = $("close-door").hidden = hasLift;
  $("call-lift").disabled = operating || !latest?.live || Boolean(lift?.session);
  $("open-door").disabled = $("close-door").disabled = operating || !latest?.live;
  let status = fraction === 1 ? "Open" : fraction === 0 ? "Closed" : "Moving";
  if (hasLift) status = liftStatus(lift) + (lift?.session ? " · Reserved" : "");
  $("equipment-state").textContent = latest?.live ? status : "Simulation offline";
}

function showBuilding(equipment) {
  $("equipment").value = equipment || layout.levels[map.level].doors[0]?.name || layout.lifts[0].name;
  $("lift-floor").value = map.level;
  $("equipment-feedback").hidden = true;
  showEquipmentState();
  buildingDialog.showModal();
}
$("building-controls").addEventListener("click", () => showBuilding());
$("close-building").addEventListener("click", () => buildingDialog.close());
$("equipment").addEventListener("change", () => {
  $("equipment-feedback").hidden = true;
  showEquipmentState();
});
$("building-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  if (operating) return;
  operating = true;
  showEquipmentState();
  const feedback = $("equipment-feedback");
  feedback.hidden = true;
  feedback.classList.remove("form-error");
  try {
    await getJson("/api/infrastructure", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({
      name: $("equipment").value, action: event.submitter?.value, level: $("lift-floor").value,
    }) });
    feedback.textContent = "Request sent.";
  } catch (error) {
    feedback.textContent = error.message;
    feedback.classList.add("form-error");
  } finally {
    feedback.hidden = false;
    operating = false;
    showEquipmentState();
  }
});

$("zoom-in").addEventListener("click", () => map?.zoomBy(1.24));
$("zoom-out").addEventListener("click", () => map?.zoomBy(1 / 1.24));
$("fit-map").addEventListener("click", () => map?.fit());
$("view-toggle").addEventListener("click", toggleCamera);
$("orbit-left").addEventListener("click", () => camera?.orbit(Math.PI / 6));
$("orbit-right").addEventListener("click", () => camera?.orbit(-Math.PI / 6));
$("camera-in").addEventListener("click", () => camera?.zoom(.78));
$("camera-out").addEventListener("click", () => camera?.zoom(1.28));
$("camera-fit").addEventListener("click", () => camera?.fit());
$("lane-toggle").addEventListener("change", (event) => {
  if (!map) return;
  map.showLanes = event.currentTarget.checked;
  map.draw();
});

async function poll() {
  try {
    if (!map) initialize(...await Promise.all([getJson("/api/layout"), getJson("/api/presets")]));
    render(await getJson("/api/state", { signal: AbortSignal.timeout(3000) }));
  } catch (error) {
    if (latest) render({ ...latest, live: false, ready: false, robots: latest.robots.map((robot) => ({ ...robot, online: false })) });
    $("connection").classList.add("is-offline");
    $("connection-text").textContent = "Dashboard disconnected";
    $("new-task").disabled = true;
  } finally {
    setTimeout(poll, show3D ? 120 : 300);
  }
}
poll();
