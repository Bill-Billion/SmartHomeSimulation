<template>
  <section class="panel-card">
    <details>
      <summary>分析面板（调试）</summary>
      <p v-if="!diagnostics" class="meta-line">
        场景生成完成后可查看解析诊断信息。
      </p>

      <div v-else class="panel-content">
        <div class="row">
          <span>源类型：{{ diagnostics.parse_summary.source_type }}</span>
          <span>房间：{{ diagnostics.parse_summary.room_count }}</span>
          <span>墙体：{{ diagnostics.parse_summary.wall_count }}</span>
          <span>开口：{{ diagnostics.parse_summary.opening_count }}</span>
        </div>

        <div class="overlay-box">
          <label class="toggle-row">
            <input
              v-model="overlayEnabled"
              type="checkbox"
              @change="emitOverlayChange"
            >
            <span>显示 2D 源图叠加</span>
          </label>
          <label class="range-row">
            叠加透明度
            <input
              v-model.number="overlayOpacity"
              type="range"
              min="0.1"
              max="0.9"
              step="0.05"
              :disabled="!overlayEnabled"
              @input="emitOverlayChange"
            >
            <span>{{ Math.round(overlayOpacity * 100) }}%</span>
          </label>
          <img
            v-if="sourcePreviewUrl"
            :src="sourcePreviewUrl"
            alt="源图预览"
            class="source-preview"
          >
          <p v-else class="meta-line">当前任务没有可用源图预览。</p>
        </div>

        <div class="room-box">
          <p class="block-title">房间诊断</p>
          <ul class="room-list">
            <li
              v-for="room in diagnostics.room_diagnostics"
              :key="room.room_id"
              :class="{ active: selectedRoomId === room.room_id }"
              @click="emit('room-select', room.room_id)"
            >
              <p>{{ room.name }} · {{ room.chosen_type }}</p>
              <p>置信度：{{ Math.round(room.confidence * 100) }}%</p>
              <p v-if="room.evidence_flags?.length">证据：{{ room.evidence_flags.join(' / ') }}</p>
              <p v-if="room.fallback_flags?.length">降级：{{ room.fallback_flags.join(' / ') }}</p>
            </li>
          </ul>
        </div>

        <div class="ai-box">
          <p class="block-title">AI 诊断</p>
          <p class="meta-line">状态：{{ diagnostics.ai_diagnostics.status }}</p>
          <p class="meta-line" v-if="diagnostics.ai_diagnostics.model">
            模型：{{ diagnostics.ai_diagnostics.model }}
          </p>
          <p class="meta-line" v-if="diagnostics.ai_diagnostics.failure_reason">
            原因：{{ diagnostics.ai_diagnostics.failure_reason }}
          </p>
        </div>
      </div>
    </details>
  </section>
</template>

<script setup>
import { ref, watch } from 'vue';

const props = defineProps({
  diagnostics: {
    type: Object,
    default: null
  },
  sourcePreviewUrl: {
    type: String,
    default: ''
  },
  selectedRoomId: {
    type: String,
    default: ''
  }
});

const emit = defineEmits(['overlay-change', 'room-select']);
const overlayEnabled = ref(false);
const overlayOpacity = ref(0.45);

watch(
  () => props.diagnostics?.job_id,
  () => {
    overlayEnabled.value = false;
    overlayOpacity.value = 0.45;
    emitOverlayChange();
  }
);

function emitOverlayChange() {
  emit('overlay-change', {
    enabled: overlayEnabled.value,
    opacity: overlayOpacity.value
  });
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

summary {
  cursor: pointer;
  color: #314744;
  font-weight: 600;
}

.panel-content {
  margin-top: 12px;
  display: grid;
  gap: 12px;
}

.row {
  display: flex;
  flex-wrap: wrap;
  gap: 8px 14px;
  color: #42514c;
  font-size: 13px;
}

.overlay-box,
.room-box,
.ai-box {
  padding: 10px 12px;
  border-radius: 12px;
  border: 1px solid rgba(90, 105, 101, 0.14);
  background: rgba(248, 246, 239, 0.76);
}

.toggle-row,
.range-row {
  display: flex;
  align-items: center;
  gap: 8px;
  color: #42514c;
  margin-bottom: 8px;
}

.source-preview {
  width: 100%;
  border-radius: 8px;
  border: 1px solid rgba(117, 129, 124, 0.24);
}

.block-title {
  margin: 0 0 8px;
  color: #33433e;
  font-weight: 700;
}

.room-list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: grid;
  gap: 8px;
}

.room-list li {
  padding: 8px;
  border-radius: 8px;
  background: rgba(255, 255, 255, 0.7);
  cursor: pointer;
  color: #3e4a46;
}

.room-list li.active {
  outline: 2px solid rgba(70, 117, 99, 0.4);
}

.room-list p {
  margin: 0 0 4px;
  font-size: 13px;
}

.meta-line {
  margin: 0 0 6px;
  color: #42514c;
  line-height: 1.5;
}
</style>
