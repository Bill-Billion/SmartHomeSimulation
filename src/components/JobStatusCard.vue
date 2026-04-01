<template>
  <section v-if="jobState" class="panel-card">
    <div class="status-row">
      <span class="status-label">任务状态</span>
      <strong :class="['status-chip', `status-${jobState.status}`]">
        {{ statusText(jobState.status) }}
      </strong>
    </div>
    <p class="panel-text">{{ jobState.message }}</p>
    <p v-if="jobState.job_id" class="meta-line">任务编号：{{ jobState.job_id }}</p>
    <p v-if="jobState.confidence != null" class="meta-line">
      识别置信度：{{ Math.round(jobState.confidence * 100) }}%
    </p>
    <p v-if="jobState.error" class="error-text">{{ jobState.error }}</p>
    <ul v-if="jobState.warnings?.length" class="warning-list">
      <li v-for="warning in jobState.warnings" :key="warning">{{ warning }}</li>
    </ul>
  </section>
</template>

<script setup>
defineProps({
  jobState: {
    type: Object,
    default: null
  }
});

function statusText(status) {
  const mapping = {
    pending: '等待处理',
    processing: '处理中',
    completed: '已完成',
    failed: '失败'
  };
  return mapping[status] || status;
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

.panel-text,
.meta-line,
.error-text {
  margin: 0 0 10px;
  line-height: 1.6;
  color: #42514c;
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
</style>
