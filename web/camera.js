import * as THREE from "three";
import { OrbitControls } from "./vendor/controls/OrbitControls.js";
import { GLTFLoader } from "./vendor/loaders/GLTFLoader.js";
import { RoomEnvironment } from "./vendor/environments/RoomEnvironment.js";
import { PeopleView } from "./people.js";

export class CameraView {
  constructor(container) {
    this.level = "L1";
    this.active = false;
    this.container = container;
    this.nodes = new Map();
    this.scene = new THREE.Scene();
    this.loader = new GLTFLoader();
    this.people = new PeopleView(this.scene, this.loader, () => this.draw());
    this.scene.background = new THREE.Color(0xe9eaf6);
    this.camera = new THREE.PerspectiveCamera(40, 1, .08, 360);
    this.camera.up.set(0, 0, 1);
    this.camera.position.set(25, -35, 44);

    this.renderer = new THREE.WebGLRenderer({ antialias: true });
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio, 1.75));
    this.renderer.toneMapping = THREE.ACESFilmicToneMapping;
    const room = new RoomEnvironment();
    const reflections = new THREE.PMREMGenerator(this.renderer);
    this.scene.environment = reflections.fromScene(room, 0.05).texture;
    this.scene.environmentIntensity = 0.5;
    room.dispose();
    reflections.dispose();
    container.append(this.renderer.domElement);
    this.controls = new OrbitControls(this.camera, this.renderer.domElement);
    this.controls.minDistance = 1.5;
    this.controls.maxDistance = 180;
    this.controls.maxPolarAngle = Math.PI / 2 - .05;
    this.controls.addEventListener("change", () => this.draw());

    this.scene.add(new THREE.HemisphereLight(0xf8f9ff, 0xb6b7ca, 1.5));
    const key = new THREE.DirectionalLight(0xfff5e6, 2.0);
    key.position.set(-25, -33, 70);
    this.scene.add(key);
    new ResizeObserver(() => this.resize()).observe(container);
  }

  async load() {
    const { scene } = await this.loader.loadAsync("/static/hotel.glb");
    this.scene.add(scene);
    scene.traverse((node) => {
      if (node.isMesh && node.geometry.hasAttribute("color")) {
        node.material.metalness = 0;
        node.material.roughness = 0.85;
        node.material.side = THREE.DoubleSide;
      }
      if (!node.userData.usdPath) return;
      node.updateMatrix();
      node.matrixAutoUpdate = false;
      this.nodes.set(node.userData.usdPath, node);
    });
  }

  setActive(active) {
    this.active = active;
    if (active) {
      this.resize();
      this.draw();
    }
  }

  resize() {
    const { clientWidth, clientHeight } = this.container;
    if (!clientWidth || !clientHeight) return;
    this.renderer.setSize(clientWidth, clientHeight, false);
    this.camera.aspect = clientWidth / clientHeight;
    this.camera.updateProjectionMatrix();
    this.draw();
  }

  setFloor(level, elevation) {
    if (this.level !== level || this.elevation !== elevation) {
      this.level = level;
      this.elevation = elevation;
      this.fit();
    }
  }

  fit() {
    const floor = this.nodes.get(`/World/Hotel/${this.level}`);
    if (!floor) return;
    const box = new THREE.Box3().setFromObject(floor);
    const center = box.getCenter(new THREE.Vector3());
    const direction = new THREE.Vector3(.60, -.84, 1.45).normalize();
    const halfFov = THREE.MathUtils.degToRad(this.camera.fov / 2);
    const distance = box.getSize(new THREE.Vector3()).length() * .5 /
      Math.sin(Math.min(halfFov, Math.atan(this.camera.aspect * Math.tan(halfFov))));
    this.controls.target.copy(center);
    this.camera.position.copy(center).addScaledVector(direction, distance);
    this.controls.update();
    this.draw();
  }

  orbit(delta) {
    const offset = this.camera.position.clone().sub(this.controls.target);
    offset.applyAxisAngle(this.camera.up, delta);
    this.camera.position.copy(this.controls.target).add(offset);
    this.controls.update();
    this.draw();
  }

  zoom(factor) {
    const offset = this.camera.position.clone().sub(this.controls.target);
    offset.clampLength(this.controls.minDistance / factor, this.controls.maxDistance / factor);
    this.camera.position.copy(this.controls.target).addScaledVector(offset, factor);
    this.controls.update();
    this.draw();
  }

  focusRobot(name) {
    const robot = this.nodes.get(`/World/Robots/${name}`);
    if (!robot) return;
    const center = new THREE.Box3().setFromObject(robot).getCenter(new THREE.Vector3());
    const offset = this.camera.position.clone().sub(this.controls.target).setLength(6);
    this.controls.target.copy(center);
    this.camera.position.copy(center).add(offset);
    this.controls.update();
    this.draw();
  }

  sync(actors, people, time) {
    for (const [path, node] of this.nodes) {
      const [, , group, name] = path.split("/");
      if (actors[path]) {
        node.matrix.fromArray(actors[path]);
        node.matrixWorldNeedsUpdate = true;
      }
      switch (group) {
        case "Hotel": node.visible = name === this.level; break;
        case "Doors": node.visible = name.startsWith(`${this.level}_`); break;
        case "Landings": node.visible = name.endsWith(`_${this.level}`); break;
        default: node.visible = Math.abs(node.matrix.elements[14] - this.elevation) < 2.2;
      }
    }
    this.people.sync(people, this.level, time);
    this.draw();
  }

  draw() {
    if (!this.active || this.frameRequest) return;
    this.frameRequest = requestAnimationFrame((now) => {
      this.frameRequest = null;
      if (!this.active) return;
      const moving = this.people.step(now);
      this.renderer.render(this.scene, this.camera);
      if (moving) this.draw();
    });
  }
}
