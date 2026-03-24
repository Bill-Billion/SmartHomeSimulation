# 控制平面与通信模型精简方案

> 历史说明  
> 当前状态：历史讨论稿，不作为当前实现依据。  
> 当前唯一主设计稿：`docs/floorplan-to-3d-minimum-implementation.md`

版本：v1.0
日期：2026-03-24
状态：基于多智能体评审意见的精简方案

---

## 1. 精简后的控制平面组件清单

### 1.1 保留组件

| 组件 | 层级归属 | 核心职责 |
|------|----------|----------|
| `Orchestrator` | 控制平面 | 单层编排：用户目标分解、跨域任务协调、能力发现与动态分派 |
| `AgentRegistry` | 控制平面 | agent 注册/注销、manifest 管理、能力发现、心跳监控 |
| `TaskBoard` | 控制平面 | 任务创建/认领/完成、依赖管理、CAS 乐观锁并发控制 |
| `EventBus` | 控制平面 | **独立定义** — 消息路由、主题订阅、发布分发、correlation 追踪 |
| `ProtocolManager` | 控制平面 | Phase 1 仅管理 Proposal-Approval 状态机 |
| `PolicyArbiter` | 控制平面 | 确定性规则仲裁（安全/权限/健康/设备四级） |
| `SessionManager` | 控制平面 | 仿真会话创建/销毁/隔离 |

### 1.2 删除组件

| 组件 | 删除原因 |
|------|----------|
| `DomainOrchestrator` | < 8 个 agent 的单机仿真系统无需双层编排。域内任务分发通过 Orchestrator + AgentRegistry 能力发现覆盖 |
| `Scheduler`（独立组件） | Phase 1 调度功能由 EventBus 定时事件 + SessionManager 生命周期管理覆盖，不需要独立调度器 |

### 1.3 新增独立定义：EventBus

原文架构图中 EventBus 已存在，但 7.3 节控制平面组件清单中未独立列出。EventBus 是控制平面的核心通信基础设施，必须独立定义。

```python
class EventBus:
    """
    控制平面核心通信基础设施。
    职责：
    1. 基于主题的消息发布/订阅
    2. 点对点消息投递（通过 to 字段）
    3. correlation_id 自动追踪
    4. 消息持久化到 EventLog
    """

    def __init__(self, event_log: EventLog):
        self._subscribers: dict[str, list[Callable]] = {}  # topic → handlers
        self._mailboxes: dict[str, asyncio.Queue] = {}      # agent_id → inbox
        self._event_log = event_log

    async def publish(self, msg: Message) -> None:
        """发布消息：写入 EventLog，然后路由到订阅者或指定收件箱。"""
        await self._event_log.append(msg)

        if msg.to:
            # 点对点投递
            if msg.to in self._mailboxes:
                await self._mailboxes[msg.to].put(msg)
        else:
            # 主题广播
            for handler in self._subscribers.get(msg.topic, []):
                asyncio.create_task(handler(msg))

    def subscribe(self, topic: str, handler: Callable) -> None:
        """订阅主题。"""
        self._subscribers.setdefault(topic, []).append(handler)

    def register_mailbox(self, agent_id: str) -> asyncio.Queue:
        """为 agent 注册收件箱。"""
        queue = asyncio.Queue()
        self._mailboxes[agent_id] = queue
        return queue

    async def request(
        self, msg: Message, timeout_s: float = 10.0
    ) -> Message:
        """发送 request 并等待 response（基于 correlation_id 匹配）。"""
        future: asyncio.Future = asyncio.get_event_loop().create_future()
        self._pending_requests[msg.message_id] = future
        await self.publish(msg)
        try:
            return await asyncio.wait_for(future, timeout=timeout_s)
        except asyncio.TimeoutError:
            raise RequestTimeoutError(msg.message_id)
        finally:
            self._pending_requests.pop(msg.message_id, None)
```

### 1.4 精简后组件关系图

```text
用户目标 / 场景脚本
        │
        ▼
  ┌─────────────┐
  │ Orchestrator │──── AgentRegistry（能力发现）
  └──────┬──────┘          │
         │                 │ manifest / heartbeat
         ▼                 ▼
    ┌──────────┐    ┌─────────────┐
    │ TaskBoard│    │  EventBus   │◄──── 所有 agent 消息通过此处路由
    └──────────┘    └──────┬──────┘
                           │
              ┌────────────┼────────────┐
              ▼            ▼            ▼
        领域 Agent    上下文 Agent   PolicyArbiter
      (Lighting等)   (Habit等)     (规则仲裁)
              │                         │
              └──── proposal ──────────►│
                                        │
                                        ▼
                                 ActionExecutor
                                        │
                                        ▼
                                   WorldState
```

---

## 2. 精简后的消息类型设计

### 2.1 从 7 种精简为 4 种

| 原消息类型 | 精简后 | 处理方式 |
|-----------|--------|----------|
| `event` | **保留** | 不变 |
| `request` | **保留** | 合并 response 语义 |
| `response` | **删除** | 通过 `request` + `correlation_id` + `status` 表达 |
| `proposal` | **保留** | 合并 decision 语义 |
| `decision` | **删除** | 通过 `proposal` + `correlation_id` + `status` 表达 |
| `incident` | **延后** | 延后到 Phase 3+，Phase 1 用 event + topic="incident.*" 临时替代 |
| `heartbeat` | **保留** | 不变 |

### 2.2 response 如何通过 request 消息表达

```text
# Agent A 发出请求
{
  "message_id": "msg_001",
  "kind": "request",
  "status": null,              ← 无 status = 这是一个请求
  "from": "orchestrator",
  "to": "habit_agent",
  "topic": "habit.sleep_window.query",
  "payload": { "date": "2026-03-24" }
}

# Agent B 回复响应
{
  "message_id": "msg_002",
  "correlation_id": "msg_001",  ← 关联到原始请求
  "kind": "request",            ← 仍然是 request 类型
  "status": "fulfilled",        ← status 表达这是响应
  "from": "habit_agent",
  "to": "orchestrator",
  "topic": "habit.sleep_window.query",
  "payload": { "sleep_start": "22:30", "sleep_end": "06:30" }
}
```

`request` 的 status 枚举：

| status | 语义 |
|--------|------|
| `null` | 这是一个请求（等待响应） |
| `"fulfilled"` | 请求已成功响应 |
| `"rejected"` | 请求被拒绝 |
| `"error"` | 请求处理出错 |

### 2.3 decision 如何通过 proposal 消息表达

```text
# Lighting Agent 提交提案
{
  "message_id": "msg_010",
  "kind": "proposal",
  "status": "submitted",
  "from": "lighting_agent",
  "to": "policy_arbiter",
  "topic": "proposal.lighting.set_state",
  "payload": {
    "device_id": "bedroom_light_1",
    "operation": "set_state",
    "params": { "brightness": 20, "color_temp": 2700 }
  }
}

# PolicyArbiter 返回仲裁决策
{
  "message_id": "msg_011",
  "correlation_id": "msg_010",   ← 关联到原始提案
  "kind": "proposal",            ← 仍然是 proposal 类型
  "status": "approved",          ← status 表达仲裁结果
  "from": "policy_arbiter",
  "to": "lighting_agent",
  "topic": "proposal.lighting.set_state",
  "payload": {
    "decision_reason": "符合睡眠模式约束",
    "applied_constraints": ["cst_001"]
  }
}
```

`proposal` 的 status 枚举（与 12.4 节仲裁输出完全对齐）：

| status | 语义 | Phase |
|--------|------|-------|
| `"drafted"` | 提案草稿（agent 本地） | Phase 1 |
| `"submitted"` | 已提交仲裁 | Phase 1 |
| `"approved"` | 批准执行 | Phase 1 |
| `"rejected"` | 驳回 | Phase 1 |
| `"modified"` | 批准但修改参数（对应 approved_with_modification） | Phase 1 |
| `"executed"` | 已执行 | Phase 1 |
| `"confirmed"` | 执行已确认 | Phase 1 |
| `"failed"` | 执行失败 | Phase 1 |
| `"deferred"` | 延后处理 | Phase 2+ |
| `"escalated"` | 升级到人工 | Phase 2+ |
| `"withdrawn"` | agent 主动撤回 | Phase 2+ |
| `"retry"` | 失败后重试 | Phase 2+ |

### 2.4 精简后的统一消息模型 JSON Schema

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "title": "UnifiedMessage",
  "description": "智能家居多智能体系统统一消息模型（精简版，4 种 kind）",
  "type": "object",
  "required": ["message_id", "session_id", "trace_id", "kind", "from", "topic", "payload", "timestamp", "sim_time"],
  "properties": {
    "message_id": {
      "type": "string",
      "description": "全局唯一消息 ID",
      "pattern": "^msg_[a-z0-9]+$"
    },
    "correlation_id": {
      "type": ["string", "null"],
      "default": null,
      "description": "关联 ID。response 关联 request，decision 关联 proposal"
    },
    "session_id": {
      "type": "string",
      "description": "所属仿真会话 ID"
    },
    "trace_id": {
      "type": "string",
      "description": "因果链追踪 ID，跨 agent 传播时不变"
    },
    "kind": {
      "type": "string",
      "enum": ["event", "request", "proposal", "heartbeat"],
      "description": "消息类型（4 种）"
    },
    "status": {
      "type": ["string", "null"],
      "default": null,
      "description": "消息状态。event/heartbeat 固定为 null；request 用于区分请求/响应；proposal 用于表达状态机流转"
    },
    "from": {
      "type": "string",
      "description": "发送方 agent_id"
    },
    "to": {
      "type": ["string", "null"],
      "default": null,
      "description": "接收方 agent_id。null 表示主题广播"
    },
    "topic": {
      "type": "string",
      "description": "消息主题，格式：domain.entity.action",
      "pattern": "^[a-z_]+\\.[a-z_]+\\.[a-z_]+$"
    },
    "payload": {
      "type": "object",
      "description": "消息负载，结构由 kind + topic 决定"
    },
    "timestamp": {
      "type": "number",
      "description": "消息创建的 wall clock 时间戳（Unix epoch seconds）"
    },
    "sim_time": {
      "type": "number",
      "description": "消息对应的仿真时间戳"
    },
    "ttl_ms": {
      "type": ["integer", "null"],
      "default": null,
      "description": "消息存活时间（毫秒）。null 表示不过期"
    },
    "require_ack": {
      "type": "boolean",
      "default": false,
      "description": "是否要求接收方确认"
    }
  },
  "allOf": [
    {
      "if": { "properties": { "kind": { "const": "event" } } },
      "then": { "properties": { "status": { "const": null } } }
    },
    {
      "if": { "properties": { "kind": { "const": "heartbeat" } } },
      "then": { "properties": { "status": { "const": null } } }
    },
    {
      "if": { "properties": { "kind": { "const": "request" } } },
      "then": {
        "properties": {
          "status": {
            "enum": [null, "fulfilled", "rejected", "error"]
          }
        }
      }
    },
    {
      "if": { "properties": { "kind": { "const": "proposal" } } },
      "then": {
        "properties": {
          "status": {
            "enum": [
              "drafted", "submitted", "approved", "rejected", "modified",
              "executed", "confirmed", "failed",
              "deferred", "escalated", "withdrawn", "retry"
            ]
          }
        }
      }
    }
  ]
}
```

---

## 3. 精简后的 Proposal-Approval 状态机

### 3.1 完整状态转换图

```text
                              ┌──────────┐
                              │  drafted  │
                              └────┬─────┘
                                   │ agent 提交
                                   ▼
                              ┌──────────┐
                  ┌───────────│ submitted │───────────┐
                  │           └────┬─────┘            │
                  │                │                   │
          ┌───────┼────────┬───────┼───────┬──────────┤
          │       │        │       │       │          │
          ▼       ▼        ▼       ▼       ▼          ▼
     ┌────────┐┌────────┐┌────────┐┌────────┐┌─────────┐┌─────────┐
     │approved││rejected││modified││deferred││escalated││withdrawn│
     └───┬────┘└────────┘└───┬────┘└───┬────┘└────┬────┘└─────────┘
         │                   │         │          │
         │                   │         │ 条件满足   │ 人工裁决
         │                   │         ▼          ▼
         │                   │    ┌──────────┐  approved
         │                   │    │ submitted │  或 rejected
         │                   │    └──────────┘
         ▼                   ▼
     ┌──────────────────────────┐
     │        executed          │
     └────────┬─────────────────┘
              │
         ┌────┼─────┐
         │         │
         ▼         ▼
    ┌─────────┐┌────────┐
    │confirmed││ failed  │
    └─────────┘└───┬────┘
                   │
                   ▼
               ┌───────┐
               │ retry  │──→ submitted（重新进入仲裁）
               └───────┘
```

### 3.2 状态转换表

| 当前状态 | 目标状态 | 触发条件 | 触发者 | Phase |
|---------|---------|---------|--------|-------|
| `drafted` | `submitted` | agent 完成提案并提交 | 发起 Agent | **Phase 1** |
| `submitted` | `approved` | PolicyArbiter 批准 | PolicyArbiter | **Phase 1** |
| `submitted` | `rejected` | PolicyArbiter 驳回 | PolicyArbiter | **Phase 1** |
| `submitted` | `modified` | PolicyArbiter 批准但修改参数 | PolicyArbiter | **Phase 1** |
| `submitted` | `deferred` | 当前条件不满足，延后执行 | PolicyArbiter | Phase 2+ |
| `submitted` | `escalated` | 超出自动仲裁能力，需人工 | PolicyArbiter | Phase 2+ |
| `submitted` | `withdrawn` | agent 主动撤回提案 | 发起 Agent | Phase 2+ |
| `approved` | `executed` | ActionExecutor 执行动作 | ActionExecutor | **Phase 1** |
| `modified` | `executed` | ActionExecutor 按修改后参数执行 | ActionExecutor | **Phase 1** |
| `deferred` | `submitted` | 延后条件满足，重新提交 | Orchestrator | Phase 2+ |
| `escalated` | `approved` / `rejected` | 人工裁决完成 | SessionManager | Phase 2+ |
| `executed` | `confirmed` | 执行结果验证通过 | ActionExecutor | **Phase 1** |
| `executed` | `failed` | 执行失败 | ActionExecutor | **Phase 1** |
| `failed` | `retry` | 决定重试 | 发起 Agent / Orchestrator | Phase 2+ |
| `retry` | `submitted` | 重新进入仲裁流程 | ProtocolManager | Phase 2+ |

### 3.3 Phase 1 必须实现的路径（最小闭环）

```text
drafted → submitted → approved → executed → confirmed
                    → rejected
                    → modified → executed → confirmed
                    executed → failed
```

共 4 条核心路径，覆盖正常批准、驳回、修改批准、执行失败四种场景。

### 3.4 Phase 1 状态机 Python 实现

```python
from enum import Enum
from dataclasses import dataclass, field
from typing import Optional
import time


class ProposalStatus(Enum):
    DRAFTED = "drafted"
    SUBMITTED = "submitted"
    APPROVED = "approved"
    REJECTED = "rejected"
    MODIFIED = "modified"
    EXECUTED = "executed"
    CONFIRMED = "confirmed"
    FAILED = "failed"
    # Phase 2+
    DEFERRED = "deferred"
    ESCALATED = "escalated"
    WITHDRAWN = "withdrawn"
    RETRY = "retry"


# Phase 1 合法状态转换
PHASE1_TRANSITIONS: dict[ProposalStatus, set[ProposalStatus]] = {
    ProposalStatus.DRAFTED:   {ProposalStatus.SUBMITTED},
    ProposalStatus.SUBMITTED: {ProposalStatus.APPROVED, ProposalStatus.REJECTED, ProposalStatus.MODIFIED},
    ProposalStatus.APPROVED:  {ProposalStatus.EXECUTED},
    ProposalStatus.MODIFIED:  {ProposalStatus.EXECUTED},
    ProposalStatus.EXECUTED:  {ProposalStatus.CONFIRMED, ProposalStatus.FAILED},
    ProposalStatus.REJECTED:  set(),   # 终态
    ProposalStatus.CONFIRMED: set(),   # 终态
    ProposalStatus.FAILED:    set(),   # Phase 1 终态（Phase 2+ 可转 retry）
}


@dataclass
class Proposal:
    proposal_id: str
    agent_id: str
    session_id: str
    trace_id: str
    status: ProposalStatus = ProposalStatus.DRAFTED
    payload: dict = field(default_factory=dict)
    modified_payload: Optional[dict] = None  # modified 时的修改后参数
    decision_reason: str = ""
    history: list[dict] = field(default_factory=list)

    def transition(self, new_status: ProposalStatus, reason: str = "", modified_payload: dict | None = None) -> None:
        allowed = PHASE1_TRANSITIONS.get(self.status, set())
        if new_status not in allowed:
            raise InvalidTransitionError(
                f"非法状态转换: {self.status.value} → {new_status.value}"
            )
        self.history.append({
            "from": self.status.value,
            "to": new_status.value,
            "reason": reason,
            "timestamp": time.time(),
        })
        self.status = new_status
        self.decision_reason = reason
        if modified_payload is not None:
            self.modified_payload = modified_payload

    @property
    def is_terminal(self) -> bool:
        return self.status in {
            ProposalStatus.REJECTED,
            ProposalStatus.CONFIRMED,
            ProposalStatus.FAILED,
        }

    @property
    def effective_payload(self) -> dict:
        """返回实际要执行的参数（modified 时用修改后的）。"""
        if self.modified_payload is not None and self.status in {
            ProposalStatus.MODIFIED, ProposalStatus.EXECUTED, ProposalStatus.CONFIRMED
        }:
            return self.modified_payload
        return self.payload


class InvalidTransitionError(Exception):
    pass
```

---

## 4. Phase 1 的 Request-Response 简化方案

### 4.1 设计原则

Phase 1 不为 Request-Response 引入正式状态机。原因：

- Request-Response 是无状态的一次性交互，不需要持久化状态跟踪
- `asyncio.Future` + timeout 已足够满足 Phase 1 的需求
- 如果 Phase 2+ 需要更复杂的模式（如 ack、多轮协商），再升级为状态机

### 4.2 核心代码实现

```python
import asyncio
import uuid
import time
from dataclasses import dataclass


@dataclass
class Message:
    message_id: str
    correlation_id: str | None
    session_id: str
    trace_id: str
    kind: str          # "event" | "request" | "proposal" | "heartbeat"
    status: str | None  # request: None/"fulfilled"/"rejected"/"error"
    from_agent: str
    to: str | None
    topic: str
    payload: dict
    timestamp: float
    sim_time: float
    ttl_ms: int | None = None
    require_ack: bool = False


class RequestResponseManager:
    """
    Phase 1 轻量级 Request-Response 管理器。
    不使用状态机，基于 asyncio.Future + timeout 实现。
    """

    def __init__(self, event_bus: "EventBus"):
        self._bus = event_bus
        self._pending: dict[str, asyncio.Future[Message]] = {}
        # 注册全局响应处理器
        self._bus.on_response(self._handle_incoming_response)

    async def send_request(
        self,
        from_agent: str,
        to: str,
        topic: str,
        payload: dict,
        *,
        session_id: str,
        trace_id: str,
        sim_time: float,
        timeout_s: float = 10.0,
    ) -> Message:
        """
        发送请求并等待响应。超时自动清理。
        返回响应 Message（status 为 fulfilled/rejected/error）。
        """
        msg = Message(
            message_id=f"msg_{uuid.uuid4().hex[:12]}",
            correlation_id=None,
            session_id=session_id,
            trace_id=trace_id,
            kind="request",
            status=None,  # None = 这是一个请求
            from_agent=from_agent,
            to=to,
            topic=topic,
            payload=payload,
            timestamp=time.time(),
            sim_time=sim_time,
        )

        loop = asyncio.get_running_loop()
        future: asyncio.Future[Message] = loop.create_future()
        self._pending[msg.message_id] = future

        await self._bus.publish(msg)

        try:
            response = await asyncio.wait_for(future, timeout=timeout_s)
            return response
        except asyncio.TimeoutError:
            # 超时：构造一个 error 响应返回给调用者
            return Message(
                message_id=f"msg_{uuid.uuid4().hex[:12]}",
                correlation_id=msg.message_id,
                session_id=session_id,
                trace_id=trace_id,
                kind="request",
                status="error",
                from_agent="system",
                to=from_agent,
                topic=topic,
                payload={"error": "timeout", "timeout_s": timeout_s},
                timestamp=time.time(),
                sim_time=sim_time,
            )
        finally:
            self._pending.pop(msg.message_id, None)

    def reply(
        self,
        original: Message,
        from_agent: str,
        status: str,
        payload: dict,
        sim_time: float,
    ) -> Message:
        """
        构造响应消息。被请求方调用后 publish 到 EventBus。
        """
        return Message(
            message_id=f"msg_{uuid.uuid4().hex[:12]}",
            correlation_id=original.message_id,
            session_id=original.session_id,
            trace_id=original.trace_id,
            kind="request",
            status=status,  # "fulfilled" | "rejected" | "error"
            from_agent=from_agent,
            to=original.from_agent,
            topic=original.topic,
            payload=payload,
            timestamp=time.time(),
            sim_time=sim_time,
        )

    async def _handle_incoming_response(self, msg: Message) -> None:
        """EventBus 回调：收到带 status 的 request 消息时匹配 pending Future。"""
        if (
            msg.kind == "request"
            and msg.status is not None
            and msg.correlation_id in self._pending
        ):
            future = self._pending[msg.correlation_id]
            if not future.done():
                future.set_result(msg)
```

### 4.3 使用示例

```python
# Orchestrator 向 HabitAgent 查询作息预测
response = await req_mgr.send_request(
    from_agent="orchestrator",
    to="habit_agent",
    topic="habit.sleep_window.query",
    payload={"date": "2026-03-24"},
    session_id=session.id,
    trace_id=current_trace_id,
    sim_time=sim_clock.now(),
    timeout_s=5.0,
)

if response.status == "fulfilled":
    sleep_window = response.payload
elif response.status == "error":
    # 超时或处理出错，走降级路径
    sleep_window = DEFAULT_SLEEP_WINDOW
```

---

## 5. 单层 Orchestrator 完整职责定义

### 5.1 Orchestrator 负责

| 职责 | 说明 |
|------|------|
| 用户目标接收与分解 | 将高层目标（如"启动睡眠模式"）分解为若干跨域子任务 |
| 能力发现与动态分派 | 通过 AgentRegistry 查询能力匹配的 agent，动态选择执行者 |
| 任务创建与发布 | 将分解后的任务写入 TaskBoard，设置依赖关系 |
| 跨域协调 | 需要多 agent 联动时，协调执行顺序和数据传递 |
| 提案汇聚 | 将多个 agent 的提案汇聚后统一提交给 PolicyArbiter |
| 任务状态监听 | 监听 TaskBoard 状态变化，驱动后续任务和流程推进 |
| 上下文收集 | 在协调过程中向上下文推理型 agent 收集约束和建议 |

### 5.2 Orchestrator 不负责

| 不负责 | 由谁负责 |
|--------|---------|
| 设备状态写入 | ActionExecutor |
| 设备动作执行 | ActionExecutor |
| 仿真时钟维护 | EventScheduler |
| 策略仲裁决策 | PolicyArbiter |
| agent 生命周期管理 | AgentRegistry |
| 会话生命周期管理 | SessionManager |
| 消息路由与分发 | EventBus |
| 域内具体策略推理 | 各领域 Agent 自行负责 |

### 5.3 通过 AgentRegistry 能力发现实现动态任务分派

```python
class Orchestrator:
    """单层编排器。通过能力发现动态分派，不硬编码 agent 列表。"""

    def __init__(
        self,
        registry: AgentRegistry,
        task_board: TaskBoard,
        event_bus: EventBus,
        req_mgr: RequestResponseManager,
    ):
        self._registry = registry
        self._task_board = task_board
        self._bus = event_bus
        self._req_mgr = req_mgr

    async def handle_user_goal(self, goal: str, session_id: str, trace_id: str) -> list[str]:
        """
        处理用户目标：分解 → 查询能力 → 分派任务。
        返回创建的任务 ID 列表。
        """
        # 1. 目标分解（LLM 推理）
        subtasks = await self._decompose_goal(goal, session_id, trace_id)

        task_ids = []
        for subtask in subtasks:
            # 2. 能力发现：查询哪些 agent 能处理这个子任务
            candidates = await self._registry.find_by_capabilities(
                required=subtask.required_capabilities,
                status="online",
            )

            if not candidates:
                # 无可用 agent，创建 unassigned 任务等待认领
                task_id = await self._task_board.create(
                    subject=subtask.subject,
                    required_capabilities=subtask.required_capabilities,
                    session_id=session_id,
                    trace_id=trace_id,
                )
            else:
                # 3. 选择最佳 agent（按负载、优先级）
                agent_id = self._select_best(candidates)

                # 4. 创建并分配任务
                task_id = await self._task_board.create_and_assign(
                    subject=subtask.subject,
                    owner=agent_id,
                    session_id=session_id,
                    trace_id=trace_id,
                    blocked_by=subtask.dependencies,
                )

            task_ids.append(task_id)

        return task_ids

    def _select_best(self, candidates: list[AgentInfo]) -> str:
        """选择最佳 agent：优先选择负载最低的在线 agent。"""
        online = [c for c in candidates if c.status == "online"]
        if not online:
            return candidates[0].agent_id
        # 按当前持有任务数升序排序
        online.sort(key=lambda c: c.active_task_count)
        return online[0].agent_id

    async def collect_constraints(
        self, domains: list[str], session_id: str, trace_id: str, sim_time: float
    ) -> list[dict]:
        """
        并行向上下文推理型 agent 收集约束。
        通过能力发现找到 constraint_provider，不硬编码 agent 名称。
        """
        providers = await self._registry.find_by_capabilities(
            required=["constraint_provider"],
            status="online",
        )

        # 并行发送 request
        tasks = [
            self._req_mgr.send_request(
                from_agent="orchestrator",
                to=p.agent_id,
                topic=f"constraint.query",
                payload={"domains": domains},
                session_id=session_id,
                trace_id=trace_id,
                sim_time=sim_time,
                timeout_s=5.0,
            )
            for p in providers
        ]

        responses = await asyncio.gather(*tasks, return_exceptions=True)

        constraints = []
        for resp in responses:
            if isinstance(resp, Message) and resp.status == "fulfilled":
                constraints.extend(resp.payload.get("constraints", []))

        return constraints
```

### 5.4 AgentRegistry 能力发现接口

```python
@dataclass
class AgentInfo:
    agent_id: str
    role: str                          # "orchestrator" | "executor" | "context_reasoner"
    capabilities: list[str]            # ["lighting_control", "brightness_optimization"]
    publishes: list[str]               # 可发布的主题
    subscribes: list[str]              # 订阅的主题
    request_handlers: list[str]        # 可处理的 request 类型
    proposal_scope: str                # "binding" | "advisory" | "optional"
    status: str = "online"             # "online" | "degraded" | "offline"
    active_task_count: int = 0
    last_heartbeat: float = 0.0


class AgentRegistry:
    """agent 注册中心。提供注册、注销、能力查询、心跳监控。"""

    def __init__(self):
        self._agents: dict[str, AgentInfo] = {}

    async def register(self, info: AgentInfo) -> None:
        self._agents[info.agent_id] = info

    async def unregister(self, agent_id: str) -> None:
        self._agents.pop(agent_id, None)

    async def find_by_capabilities(
        self,
        required: list[str],
        status: str | None = None,
    ) -> list[AgentInfo]:
        """查找具备所有指定能力的 agent。"""
        results = []
        for agent in self._agents.values():
            if status and agent.status != status:
                continue
            if all(cap in agent.capabilities for cap in required):
                results.append(agent)
        return results

    async def heartbeat(self, agent_id: str, timestamp: float) -> None:
        if agent_id in self._agents:
            self._agents[agent_id].last_heartbeat = timestamp
            if self._agents[agent_id].status == "degraded":
                self._agents[agent_id].status = "online"
```

---

## 6. 精简前后对比总结

| 维度 | 精简前 | 精简后 | 变化 |
|------|--------|--------|------|
| Orchestrator 层级 | 2 层（Global + Domain） | 1 层 | 删除 DomainOrchestrator |
| 消息类型 | 7 种 | 4 种 | response/decision 合并，incident 延后 |
| 协议状态机 | 3 套 | 1 套 + Future | Phase 1 只需 Proposal-Approval |
| Proposal 状态数 | 6 个 | 12 个（Phase 1: 8 个） | 与 12.4 节仲裁输出完全对齐 |
| EventBus | 隐式存在 | 独立定义 | 补全缺失组件 |
| 控制平面组件 | 7 个 + DomainOrchestrator | 7 个（含 EventBus） | 总数不变，结构更清晰 |

### 关键设计决策

1. **status 字段复用 kind**：response 不是独立消息类型，而是带 `status="fulfilled"` 的 request 消息。这减少了消息类型数量，同时通过 `correlation_id` 保持关联语义。

2. **Proposal 状态机是唯一正式状态机**：只有设备动作提案需要持久化的状态跟踪和仲裁流程。Request-Response 是瞬态交互，用 Future 足够。

3. **单层 Orchestrator + 能力发现**：不通过双层编排解决域内分发，而是通过 AgentRegistry 能力查询实现动态路由。Orchestrator 不硬编码知道任何域的存在。

4. **Phase 分层实现**：12 个 proposal 状态中 Phase 1 只实现 8 个核心状态，4 个高级状态（deferred/escalated/withdrawn/retry）延后到 Phase 2+，避免过早引入复杂度。
