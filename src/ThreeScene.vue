<template>
  <div class="scene-shell">
    <div class="scene-toolbar">
      <span class="scene-label">{{ sceneLabel }}</span>
      <div class="toolbar-actions">
        <button @click="resetView" :disabled="!modelScene">重置视角</button>
        <button @click="highlightNextComponent" :disabled="allMeshes.length === 0">
          高亮下一个组件
        </button>
      </div>
    </div>

    <div class="scene-loading" v-if="isLoading">模型加载中</div>
    <div class="scene-error" v-if="loadError">{{ loadError }}</div>

    <TresCanvas shadows alpha>
      <TresPerspectiveCamera :position="cameraPosition" :look-at="cameraTarget" />
      <OrbitControls ref="controlsRef" />

      <TresAmbientLight :intensity="1.05" />
      <TresDirectionalLight :position="[12, 18, 11]" :intensity="1.45" cast-shadow />
      <TresDirectionalLight :position="[-8, 14, 7]" :intensity="0.82" />
      <TresDirectionalLight :position="[4, 9, -10]" :intensity="0.45" />

      <primitive :object="modelScene" v-if="modelScene" />
    </TresCanvas>
  </div>
</template>

<script setup>
import { ref, shallowRef, watch } from 'vue';
import { TresCanvas } from '@tresjs/core';
import { OrbitControls } from '@tresjs/cientos';
import * as THREE from 'three';
import { GLTFLoader } from 'three/examples/jsm/loaders/GLTFLoader';

const props = defineProps({
  modelUrl: {
    type: String,
    default: '/models/scene.glb'
  },
  sceneLabel: {
    type: String,
    default: '默认示例场景'
  },
  sceneCamera: {
    type: Object,
    default: null
  },
  roomLightStates: {
    type: Object,
    default: () => ({})
  },
  selectedRoomId: {
    type: String,
    default: ''
  },
  roomIds: {
    type: Array,
    default: () => []
  }
});

const controlsRef = ref(null);
const modelScene = shallowRef(null);
const cameraPosition = ref([8, 14, 9]);
const cameraTarget = ref([0, 0, 0]);
const isLoading = ref(false);
const loadError = ref('');
const allMeshes = ref([]);
const modelCenter = ref(new THREE.Vector3(0, 0, 0));
const modelMaxDim = ref(6);
const roomBounds = ref(new Map());
let highlightIndex = -1;
let activeLoadToken = 0;

function resetHighlight() {
  allMeshes.value.forEach((mesh) => {
    if (mesh.userData.originalMaterial) {
      mesh.material = mesh.userData.originalMaterial;
      mesh.userData.originalMaterial = null;
    }
  });
  highlightIndex = -1;
}

function processModel(scene) {
  const meshes = [];
  scene.traverse((child) => {
    if (!child.isMesh) {
      return;
    }
    child.castShadow = true;
    child.receiveShadow = true;
    if (child.material) {
      child.material = child.material.clone();
      child.material.side = THREE.DoubleSide;
      child.userData.baseColor = child.material.color?.clone() || new THREE.Color(0xffffff);
      child.userData.baseEmissive = child.material.emissive?.clone() || new THREE.Color(0x000000);
      child.userData.baseEmissiveIntensity = child.material.emissiveIntensity || 0;
    }
    child.userData.roomId = parseRoomIdFromNodeName(child.name || '');
    meshes.push(child);
  });
  allMeshes.value = meshes;

  const box = new THREE.Box3().setFromObject(scene);
  const center = box.getCenter(new THREE.Vector3());
  const size = box.getSize(new THREE.Vector3());
  modelCenter.value = center.clone();
  scene.position.set(-center.x, -center.y, -center.z);
  roomBounds.value = collectRoomBounds(meshes);

  const maxDim = Math.max(size.x, size.y, size.z, 6);
  modelMaxDim.value = maxDim;
  syncCameraState();
  applyRoomVisualState();

  if (controlsRef.value?.instance) {
    controlsRef.value.instance.enableDamping = true;
    controlsRef.value.instance.minDistance = maxDim * 0.45;
    controlsRef.value.instance.maxDistance = maxDim * 3.2;
    controlsRef.value.instance.maxPolarAngle = Math.PI / 2.2;
    applyCameraState();
  }

  if (props.selectedRoomId) {
    focusSelectedRoom();
  }
}

function syncCameraState() {
  const fallbackPosition = [modelMaxDim.value * 0.58, modelMaxDim.value * 1.55, modelMaxDim.value * 0.68];
  const fallbackTarget = [0, 0, 0];
  if (props.sceneCamera?.position && props.sceneCamera?.target) {
    cameraPosition.value = [
      props.sceneCamera.position.x - modelCenter.value.x,
      props.sceneCamera.position.y - modelCenter.value.y,
      props.sceneCamera.position.z - modelCenter.value.z
    ];
    cameraTarget.value = [
      props.sceneCamera.target.x - modelCenter.value.x,
      props.sceneCamera.target.y - modelCenter.value.y,
      props.sceneCamera.target.z - modelCenter.value.z
    ];
    return;
  }
  cameraPosition.value = fallbackPosition;
  cameraTarget.value = fallbackTarget;
}

function applyCameraState() {
  if (!controlsRef.value?.instance) {
    return;
  }
  controlsRef.value.instance.target.set(
    cameraTarget.value[0],
    cameraTarget.value[1],
    cameraTarget.value[2]
  );
  controlsRef.value.instance.object.position.set(
    cameraPosition.value[0],
    cameraPosition.value[1],
    cameraPosition.value[2]
  );
  controlsRef.value.instance.update();
}

function resetView() {
  applyCameraState();
}

function loadModel(modelUrl) {
  activeLoadToken += 1;
  const loadToken = activeLoadToken;
  isLoading.value = true;
  loadError.value = '';
  resetHighlight();
  modelScene.value = null;

  const loader = new GLTFLoader();
  loader.load(
    modelUrl,
    (gltf) => {
      if (loadToken !== activeLoadToken) {
        return;
      }
      const scene = gltf.scene || gltf.scenes?.[0];
      if (!scene) {
        loadError.value = '模型已返回，但内容为空。';
        isLoading.value = false;
        return;
      }
      processModel(scene);
      modelScene.value = scene;
      isLoading.value = false;
    },
    undefined,
    (error) => {
      if (loadToken !== activeLoadToken) {
        return;
      }
      console.error('模型加载失败', error);
      loadError.value = '模型加载失败，请检查后端服务是否已经启动。';
      isLoading.value = false;
    }
  );
}

function highlightNextComponent() {
  if (allMeshes.value.length === 0) {
    return;
  }

  if (highlightIndex >= 0) {
    const previous = allMeshes.value[highlightIndex];
    if (previous.userData.originalMaterial) {
      previous.material = previous.userData.originalMaterial;
    }
  }

  highlightIndex = (highlightIndex + 1) % allMeshes.value.length;
  const current = allMeshes.value[highlightIndex];
  current.userData.originalMaterial = current.material;
  current.material = current.material.clone();
  current.material.color.set(0x5f9f7f);
}

function parseRoomIdFromNodeName(name) {
  if (!name) {
    return '';
  }
  if (name.startsWith('floor_')) {
    return name.slice('floor_'.length);
  }
  if (name.startsWith('ceiling_')) {
    return name.slice('ceiling_'.length);
  }
  if (name.startsWith('furniture_')) {
    const suffix = name.slice('furniture_'.length);
    const orderedRoomIds = [...props.roomIds].sort((left, right) => right.length - left.length);
    for (const roomId of orderedRoomIds) {
      if (suffix.startsWith(`${roomId}_`)) {
        return roomId;
      }
    }
  }
  return '';
}

function collectRoomBounds(meshes) {
  const nextBounds = new Map();
  meshes.forEach((mesh) => {
    const roomId = mesh.userData.roomId;
    if (!roomId) {
      return;
    }
    const meshBox = new THREE.Box3().setFromObject(mesh);
    const existing = nextBounds.get(roomId);
    if (!existing) {
      nextBounds.set(roomId, meshBox.clone());
      return;
    }
    existing.union(meshBox);
  });
  return nextBounds;
}

function applyRoomVisualState() {
  allMeshes.value.forEach((mesh) => {
    if (!mesh.material || !mesh.userData.baseColor) {
      return;
    }
    if (mesh.userData.originalMaterial) {
      return;
    }

    mesh.material.color.copy(mesh.userData.baseColor);
    if (mesh.material.emissive && mesh.userData.baseEmissive) {
      mesh.material.emissive.copy(mesh.userData.baseEmissive);
      mesh.material.emissiveIntensity = mesh.userData.baseEmissiveIntensity || 0;
    }

    const roomId = mesh.userData.roomId;
    if (!roomId) {
      return;
    }

    const isOn = Boolean(props.roomLightStates?.[roomId]);
    const isSelected = props.selectedRoomId && props.selectedRoomId === roomId;
    if (isOn) {
      mesh.material.color.lerp(new THREE.Color(0xffffff), 0.1);
      if (mesh.material.emissive) {
        mesh.material.emissive.set(0x2a281f);
        mesh.material.emissiveIntensity = isSelected ? 0.34 : 0.2;
      }
      return;
    }

    mesh.material.color.multiplyScalar(isSelected ? 0.92 : 0.8);
    if (isSelected) {
      mesh.material.color.lerp(new THREE.Color(0x5f9f7f), 0.18);
    }
  });
}

function focusSelectedRoom() {
  const roomId = props.selectedRoomId;
  if (!roomId) {
    return;
  }
  const box = roomBounds.value.get(roomId);
  if (!box) {
    return;
  }
  const center = box.getCenter(new THREE.Vector3());
  const size = box.getSize(new THREE.Vector3());
  const horizontal = Math.max(size.x, size.z, 2.6);
  cameraTarget.value = [center.x, Math.max(center.y, 0), center.z];
  cameraPosition.value = [
    center.x + horizontal * 0.92,
    Math.max(horizontal * 1.15, 2.8),
    center.z + horizontal * 0.92
  ];
  applyCameraState();
}

watch(
  () => props.modelUrl,
  (nextUrl) => {
    if (nextUrl) {
      loadModel(nextUrl);
    }
  },
  { immediate: true }
);

watch(
  () => props.sceneCamera,
  () => {
    if (modelScene.value) {
      syncCameraState();
      resetView();
    }
  }
);

watch(
  () => props.roomLightStates,
  () => {
    applyRoomVisualState();
  },
  { deep: true }
);

watch(
  () => props.selectedRoomId,
  () => {
    applyRoomVisualState();
    focusSelectedRoom();
  }
);
</script>

<style scoped>
.scene-shell {
  position: relative;
  width: 100%;
  height: 100%;
}

.scene-toolbar {
  position: absolute;
  top: 86px;
  left: 18px;
  right: 18px;
  z-index: 12;
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.toolbar-actions {
  display: flex;
  gap: 10px;
}

.scene-label {
  padding: 10px 14px;
  border-radius: 999px;
  background: rgba(255, 252, 245, 0.8);
  color: #24322d;
  box-shadow: 0 10px 22px rgba(36, 50, 45, 0.08);
}

button {
  padding: 10px 14px;
  border: none;
  border-radius: 12px;
  background: rgba(49, 71, 68, 0.92);
  color: #f5f1e8;
  cursor: pointer;
}

button:disabled {
  opacity: 0.52;
  cursor: not-allowed;
}

.scene-loading,
.scene-error {
  position: absolute;
  left: 18px;
  bottom: 18px;
  z-index: 12;
  padding: 10px 14px;
  border-radius: 12px;
  background: rgba(255, 252, 245, 0.86);
  color: #314744;
}

.scene-error {
  color: #8b4131;
}
</style>
