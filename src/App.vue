<template>
  <div class="app-shell">
    <aside class="side-panel">
      <UploadPanel
        :selected-file-name="selectedFile?.name || ''"
        :is-submitting="isSubmitting"
        :llm-options="llmOptions"
        @file-change="handleFileChange"
        @submit="submitUpload"
      />
      <JobStatusCard :job-state="jobState" />
      <SceneSummaryCard :scene-spec="sceneSpec" />
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
        :scene-camera="sceneSpec?.camera || null"
      />
    </main>
  </div>
</template>

<script setup>
import { onBeforeUnmount, reactive, ref } from 'vue';
import JobStatusCard from './components/JobStatusCard.vue';
import SceneSummaryCard from './components/SceneSummaryCard.vue';
import UploadPanel from './components/UploadPanel.vue';
import FloorplanPreview from './FloorplanPreview.vue';
import ThreeScene from './ThreeScene.vue';

const apiBase = (import.meta.env.VITE_API_BASE || '').replace(/\/$/, '');
const selectedFile = ref(null);
const isSubmitting = ref(false);
const jobState = ref(null);
const sceneSpec = ref(null);
const modelUrl = ref('/models/scene.glb');
const activeView = ref('model');
const activeRequestKey = ref(0);
const llmOptions = reactive({
  enabled: false,
  baseUrl: 'https://api.openai.com/v1',
  model: '',
  apiKey: ''
});
let pollTimer = null;
let pollController = null;
let sceneController = null;

function buildApiPath(path) {
  return `${apiBase}${path}`;
}

function handleFileChange(event) {
  const [file] = event.target.files || [];
  selectedFile.value = file || null;
}

function stopPolling() {
  if (pollTimer) {
    window.clearTimeout(pollTimer);
    pollTimer = null;
  }
}

function abortInFlightRequests() {
  if (pollController) {
    pollController.abort();
    pollController = null;
  }
  if (sceneController) {
    sceneController.abort();
    sceneController = null;
  }
}

async function submitUpload() {
  if (!selectedFile.value) {
    return;
  }

  activeRequestKey.value += 1;
  const requestKey = activeRequestKey.value;
  stopPolling();
  abortInFlightRequests();
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
  if (llmOptions.enabled) {
    formData.append('llm_enabled', 'true');
    formData.append('llm_base_url', llmOptions.baseUrl);
    formData.append('llm_model', llmOptions.model);
    formData.append('llm_api_key', llmOptions.apiKey);
  }

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
    await pollJob(payload.job_id, requestKey);
  } catch (error) {
    if (error.name === 'AbortError' || requestKey !== activeRequestKey.value) {
      return;
    }
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

async function pollJob(jobId, requestKey) {
  if (requestKey !== activeRequestKey.value) {
    return;
  }
  const controller = new AbortController();
  try {
    pollController?.abort();
    pollController = controller;
    const response = await fetch(buildApiPath(`/api/jobs/${jobId}`), {
      signal: controller.signal
    });
    const payload = await response.json();
    if (requestKey !== activeRequestKey.value) {
      return;
    }

    if (!response.ok) {
      throw new Error(payload.detail || '无法读取任务状态。');
    }

    jobState.value = payload;

    if (payload.status === 'completed' && payload.scene_url && payload.model_url) {
      modelUrl.value = `${buildApiPath(payload.model_url)}?t=${Date.now()}`;
      await loadScene(payload.scene_url, requestKey);
      if (requestKey !== activeRequestKey.value) {
        return;
      }
      activeView.value = 'plan';
      stopPolling();
      return;
    }

    if (payload.status === 'failed') {
      stopPolling();
      return;
    }

    pollTimer = window.setTimeout(() => {
      pollJob(jobId, requestKey);
    }, 1200);
  } catch (error) {
    if (error.name === 'AbortError' || requestKey !== activeRequestKey.value) {
      return;
    }
    jobState.value = {
      ...jobState.value,
      status: 'failed',
      message: '查询任务状态失败。',
      error: error.message
    };
    stopPolling();
  } finally {
    if (pollController === controller) {
      pollController = null;
    }
  }
}

async function loadScene(sceneUrl, requestKey) {
  if (requestKey !== activeRequestKey.value) {
    return;
  }
  const controller = new AbortController();
  sceneController?.abort();
  sceneController = controller;
  try {
    const response = await fetch(buildApiPath(sceneUrl), { signal: controller.signal });
    const payload = await response.json();
    if (requestKey !== activeRequestKey.value) {
      return;
    }

    if (!response.ok) {
      throw new Error(payload.detail || '场景详情读取失败。');
    }

    sceneSpec.value = payload;
  } finally {
    if (sceneController === controller) {
      sceneController = null;
    }
  }
}

onBeforeUnmount(() => {
  stopPolling();
  abortInFlightRequests();
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
  padding: 12px 14px;
  border: none;
  border-radius: 14px;
  background: transparent;
  color: rgba(244, 240, 232, 0.72);
  font-size: 15px;
  cursor: pointer;
}

.view-switch button:disabled {
  cursor: not-allowed;
  opacity: 0.56;
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
