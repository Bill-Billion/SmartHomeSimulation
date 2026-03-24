<template>
  <div class="app-shell">
    <aside class="side-panel">
      <section class="panel-card">
        <p class="eyebrow">第一阶段</p>
        <h1>上传 CAD 或平面图，直接生成 3D 场景</h1>
        <p class="panel-text">
          支持 JPG、PNG、PDF、DXF。DWG 会收到转换提示。当前阶段走规则生成，重点是让上传、生成和预览链路稳定可用。
        </p>

        <form class="upload-form" @submit.prevent="submitUpload">
          <label class="file-picker">
            <span>{{ selectedFile ? selectedFile.name : '选择户型文件' }}</span>
            <input
              type="file"
              accept=".jpg,.jpeg,.png,.pdf,.dxf,.dwg"
              @change="handleFileChange"
            >
          </label>
          <button type="submit" :disabled="isSubmitting || !selectedFile">
            {{ isSubmitting ? '正在提交' : '开始生成' }}
          </button>
        </form>

        <p class="hint-text">
          推荐优先使用线条清晰的 JPG、PNG、PDF 或 DXF。若上传 DWG，后端会提示先转换格式。
        </p>
      </section>

      <section class="panel-card" v-if="jobState">
        <div class="status-row">
          <span class="status-label">任务状态</span>
          <strong :class="['status-chip', `status-${jobState.status}`]">
            {{ statusText(jobState.status) }}
          </strong>
        </div>
        <p class="panel-text">{{ jobState.message }}</p>
        <p class="meta-line" v-if="jobState.job_id">任务编号：{{ jobState.job_id }}</p>
        <p class="meta-line" v-if="jobState.confidence != null">
          识别置信度：{{ Math.round(jobState.confidence * 100) }}%
        </p>
        <p class="error-text" v-if="jobState.error">{{ jobState.error }}</p>
        <ul class="warning-list" v-if="jobState.warnings?.length">
          <li v-for="warning in jobState.warnings" :key="warning">{{ warning }}</li>
        </ul>
      </section>

      <section class="panel-card" v-if="sceneSpec">
        <div class="status-row">
          <span class="status-label">场景摘要</span>
          <strong>{{ sceneSpec.scene_id }}</strong>
        </div>
        <p class="meta-line">房间数量：{{ sceneSpec.rooms.length }}</p>
        <p class="meta-line">墙体数量：{{ sceneSpec.walls.length }}</p>
        <p class="meta-line">开口数量：{{ sceneSpec.openings.length }}</p>
        <p class="meta-line">家具数量：{{ sceneSpec.furnitures.length }}</p>
        <p class="meta-line">
          场景尺寸：{{ sceneSpec.bounds_width_m.toFixed(1) }}m × {{ sceneSpec.bounds_depth_m.toFixed(1) }}m
        </p>
      </section>
    </aside>

    <main class="scene-stage">
      <div class="stage-header">
        <div class="view-switch">
          <button
            type="button"
            :class="{ active: activeView === 'plan' }"
            :disabled="!sceneSpec"
            @click="activeView = 'plan'"
          >
            户型图
          </button>
          <button
            type="button"
            :class="{ active: activeView === 'model' }"
            @click="activeView = 'model'"
          >
            3D 模型
          </button>
        </div>
      </div>

      <FloorplanPreview
        v-if="activeView === 'plan'"
        :scene-spec="sceneSpec"
      />
      <ThreeScene
        v-else
        :model-url="modelUrl"
        :scene-label="sceneSpec?.scene_id || '默认示例场景'"
      />
    </main>
  </div>
</template>

<script setup>
import { onBeforeUnmount, ref } from 'vue';
import FloorplanPreview from './FloorplanPreview.vue';
import ThreeScene from './ThreeScene.vue';

const apiBase = (import.meta.env.VITE_API_BASE || '').replace(/\/$/, '');
const selectedFile = ref(null);
const isSubmitting = ref(false);
const jobState = ref(null);
const sceneSpec = ref(null);
const modelUrl = ref('/models/scene.glb');
const activeView = ref('model');
let pollTimer = null;

function buildApiPath(path) {
  return `${apiBase}${path}`;
}

function handleFileChange(event) {
  const [file] = event.target.files || [];
  selectedFile.value = file || null;
}

function statusText(status) {
  const mapping = {
    pending: '等待处理',
    processing: '处理中',
    completed: '已完成',
    failed: '失败'
  };
  return mapping[status] || status;
}

function stopPolling() {
  if (pollTimer) {
    window.clearTimeout(pollTimer);
    pollTimer = null;
  }
}

async function submitUpload() {
  if (!selectedFile.value) {
    return;
  }

  stopPolling();
  sceneSpec.value = null;
  activeView.value = 'model';
  isSubmitting.value = true;
  jobState.value = {
    job_id: '',
    status: 'pending',
    message: '正在上传文件。',
    warnings: [],
    error: null
  };

  const formData = new FormData();
  formData.append('file', selectedFile.value);

  try {
    const response = await fetch(buildApiPath('/api/floorplans:generate'), {
      method: 'POST',
      body: formData
    });
    const payload = await response.json();

    if (!response.ok) {
      throw new Error(payload.detail || '上传失败，请稍后重试。');
    }

    jobState.value = {
      ...jobState.value,
      ...payload,
      message: '文件已提交，正在排队生成场景。'
    };
    await pollJob(payload.job_id);
  } catch (error) {
    jobState.value = {
      ...jobState.value,
      status: 'failed',
      message: '上传失败。',
      error: error.message
    };
  } finally {
    isSubmitting.value = false;
  }
}

async function pollJob(jobId) {
  try {
    const response = await fetch(buildApiPath(`/api/jobs/${jobId}`));
    const payload = await response.json();

    if (!response.ok) {
      throw new Error(payload.detail || '无法读取任务状态。');
    }

    jobState.value = payload;

    if (payload.status === 'completed' && payload.scene_url && payload.model_url) {
      modelUrl.value = `${buildApiPath(payload.model_url)}?t=${Date.now()}`;
      await loadScene(payload.scene_url);
      activeView.value = 'plan';
      stopPolling();
      return;
    }

    if (payload.status === 'failed') {
      stopPolling();
      return;
    }

    pollTimer = window.setTimeout(() => {
      pollJob(jobId);
    }, 1200);
  } catch (error) {
    jobState.value = {
      ...jobState.value,
      status: 'failed',
      message: '查询任务状态失败。',
      error: error.message
    };
    stopPolling();
  }
}

async function loadScene(sceneUrl) {
  const response = await fetch(buildApiPath(sceneUrl));
  const payload = await response.json();

  if (!response.ok) {
    throw new Error(payload.detail || '场景详情读取失败。');
  }

  sceneSpec.value = payload;
}

onBeforeUnmount(() => {
  stopPolling();
});
</script>

<style scoped>
.app-shell {
  display: grid;
  grid-template-columns: 360px 1fr;
  width: 100%;
  height: 100%;
  background:
    radial-gradient(circle at top left, rgba(226, 215, 192, 0.18), transparent 24%),
    radial-gradient(circle at bottom right, rgba(82, 122, 111, 0.18), transparent 26%),
    #f5f1e8;
}

.side-panel {
  overflow-y: auto;
  padding: 20px 18px;
  border-right: 1px solid rgba(49, 61, 57, 0.12);
  background: rgba(255, 252, 245, 0.82);
  backdrop-filter: blur(10px);
}

.panel-card {
  margin-bottom: 16px;
  padding: 18px;
  border-radius: 18px;
  background: rgba(255, 255, 255, 0.78);
  box-shadow: 0 14px 28px rgba(49, 61, 57, 0.08);
}

.eyebrow {
  margin: 0 0 8px;
  font-size: 12px;
  font-weight: 700;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  color: #6d7f78;
}

h1 {
  margin: 0 0 12px;
  font-size: 28px;
  line-height: 1.1;
  color: #22312c;
}

.panel-text,
.hint-text,
.meta-line,
.error-text {
  margin: 0 0 10px;
  line-height: 1.6;
  color: #42514c;
}

.upload-form {
  display: grid;
  gap: 12px;
  margin-top: 16px;
}

.file-picker {
  display: block;
  padding: 14px 16px;
  border: 1px dashed #9aaea7;
  border-radius: 14px;
  background: #fbfaf6;
  color: #314744;
  cursor: pointer;
}

.file-picker input {
  display: none;
}

button {
  padding: 12px 14px;
  border: none;
  border-radius: 14px;
  background: #314744;
  color: #f7f3ea;
  font-size: 15px;
  cursor: pointer;
}

button:disabled {
  cursor: not-allowed;
  opacity: 0.56;
}

.status-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 10px;
}

.status-label {
  color: #61726c;
  font-size: 14px;
}

.status-chip {
  padding: 5px 10px;
  border-radius: 999px;
  font-size: 12px;
}

.status-pending {
  background: #ece6d6;
  color: #7b6730;
}

.status-processing {
  background: #d8e5e2;
  color: #245a51;
}

.status-completed {
  background: #dce7d7;
  color: #426037;
}

.status-failed {
  background: #efd9d3;
  color: #8b4131;
}

.warning-list {
  margin: 0;
  padding-left: 18px;
  color: #796746;
  line-height: 1.5;
}

.error-text {
  color: #8b4131;
}

.scene-stage {
  position: relative;
  min-width: 0;
  display: flex;
  flex-direction: column;
}

.stage-header {
  position: absolute;
  top: 18px;
  left: 50%;
  z-index: 20;
  transform: translateX(-50%);
}

.view-switch {
  display: flex;
  padding: 4px;
  border-radius: 16px;
  background: rgba(26, 30, 36, 0.85);
  box-shadow: 0 14px 28px rgba(18, 20, 24, 0.24);
}

.view-switch button {
  min-width: 118px;
  background: transparent;
  color: rgba(244, 240, 232, 0.72);
}

.view-switch button.active {
  background: rgba(255, 255, 255, 0.08);
  color: #fff8ec;
}

@media (max-width: 980px) {
  .app-shell {
    grid-template-columns: 1fr;
    grid-template-rows: auto 1fr;
  }

  .side-panel {
    border-right: none;
    border-bottom: 1px solid rgba(49, 61, 57, 0.12);
  }
}
</style>
