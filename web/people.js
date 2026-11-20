import { MathUtils, Quaternion, Vector3 } from "three";
import { clone } from "./vendor/utils/SkeletonUtils.js";

const translation = new Vector3();
const rotation = new Quaternion();
const poseStride = 7;

function showPose(joints, from, to, fraction) {
  for (const [index, joint] of joints.entries()) {
    const offset = index * poseStride;
    joint.position.fromArray(from, offset).lerp(translation.fromArray(to, offset), fraction);
    joint.quaternion.fromArray(from, offset + 3)
      .slerp(rotation.fromArray(to, offset + 3), fraction).normalize();
  }
}

export class PeopleView {
  constructor(scene, loader, draw) {
    this.scene = scene;
    this.loader = loader;
    this.draw = draw;
    this.models = new Map();
    this.characters = new Map();
  }

  async load(person) {
    const { model, name } = person.state;
    if (!this.models.has(model)) {
      const asset = this.loader.loadAsync(`/static/people/${model}.glb`).catch((error) => {
        console.warn(`Could not load the ${model} character`, error);
        return null;
      });
      this.models.set(model, asset);
    }
    const asset = await this.models.get(model);
    if (!asset) return;
    const node = clone(asset.scene);
    let skeleton;
    node.traverse((part) => {
      if (part.isSkinnedMesh) {
        skeleton ||= part.skeleton;
        part.frustumCulled = false;
      }
    });
    if (!skeleton) return;
    node.name = name;
    person.node = node;
    person.joints = [node, ...skeleton.bones];
    person.previous = new Float32Array(person.joints.length * poseStride);
    node.visible = false;
    this.scene.add(node);
    this.update(person, performance.now());
    this.draw();
  }

  sync(states, level, sample) {
    this.level = level;
    for (const person of this.characters.values()) person.present = false;
    const now = performance.now();
    for (const state of states) {
      let person = this.characters.get(state.name);
      if (!person) {
        person = { state };
        this.characters.set(state.name, person);
        void this.load(person);
      }
      Object.assign(person, { state, sample, present: true });
    }
    for (const person of this.characters.values()) this.update(person, now);
  }

  update(person, now) {
    if (!person.node) return;
    const { node, joints, previous, state, sample } = person;
    const wasVisible = node.visible;
    node.visible = person.present && state.level === this.level && state.pose.length === previous.length;
    if (!node.visible || (wasVisible && person.appliedSample === sample)) return;

    person.target = state.pose;
    if (wasVisible) {
      for (const [index, joint] of joints.entries()) {
        joint.position.toArray(previous, index * poseStride);
        joint.quaternion.toArray(previous, index * poseStride + 3);
      }
      person.duration = MathUtils.clamp(now - person.sampleAt, 60, 240);
    } else {
      showPose(joints, person.target, person.target, 1);
      person.duration = 0;
    }
    person.appliedSample = sample;
    person.sampleAt = now;
  }

  step(now) {
    let moving = false;
    for (const person of this.characters.values()) {
      if (!person.node?.visible || !person.duration) continue;
      const fraction = MathUtils.clamp((now - person.sampleAt) / person.duration, 0, 1);
      showPose(person.joints, person.previous, person.target, fraction);
      if (fraction === 1) person.duration = 0;
      else moving = true;
    }
    return moving;
  }
}
