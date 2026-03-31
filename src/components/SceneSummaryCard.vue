<template>
  <section v-if="sceneSpec" class="panel-card">
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
</template>

<script setup>
defineProps({
  sceneSpec: {
    type: Object,
    default: null
  }
});
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

.meta-line {
  margin: 0 0 10px;
  line-height: 1.6;
  color: #42514c;
}
</style>
