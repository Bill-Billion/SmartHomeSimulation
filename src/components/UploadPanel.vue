<template>
  <section class="panel-card">
    <p class="eyebrow">第一阶段</p>
    <h1>上传 CAD 或平面图，直接生成 3D 场景</h1>
    <p class="panel-text">
      支持 JPG、PNG、PDF、DXF。DWG 会收到转换提示。当前阶段走规则生成，重点是让上传、生成和预览链路稳定可用。
    </p>

    <form class="upload-form" @submit.prevent="$emit('submit')">
      <label class="file-picker">
        <span>{{ selectedFileName || '选择户型文件' }}</span>
        <input
          type="file"
          accept=".jpg,.jpeg,.png,.pdf,.dxf,.dwg"
          @change="emitFileChange"
        >
      </label>
      <button type="submit" :disabled="isSubmitting || !selectedFileName">
        {{ isSubmitting ? '正在提交' : '开始生成' }}
      </button>
    </form>

    <details class="llm-panel">
      <summary>AI 辅助理解（实验）</summary>
      <label class="toggle-row">
        <input v-model="llmOptions.enabled" type="checkbox">
        <span>允许用户在本次上传里接入自己的 OpenAI 兼容模型</span>
      </label>

      <div v-if="llmOptions.enabled" class="llm-fields">
        <label class="field-label">
          兼容接口地址
          <input
            v-model.trim="llmOptions.baseUrl"
            type="text"
            placeholder="https://api.openai.com/v1"
          >
        </label>
        <label class="field-label">
          模型名
          <input
            v-model.trim="llmOptions.model"
            type="text"
            placeholder="gpt-4.1-mini"
          >
        </label>
        <label class="field-label">
          API Key
          <input
            v-model.trim="llmOptions.apiKey"
            type="password"
            autocomplete="off"
            placeholder="仅本次请求使用"
          >
        </label>
      </div>
    </details>

    <p class="hint-text">
      推荐优先使用线条清晰的 JPG、PNG、PDF 或 DXF。若上传 DWG，后端会提示先转换格式。
    </p>
    <p class="hint-text llm-hint">
      AI 实验增强默认关闭。开启后只会补语义理解和家具摆放建议，不会改墙体几何，也不会把密钥写入任务文件。
    </p>
  </section>
</template>

<script setup>
const props = defineProps({
  selectedFileName: {
    type: String,
    default: ''
  },
  isSubmitting: {
    type: Boolean,
    default: false
  },
  llmOptions: {
    type: Object,
    required: true
  }
});

const emit = defineEmits(['file-change', 'submit']);

function emitFileChange(event) {
  emit('file-change', event);
}
</script>

<style scoped>
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
.hint-text {
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

.llm-panel {
  margin-top: 14px;
  padding: 12px 14px;
  border: 1px solid rgba(85, 106, 99, 0.12);
  border-radius: 16px;
  background: rgba(246, 243, 236, 0.78);
}

.llm-panel summary {
  cursor: pointer;
  font-weight: 600;
  color: #314744;
}

.toggle-row {
  display: flex;
  gap: 10px;
  align-items: flex-start;
  margin-top: 14px;
  color: #42514c;
}

.llm-fields {
  display: grid;
  gap: 12px;
  margin-top: 14px;
}

.field-label {
  display: grid;
  gap: 6px;
  color: #42514c;
  font-size: 14px;
}

.field-label input {
  padding: 10px 12px;
  border: 1px solid rgba(125, 142, 136, 0.5);
  border-radius: 12px;
  background: rgba(255, 255, 255, 0.9);
  color: #24322d;
}

.llm-hint {
  margin-bottom: 0;
}
</style>
