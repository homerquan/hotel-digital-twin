const surface = {
  concrete2: "#e8e9f3",
  red_tiles: "#eee6ec",
  white_beige_tiles: "#faf9fd",
  parquet: "#f5ede3",
  suite_corridor: "#efebe3",
  suite_bath_tile: "#edf1f2",
};
const roomNames = {
  restaurant: "Restaurant", lobby: "Lobby", shop: "Shop", kitchen: "Kitchen",
  clean_waiting_area: "Waiting area",
};

export class HotelMap {
  constructor(canvas, layout, onSelect, onEquipment) {
    this.canvas = canvas;
    this.context = canvas.getContext("2d");
    this.layout = layout;
    this.level = "L1";
    this.showLanes = true;
    this.zoom = 1;
    this.offset = [0, 0];
    this.state = { robots: [], lifts: [], doors: {} };
    this.bounds = Object.fromEntries(Object.entries(layout.levels).map(([level, floor]) => {
      const coords = floor.floors.flatMap(({rings}) => rings.flat());
      const xs = coords.map(([x]) => x), ys = coords.map(([, y]) => y);
      return [level, [Math.min(...xs), Math.max(...xs), Math.min(...ys), Math.max(...ys)]];
    }));

    canvas.addEventListener("pointerdown", (event) => {
      if (event.button !== 0) return;
      this.drag = { x: event.clientX, y: event.clientY, origin: [...this.offset], moved: false };
      canvas.setPointerCapture(event.pointerId);
    });
    canvas.addEventListener("pointermove", (event) => {
      if (!this.drag) return;
      const dx = event.clientX - this.drag.x;
      const dy = event.clientY - this.drag.y;
      this.drag.moved ||= Math.hypot(dx, dy) > 4;
      if (this.drag.moved) {
        this.offset = [this.drag.origin[0] + dx, this.drag.origin[1] + dy];
        canvas.classList.add("dragging");
        this.draw();
      }
    });
    canvas.addEventListener("pointerup", (event) => {
      if (this.drag && !this.drag.moved) {
        const { left, top } = canvas.getBoundingClientRect();
        const pointer = [event.clientX - left, event.clientY - top];
        const near = ([x, y], radius) => Math.hypot(x - pointer[0], y - pointer[1]) < radius;
        const hit = this.state.robots.find((robot) => {
          const [x, y] = this.project(robot.x, robot.y);
          return robot.level === this.level && !robot.lift && near([x, y], 13);
        });
        if (hit) {
          onSelect(hit.name);
        } else if (this.state.live) {
          const lift = layout.lifts.find((item) => near(this.project(item.x, item.y), 13));
          const door = layout.levels[this.level].doors.find(({ a, b }) =>
            near(this.project((a[0] + b[0]) / 2, (a[1] + b[1]) / 2), 9));
          if (lift || door) onEquipment((lift || door).name);
        }
      }
      this.endDrag();
    });
    canvas.addEventListener("pointercancel", () => this.endDrag());
    new ResizeObserver(() => this.draw()).observe(canvas);
  }

  endDrag() {
    this.drag = null;
    this.canvas.classList.remove("dragging");
  }

  setFloor(level) {
    this.level = level;
    this.fit();
  }

  fit() {
    this.zoom = 1;
    this.offset = [0, 0];
    this.draw();
  }

  zoomBy(amount) {
    this.zoom = Math.max(.65, Math.min(4, this.zoom * amount));
    this.draw();
  }

  project(x, y) {
    return [this.center[0] + (x - this.mid[0]) * this.scale,
      this.center[1] - (y - this.mid[1]) * this.scale];
  }

  trace(points, closed = false) {
    const ctx = this.context;
    ctx.moveTo(...this.project(...points[0]));
    for (const point of points.slice(1)) ctx.lineTo(...this.project(...point));
    if (closed) ctx.closePath();
  }

  draw() {
    const { canvas, context: ctx } = this;
    const width = canvas.clientWidth, height = canvas.clientHeight;
    if (!width || !height) return;
    const ratio = Math.min(2, window.devicePixelRatio || 1);
    if (canvas.width !== width * ratio || canvas.height !== height * ratio) {
      canvas.width = Math.round(width * ratio);
      canvas.height = Math.round(height * ratio);
    }
    ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
    ctx.clearRect(0, 0, width, height);
    const floor = this.layout.levels[this.level];
    const [left, right, bottom, top] = this.bounds[this.level];
    this.mid = [(left + right) / 2, (bottom + top) / 2];
    this.center = [width / 2 + this.offset[0], height / 2 + this.offset[1]];
    this.scale = Math.min((width - Math.min(88, width / 5)) / (right - left), (height - 64) / (top - bottom)) * this.zoom;
    document.querySelector("#map-scale span").style.width = `${5 * this.scale}px`;

    ctx.fillStyle = "#c9cde2";
    for (let x = 11; x < width; x += 18) {
      for (let y = 11; y < height; y += 18) ctx.fillRect(x, y, 1, 1);
    }
    ctx.lineCap = "round";
    ctx.lineJoin = "round";
    ctx.save();
    ctx.shadowColor = "#28345926";
    ctx.shadowOffsetY = 5;
    ctx.shadowBlur = 15;
    for (const slab of floor.floors) {
      ctx.beginPath();
      for (const ring of slab.rings) this.trace(ring, true);
      ctx.fillStyle = surface[slab.material] || "#f8f8fc";
      ctx.fill("evenodd");
    }
    ctx.restore();

    const furnishings = (floor.furnishings ?? []).filter((item) => item.height === 0);
    for (const prop of [...floor.props, ...furnishings]) {
      const [x, y] = this.project(...prop.position);
      const [w, h] = prop.size.map((length) => length * this.scale);
      ctx.save();
      ctx.translate(x, y);
      ctx.rotate(-prop.yaw);
      ctx.fillStyle = prop.kind === "bed" ? "#c8c6e5" : "#d8dae7";
      ctx.fillRect(-w / 2, -h / 2, w, h);
      ctx.restore();
    }

    if (this.showLanes) {
      ctx.beginPath();
      for (const lane of floor.lanes) this.trace(lane);
      ctx.strokeStyle = "#8d83c4";
      ctx.lineWidth = 1.15;
      ctx.setLineDash([3, 4]);
      ctx.stroke();
      ctx.setLineDash([]);
    }

    ctx.beginPath();
    for (const [ax, ay, bx, by] of floor.walls) this.trace([[ax, ay], [bx, by]]);
    ctx.strokeStyle = "#686e8d";
    ctx.lineWidth = Math.max(1.3, Math.min(4, .2 * this.scale));
    ctx.stroke();
    for (const door of floor.doors) {
      const fraction = this.state.doors[door.name] || 0;
      ctx.beginPath();
      this.trace([door.a, door.b]);
      ctx.lineWidth = fraction ? 2.8 : 2.1;
      ctx.strokeStyle = fraction > .98 ? "#13887a" : fraction ? "#b2782f" : "#8e93b1";
      if (fraction) ctx.setLineDash([2, 3]);
      ctx.stroke();
      ctx.setLineDash([]);
    }

    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    ctx.font = "600 10px system-ui, sans-serif";
    for (const lift of this.layout.lifts) {
      const [x, y] = this.project(lift.x, lift.y);
      const size = Math.max(14, Math.min(28, lift.width * this.scale));
      const stoppedHere = this.state.lifts.some((item) => item.name === lift.name && item.level === this.level && item.motion === "stopped");
      ctx.fillStyle = stoppedHere ? "#cbd1eb" : "#e3e5f2";
      ctx.fillRect(x - size / 2, y - size / 2, size, size);
      ctx.strokeStyle = "#858bab";
      ctx.lineWidth = 1;
      ctx.strokeRect(x - size / 2, y - size / 2, size, size);
      ctx.fillStyle = "#444e73";
      ctx.fillText(lift.name.slice(-1), x, y);
    }
    if (width > 400) {
      ctx.fillStyle = "#555d78";
      for (const [key, name] of Object.entries(roomNames)) {
        if (!(key in floor.places)) continue;
        const [x, y] = this.project(...floor.places[key]);
        ctx.fillText(name, x, y - 8);
      }
      for (const [number, room] of Object.entries(floor.rooms ?? {})) {
        ctx.fillText(number, ...this.project(...room.position));
      }
    }

    const onFloor = this.state.robots.filter((robot) => robot.level === this.level && !robot.lift);
    const selected = onFloor.find((robot) => robot.name === this.selected);
    if (selected?.path?.length) {
      ctx.beginPath();
      this.trace([[selected.x, selected.y], ...selected.path.filter(([level]) => level === this.level).map(([, x, y]) => [x, y])]);
      ctx.strokeStyle = selected.color;
      ctx.setLineDash([5, 4]);
      ctx.lineWidth = 2.2;
      ctx.stroke();
      ctx.setLineDash([]);
    }
    for (const robot of onFloor) this.drawRobot(robot);
  }

  drawRobot(robot) {
    const ctx = this.context;
    const [x, y] = this.project(robot.x, robot.y);
    ctx.save();
    ctx.translate(x, y);
    const selected = robot.name === this.selected;
    if (selected) {
      ctx.beginPath();
      ctx.arc(0, 0, 13, 0, 2 * Math.PI);
      ctx.fillStyle = `${robot.color}33`;
      ctx.fill();
    }
    ctx.beginPath();
    ctx.arc(0, 0, 8, 0, 2 * Math.PI);
    ctx.fillStyle = robot.online ? robot.color : "#757c94";
    ctx.fill();
    ctx.lineWidth = 1.6;
    ctx.strokeStyle = "white";
    ctx.stroke();
    ctx.rotate(-(robot.travel_yaw ?? robot.yaw));
    ctx.beginPath();
    ctx.moveTo(4, 0);
    ctx.lineTo(-2.5, -3);
    ctx.lineTo(-2.5, 3);
    ctx.closePath();
    ctx.fillStyle = "#fff";
    ctx.fill();
    ctx.restore();
    if (selected) {
      ctx.font = "700 11px system-ui, sans-serif";
      const textWidth = ctx.measureText(robot.name).width + 14;
      const label = Math.max(textWidth / 2 + 3, Math.min(this.canvas.clientWidth - textWidth / 2 - 3, x));
      ctx.fillStyle = "#2d334a";
      ctx.beginPath();
      ctx.roundRect(label - textWidth / 2, y - 32, textWidth, 18, 5);
      ctx.fill();
      ctx.fillStyle = "white";
      ctx.fillText(robot.name, label, y - 23);
    }
  }
}
