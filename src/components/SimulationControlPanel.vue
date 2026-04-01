<template>
  <section class="panel-card">
    <div class="status-row">
      <span class="status-label">仿真控制</span>
      <strong class="status-chip" :class="{ active: session }">
        {{ session ? '会话已创建' : '未创建会话' }}
      </strong>
    </div>

    <p v-if="!sceneSpec" class="panel-text">
      先完成一次场景生成，再创建仿真会话。
    </p>

    <template v-else>
      <p class="panel-text">
        当前场景：{{ sceneSpec.scene_id }}
      </p>
      <button type="button" class="primary-btn" :disabled="isCreating" @click="createSession">
        {{ isCreating ? '正在创建会话' : '创建仿真会话' }}
      </button>

      <div v-if="session" class="command-panel">
        <label class="field-label">
          命令
          <input
            v-model.trim="commandText"
            type="text"
            placeholder="例如：打开卧室 1 的灯"
            :disabled="isSending"
          >
        </label>

        <div class="action-row">
          <button type="button" :disabled="isSending || !commandText" @click="sendCommand">
            {{ isSending ? '执行中' : '执行命令' }}
          </button>
          <button type="button" class="ghost-btn" :disabled="isRefreshing" @click="refreshSession">
            刷新状态
          </button>
        </div>

        <div class="quick-row">
          <label class="field-label">
            快捷房间
            <select v-model="selectedRoomId">
              <option value="">请选择房间</option>
              <option
                v-for="room in worldRooms"
                :key="room.room_id"
                :value="room.room_id"
              >
                {{ room.room_name }}
              </option>
            </select>
          </label>
          <div class="action-row">
            <button type="button" :disabled="!selectedRoomId || isSending" @click="sendQuickCommand('on')">
              打开房间主灯
            </button>
            <button type="button" :disabled="!selectedRoomId || isSending" @click="sendQuickCommand('off')">
              关闭房间主灯
            </button>
          </div>
        </div>

        <div class="state-block">
          <p class="block-title">灯光状态</p>
          <ul v-if="deviceRows.length" class="plain-list">
            <li v-for="device in deviceRows" :key="device.device_id">
              <span>{{ device.name }}</span>
              <strong :class="{ on: device.is_on }">
                {{ device.is_on ? '开启' : '关闭' }}
              </strong>
            </li>
          </ul>
          <p v-else class="meta-line">暂无设备状态。</p>
        </div>

        <div class="state-block">
          <p class="block-title">事件流</p>
          <ul v-if="events.length" class="event-list">
            <li v-for="event in events" :key="event.event_id">
              <span class="event-kind">{{ event.kind }}</span>
              <span class="event-message">{{ event.message }}</span>
            </li>
          </ul>
          <p v-else class="meta-line">暂无事件。</p>
        </div>
      </div>
    </template>

    <p v-if="errorMessage" class="error-text">{{ errorMessage }}</p>
  </section>
</template>

<script setup>
import { computed, ref, watch } from 'vue';

const props = defineProps({
  apiBase: {
    type: String,
    default: ''
  },
  selectedRoomId: {
    type: String,
    default: ''
  },
  sceneSpec: {
    type: Object,
    default: null
  }
});

const emit = defineEmits(['state-change', 'room-select']);

const session = ref(null);
const events = ref([]);
const commandText = ref('');
const selectedRoomId = ref('');
const isCreating = ref(false);
const isSending = ref(false);
const isRefreshing = ref(false);
const errorMessage = ref('');
let requestEpoch = 0;
const activeControllers = new Set();

const worldRooms = computed(() => session.value?.world_state?.rooms || []);
const deviceRows = computed(() => session.value?.world_state?.devices || []);

watch(
  () => props.sceneSpec?.scene_id,
  () => {
    resetStateForSceneSwitch();
  }
);

watch(
  () => props.selectedRoomId,
  (nextRoomId) => {
    const normalized = nextRoomId || '';
    if (normalized !== selectedRoomId.value) {
      selectedRoomId.value = normalized;
    }
  }
);

watch(selectedRoomId, (next) => {
  emit('room-select', next || '');
});

function buildApiPath(path) {
  return `${props.apiBase}${path}`;
}

async function createSession() {
  if (!props.sceneSpec?.scene_id) {
    return;
  }
  abortActiveRequests();
  const scope = createRequestScope();
  isCreating.value = true;
  errorMessage.value = '';
  try {
    const response = await fetch(buildApiPath('/api/simulations:sessions'), {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ scene_id: props.sceneSpec.scene_id }),
      signal: scope.controller.signal
    });
    const payload = await response.json();
    if (isStale(scope.epoch)) {
      return;
    }
    if (!response.ok) {
      throw new Error(payload.detail || '创建仿真会话失败。');
    }
    session.value = payload;
    emitStateChange();
    if (!selectedRoomId.value) {
      selectedRoomId.value = payload.world_state?.rooms?.[0]?.room_id || '';
    }
    await refreshEventsSafely(scope, '会话已创建，事件流刷新失败。');
  } catch (error) {
    if (isStale(scope.epoch) || isAbortError(error)) {
      return;
    }
    errorMessage.value = error instanceof Error ? error.message : '创建仿真会话失败。';
  } finally {
    finishRequestScope(scope);
    isCreating.value = false;
  }
}

async function sendCommand() {
  if (!session.value || !commandText.value) {
    return;
  }
  abortActiveRequests();
  const scope = createRequestScope();
  isSending.value = true;
  errorMessage.value = '';
  try {
    const response = await fetch(
      buildApiPath(`/api/simulations/sessions/${session.value.session_id}/commands`),
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ command: commandText.value }),
        signal: scope.controller.signal
      }
    );
    const payload = await response.json();
    if (isStale(scope.epoch)) {
      return;
    }
    if (!response.ok) {
      throw new Error(payload.detail || '执行命令失败。');
    }
    session.value = payload.session;
    emitStateChange();
    await refreshEventsSafely(scope, '命令已执行，事件流刷新失败。');
  } catch (error) {
    if (isStale(scope.epoch) || isAbortError(error)) {
      return;
    }
    errorMessage.value = error instanceof Error ? error.message : '执行命令失败。';
  } finally {
    finishRequestScope(scope);
    isSending.value = false;
  }
}

async function refreshSession() {
  if (!session.value) {
    return;
  }
  abortActiveRequests();
  const scope = createRequestScope();
  isRefreshing.value = true;
  errorMessage.value = '';
  try {
    const response = await fetch(
      buildApiPath(`/api/simulations/sessions/${session.value.session_id}`),
      { signal: scope.controller.signal }
    );
    const payload = await response.json();
    if (isStale(scope.epoch)) {
      return;
    }
    if (!response.ok) {
      throw new Error(payload.detail || '刷新会话状态失败。');
    }
    session.value = payload;
    emitStateChange();
    await refreshEventsSafely(scope, '状态已刷新，事件流刷新失败。');
  } catch (error) {
    if (isStale(scope.epoch) || isAbortError(error)) {
      return;
    }
    errorMessage.value = error instanceof Error ? error.message : '刷新会话状态失败。';
  } finally {
    finishRequestScope(scope);
    isRefreshing.value = false;
  }
}

function sendQuickCommand(mode) {
  const room = worldRooms.value.find((item) => item.room_id === selectedRoomId.value);
  if (!room) {
    return;
  }
  commandText.value = mode === 'on' ? `打开${room.room_name}的灯` : `关闭${room.room_name}的灯`;
  void sendCommand();
}

async function fetchEvents(scope) {
  if (!session.value) {
    events.value = [];
    emitStateChange();
    return;
  }
  const response = await fetch(
    buildApiPath(`/api/simulations/sessions/${session.value.session_id}/events?cursor=0&limit=100`),
    { signal: scope?.controller?.signal }
  );
  const payload = await response.json();
  if (scope && isStale(scope.epoch)) {
    return;
  }
  if (!response.ok) {
    throw new Error(payload.detail || '读取事件流失败。');
  }
  events.value = payload.events || [];
  emitStateChange();
}

async function refreshEventsSafely(scope, message) {
  try {
    await fetchEvents(scope);
  } catch (error) {
    if (isStale(scope.epoch) || isAbortError(error)) {
      return;
    }
    errorMessage.value = message;
  }
}

function emitStateChange() {
  emit('state-change', {
    session: session.value,
    events: events.value
  });
}

function resetStateForSceneSwitch() {
  requestEpoch += 1;
  abortActiveRequests();
  session.value = null;
  events.value = [];
  commandText.value = '';
  selectedRoomId.value = '';
  errorMessage.value = '';
  emitStateChange();
}

function createRequestScope() {
  const controller = new AbortController();
  const scope = {
    epoch: requestEpoch,
    controller
  };
  activeControllers.add(controller);
  return scope;
}

function finishRequestScope(scope) {
  activeControllers.delete(scope.controller);
}

function abortActiveRequests() {
  activeControllers.forEach((controller) => controller.abort());
  activeControllers.clear();
}

function isStale(epoch) {
  return epoch !== requestEpoch;
}

function isAbortError(error) {
  return Boolean(error && typeof error === 'object' && error.name === 'AbortError');
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
  background: #e8e4da;
  color: #626a61;
  font-size: 12px;
}

.status-chip.active {
  background: #dce7d7;
  color: #426037;
}

.panel-text,
.meta-line,
.error-text {
  margin: 0 0 10px;
  line-height: 1.6;
  color: #42514c;
}

.error-text {
  color: #8b4131;
}

.primary-btn,
button {
  padding: 10px 12px;
  border: none;
  border-radius: 12px;
  background: #314744;
  color: #f7f3ea;
  cursor: pointer;
}

button:disabled {
  opacity: 0.56;
  cursor: not-allowed;
}

.ghost-btn {
  background: rgba(49, 71, 68, 0.18);
  color: #314744;
}

.command-panel {
  margin-top: 14px;
  display: grid;
  gap: 12px;
}

.field-label {
  display: grid;
  gap: 6px;
  color: #42514c;
  font-size: 14px;
}

.field-label input,
.field-label select {
  padding: 10px 12px;
  border: 1px solid rgba(125, 142, 136, 0.45);
  border-radius: 12px;
  background: rgba(255, 255, 255, 0.9);
  color: #24322d;
}

.action-row {
  display: flex;
  gap: 8px;
}

.quick-row {
  display: grid;
  gap: 8px;
}

.state-block {
  border: 1px solid rgba(90, 105, 101, 0.14);
  border-radius: 12px;
  padding: 10px 12px;
  background: rgba(248, 246, 239, 0.76);
}

.block-title {
  margin: 0 0 8px;
  color: #34423d;
  font-weight: 700;
}

.plain-list,
.event-list {
  margin: 0;
  padding-left: 18px;
  color: #42514c;
}

.plain-list li,
.event-list li {
  margin-bottom: 6px;
}

.plain-list strong {
  margin-left: 8px;
  color: #5d5240;
}

.plain-list strong.on {
  color: #2e624d;
}

.event-kind {
  margin-right: 8px;
  color: #5e6a66;
  font-size: 12px;
}

.event-message {
  color: #3d4945;
}
</style>
