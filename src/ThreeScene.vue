<template>
  <div class="scene-shell">
    <div class="scene-toolbar">
      <span class="scene-label">{{ sceneLabel }}</span>
      <button @click="highlightNextComponent" :disabled="allMeshes.length === 0">
        高亮下一个组件
      </button>
    </div>

    <div class="scene-loading" v-if="isLoading">模型加载中</div>
    <div class="scene-error" v-if="loadError">{{ loadError }}</div>

    <TresCanvas shadows alpha>
      <TresPerspectiveCamera :position="cameraPosition" :look-at="[0, 0, 0]" />
      <OrbitControls ref="controlsRef" />

      <TresAmbientLight :intensity="1.35" />
      <TresDirectionalLight :position="[14, 20, 10]" :intensity="1.75" cast-shadow />
      <TresDirectionalLight :position="[-10, 12, -8]" :intensity="0.65" />

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
  }
});

const controlsRef = ref(null);
const modelScene = shallowRef(null);
const cameraPosition = ref([8, 14, 9]);
const isLoading = ref(false);
const loadError = ref('');
const allMeshes = ref([]);
let highlightIndex = -1;

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
      child.material.side = THREE.DoubleSide;
    }
    meshes.push(child);
  });
  allMeshes.value = meshes;

  const box = new THREE.Box3().setFromObject(scene);
  const center = box.getCenter(new THREE.Vector3());
  const size = box.getSize(new THREE.Vector3());
  scene.position.set(-center.x, -center.y, -center.z);

  const maxDim = Math.max(size.x, size.y, size.z, 6);
  cameraPosition.value = [maxDim * 0.58, maxDim * 1.55, maxDim * 0.68];

  if (controlsRef.value?.instance) {
    controlsRef.value.instance.enableDamping = true;
    controlsRef.value.instance.minDistance = maxDim * 0.45;
    controlsRef.value.instance.maxDistance = maxDim * 3.2;
    controlsRef.value.instance.maxPolarAngle = Math.PI / 2.2;
    controlsRef.value.instance.target.set(0, 0, 0);
    controlsRef.value.instance.object.position.set(
      cameraPosition.value[0],
      cameraPosition.value[1],
      cameraPosition.value[2]
    );
    controlsRef.value.instance.update();
  }
}

function loadModel(modelUrl) {
  isLoading.value = true;
  loadError.value = '';
  resetHighlight();
  modelScene.value = null;

  const loader = new GLTFLoader();
  loader.load(
    modelUrl,
    (gltf) => {
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

watch(
  () => props.modelUrl,
  (nextUrl) => {
    if (nextUrl) {
      loadModel(nextUrl);
    }
  },
  { immediate: true }
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
