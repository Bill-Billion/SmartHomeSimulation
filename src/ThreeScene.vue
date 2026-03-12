<template>
  <button @click="highlightNextComponent" class="highlight-button">高亮下一个组件</button>

  <!-- TresCanvas 增加 window-size 确保铺满，或保持容器铺满 -->
  <TresCanvas shadows alpha>
    <TresPerspectiveCamera ref="cameraRef" :position="[15, 15, 15]" :look-at="[0, 0, 0]" />
    <OrbitControls ref="controlsRef" />

    <!-- 环境光和基础平行光，强度稍微调高一点点 -->
    <TresAmbientLight :intensity="1.5" />
    <TresDirectionalLight
      :position="[10, 20, 10]"
      :intensity="2.0"
      cast-shadow
    />

    <!-- 核心：吊灯光源 -->
    <TresPointLight
      v-if="lightPosition"
      :position="lightPosition"
      :intensity="isLightOn ? 3000 : 0"
      :distance="lightDistance"
      :decay="2"
      color="#ffffcc"
      cast-shadow
    />

    <!-- 直接渲染模型 -->
    <primitive :object="modelScene" v-if="modelScene" />
  </TresCanvas>
</template>

<script setup>
import { ref, watch, onMounted, shallowRef, nextTick } from 'vue';
import { TresCanvas } from '@tresjs/core';
import { OrbitControls } from '@tresjs/cientos';
import * as THREE from 'three';
import { GLTFLoader } from 'three/examples/jsm/loaders/GLTFLoader';

const props = defineProps({
  isLightOn: Boolean,
  lightDistance: {
    type: Number,
    default: 20
  }
});

const controlsRef = ref(null);
const lightPosition = ref([0, 4, 0]);
const chandelierParts = ref([]);
const modelScene = shallowRef(null);
const modelPath = "/models/scene.glb";
const allMeshes = [];
let highlightIndex = -1;

// 1. 核心加载逻辑：使用原生 GLTFLoader 确保没有任何库的中间层魔法
const loadModelManual = () => {
  console.log("--- 🚀 开始原生模型加载流程 ---");
  const loader = new GLTFLoader();
  
  loader.load(
    modelPath,
    (gltf) => {
      console.log("✅ 原生加载成功:", gltf);
      const scene = gltf.scene || gltf;
      
      if (scene && typeof scene.traverse === 'function') {
        processModel(scene);
        modelScene.value = scene;
        console.log("✅ 模型已挂载到场景中");
      } else {
        console.error("❌ 加载到的对象不支持 traverse 操作:", scene);
      }
    },
    (progress) => {
      const percent = (progress.loaded / progress.total * 100).toFixed(2);
      console.log(`正在加载: ${percent}%`);
    },
    (error) => {
      console.error("❌ 原生加载失败:", error);
    }
  );
};

// 2. 极致健壮的模型处理
const processModel = (model) => {
  if (!model || typeof model.traverse !== 'function') {
    console.error("❌ 无法处理无效的模型对象");
    return;
  }
  
  console.log("%c--- 🔍 模型组件层级结构 ---", "color: blue; font-weight: bold;");
  
  model.traverse((child) => {
    // 1. 计算深度用于缩进
    let depth = 0;
    let current = child;
    while (current.parent && current !== model) {
      depth++;
      current = current.parent;
    }
    const indent = '  '.repeat(depth);

    // 2. 打印带缩进的层级信息
    console.log(`${indent}➡️ [${child.type}] ${child.name || '(未命名)'}`);

    // 3. 开启阴影
    if (child.isMesh) {
      child.castShadow = true;
      child.receiveShadow = true;
      allMeshes.push(child);
      if (child.material) {
        child.material.side = THREE.DoubleSide; // 双面渲染防止看不见
      }
    }
  });
  console.log("%c--- ✅ 模型组件遍历完成 ---", "color: blue; font-weight: bold;");

  // 几何中心校准
  const box = new THREE.Box3().setFromObject(model);
  const center = box.getCenter(new THREE.Vector3());
  const size = box.getSize(new THREE.Vector3());
  
  // 强制归零，解决模型偏离中心的问题
  model.position.x = -center.x;
  model.position.y = -center.y;
  model.position.z = -center.z;

  const maxDim = Math.max(size.x, size.y, size.z);

  // 在下个 Tick 更新相机，确保控制器的 target 和 position 同步
  if (controlsRef.value) {
    const controls = controlsRef.value.instance;
    controls.target.set(0, 0, 0);
    const dist = maxDim * 2;
    controls.object.position.set(dist, dist, dist);
    controls.update();
  }

  // 吊灯定位逻辑
  const targetNames = ['Light1'];
  let chandelierCenter = new THREE.Vector3();
  let count = 0;

  model.traverse((object) => {
    if (object.name && targetNames.includes(object.name) && object.isMesh) {
      const worldPos = new THREE.Vector3();
      object.getWorldPosition(worldPos);
      chandelierCenter.add(worldPos);
      count++;
      chandelierParts.value.push(object);
      console.log(`[灯光组件识别] 发现名为 '${object.name}' 的网格，已添加到发光列表。`, object);
    }
  });

  if (count > 0) {
    chandelierCenter.divideScalar(count);
    // 这里需要同步位移
    lightPosition.value = [
      chandelierCenter.x - center.x, 
      chandelierCenter.y - center.y - 0.5, 
      chandelierCenter.z - center.z
    ];
  }
};

onMounted(() => {
  loadModelManual();
});

// 高亮下一个组件的函数
function highlightNextComponent() {
  if (allMeshes.length === 0) return;

  // 恢复上一个高亮的组件
  if (highlightIndex >= 0) {
    const prevMesh = allMeshes[highlightIndex];
    if (prevMesh.originalMaterial) {
      prevMesh.material = prevMesh.originalMaterial;
    }
  }

  // 循环高亮
  highlightIndex = (highlightIndex + 1) % allMeshes.length;
  const currentMesh = allMeshes[highlightIndex];

  // 保存原始材质并设置新颜色
  if (currentMesh) {
    currentMesh.originalMaterial = currentMesh.material;
    currentMesh.material = currentMesh.material.clone();
    currentMesh.material.color.set(0x00ff00); // 绿色
    console.log(`高亮组件: ${currentMesh.name || '(未命名)'}`);
  }
}

// 监听开关灯
watch(() => props.isLightOn, (isOn) => {
  chandelierParts.value.forEach(part => {
    if (part.material) {
      part.material.emissive.set(isOn ? 0xffff00c : 0x000000);
      part.material.emissiveIntensity = isOn ? 1 : 0;
    }
  });
});
</script>

<style scoped>
.highlight-button {
  position: absolute;
  top: 10px;
  right: 10px;
  z-index: 10;
  padding: 8px 12px;
  background-color: #4CAF50;
  color: white;
  border: none;
  border-radius: 4px;
  cursor: pointer;
}

.highlight-button:hover {
  background-color: #45a049;
}
/* TresCanvas 会自动撑满容器 */
</style>
