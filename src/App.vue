<template>
    <div class="ui-controls">
        <div class="control-panel">
            <b>吊灯控制</b>
            <div class="button-group">
                <button @click="isLightOn = true">开灯</button>
                <button @click="isLightOn = false">关灯</button>
            </div>
            <div class="slider-group">
                <label>灯光渲染距离 (0-10000):</label>
                <input type="range" v-model.number="lightDistance" min="0" max="10000" step="1">
                <span>{{ lightDistance }}</span>
            </div>
        </div>
    </div>
    <div class="scene-container">
        <ThreeScene ref="threeSceneRef" :is-light-on="isLightOn" :light-distance="lightDistance" />
    </div>
</template>

<script setup>
import { ref } from 'vue';
import ThreeScene from './ThreeScene.vue';

const isLightOn = ref(false);
const lightDistance = ref(20);
const threeSceneRef = ref(null);

const highlightNextComponent = () => {
  if (threeSceneRef.value) {
    threeSceneRef.value.highlightNextComponent();
  }
};
</script>

<style>
/* 全局样式确保画布占满屏幕 */
html, body, #app {
    margin: 0;
    padding: 0;
    width: 100%;
    height: 100%;
    overflow: hidden;
}

.scene-container {
    width: 100%;
    height: 100%;
    background-color: #111;
}

.ui-controls {
    position: absolute;
    top: 10px;
    left: 10px;
    z-index: 100;
}

.control-panel {
    background: rgba(255, 255, 255, 0.8);
    padding: 15px;
    border-radius: 8px;
    display: flex;
    flex-direction: column;
    gap: 10px;
}

.button-group button {
    padding: 8px 12px;
    margin-right: 5px;
    cursor: pointer;
    border: 1px solid #ccc;
    border-radius: 4px;
}

.slider-group {
    display: flex;
    flex-direction: column;
    gap: 5px;
}

.slider-group label {
    font-size: 14px;
}

.slider-group input {
    width: 200px;
}
</style>
