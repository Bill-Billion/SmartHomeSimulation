<template>
  <div class="plan-shell">
    <svg
      v-if="sceneSpec?.rooms?.length"
      class="plan-svg"
      :viewBox="viewBox"
      preserveAspectRatio="xMidYMid meet"
    >
      <rect
        :x="bounds.minX - padding"
        :y="-(bounds.maxZ + padding)"
        :width="bounds.width + padding * 2"
        :height="bounds.height + padding * 2"
        fill="#6a707f"
        rx="0.2"
      />
      <g transform="scale(1,-1)">
        <polygon
          v-for="room in sceneSpec.rooms"
          :key="room.room_id"
          :points="polygonPoints(room.polygon)"
          :fill="roomFill(room.room_type)"
          stroke="#26282f"
          :stroke-width="strokeWidth"
          stroke-linejoin="round"
        />
        <line
          v-for="wall in sceneSpec.walls"
          :key="wall.wall_id"
          :x1="wall.start.x"
          :y1="wall.start.z"
          :x2="wall.end.x"
          :y2="wall.end.z"
          stroke="#1f2127"
          :stroke-width="wallStrokeWidth"
          stroke-linecap="square"
        />
      </g>
      <g v-for="room in roomLabels" :key="room.room_id">
        <text
          :x="room.center.x"
          :y="room.center.y - 0.24"
          class="room-name"
          text-anchor="middle"
        >
          {{ room.title }}
        </text>
        <text
          :x="room.center.x"
          :y="room.center.y + 0.18"
          class="room-area"
          text-anchor="middle"
        >
          {{ room.area }}
        </text>
      </g>
    </svg>

    <div v-else class="empty-state">
      场景生成完成后，这里会显示可对照的户型图。
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue';

const props = defineProps({
  sceneSpec: {
    type: Object,
    default: null
  }
});

const padding = 1.2;
const strokeWidth = 0.08;
const wallStrokeWidth = 0.16;

const bounds = computed(() => {
  const polygons = props.sceneSpec?.rooms?.flatMap((room) => room.polygon || []) || [];
  if (polygons.length === 0) {
    return {
      minX: -6,
      maxX: 6,
      minZ: -8,
      maxZ: 8,
      width: 12,
      height: 16
    };
  }

  const xs = polygons.map((point) => point.x);
  const zs = polygons.map((point) => point.z);
  const minX = Math.min(...xs);
  const maxX = Math.max(...xs);
  const minZ = Math.min(...zs);
  const maxZ = Math.max(...zs);
  return {
    minX,
    maxX,
    minZ,
    maxZ,
    width: maxX - minX || 12,
    height: maxZ - minZ || 16
  };
});

const viewBox = computed(() => {
  const value = bounds.value;
  return `${value.minX - padding} ${-(value.maxZ + padding)} ${value.width + padding * 2} ${value.height + padding * 2}`;
});

const roomLabels = computed(() => {
  const rooms = props.sceneSpec?.rooms || [];
  return rooms.map((room) => {
    const center = roomCenter(room.polygon || []);
    return {
      room_id: room.room_id,
      title: roomTypeLabel(room.room_type),
      area: `${Number(room.area_sqm || 0).toFixed(1)}m²`,
      center: {
        x: center.x,
        y: -center.z
      }
    };
  });
});

function polygonPoints(points) {
  return points.map((point) => `${point.x},${point.z}`).join(' ');
}

function roomCenter(points) {
  if (!points?.length) {
    return { x: 0, z: 0 };
  }

  const sum = points.reduce(
    (accumulator, point) => {
      accumulator.x += point.x;
      accumulator.z += point.z;
      return accumulator;
    },
    { x: 0, z: 0 }
  );

  return {
    x: sum.x / points.length,
    z: sum.z / points.length
  };
}

function roomTypeLabel(roomType) {
  const mapping = {
    bedroom: '卧室',
    living_room: '客厅',
    kitchen: '厨房',
    bathroom: '卫生间',
    corridor: '走廊',
    generic: '通用空间'
  };
  return mapping[roomType] || '空间';
}

function roomFill(roomType) {
  const mapping = {
    bedroom: '#e6c98d',
    living_room: '#eee4cf',
    kitchen: '#f3f0e8',
    bathroom: '#d8ebf0',
    corridor: '#efe7d6',
    generic: '#e9e5d9'
  };
  return mapping[roomType] || '#e9e5d9';
}
</script>

<style scoped>
.plan-shell {
  width: 100%;
  height: 100%;
  background: linear-gradient(180deg, #797f90 0%, #646a79 100%);
}

.plan-svg {
  width: 100%;
  height: 100%;
}

.room-name,
.room-area {
  fill: #23262d;
  font-family: "PingFang SC", "Noto Sans SC", "Microsoft YaHei", sans-serif;
}

.room-name {
  font-size: 0.42px;
  font-weight: 700;
}

.room-area {
  font-size: 0.28px;
}

.empty-state {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 100%;
  height: 100%;
  color: rgba(255, 255, 255, 0.85);
}
</style>

