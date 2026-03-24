# 精简方案验证报告

> 历史说明  
> 当前状态：历史讨论稿，不作为当前实现依据。  
> 当前唯一主设计稿：`docs/floorplan-to-3d-minimum-implementation.md`

版本：v1.0
日期：2026-03-24
验证者：资深架构验证专家
状态：最终验证报告

---

## 一、验证总览表

| 维度 | 判定 | 关键发现 |
|------|------|----------|
| 1. 核心目标保持性 | **通过** | 4 个核心目标均完整保留，回放能力从"声称"升级为具体技术方案 |
| 2. 技术选型兼容性 | **有条件通过** | SimPy（同步）与 asyncio 共存方案未定义；requirements.txt 缺少 aiosqlite；pyee 错误边界未设计 |
| 3. 组件完整性 | **有条件通过** | WorldState 实体模型（House/Room/Sensor/Occupant）在精简方案中被弱化；ScenarioRunner 无接口定义 |
| 4. 精简一致性 | **不通过** | 三份文档的消息 kind 枚举不一致（4 种 vs 6 种 vs 7 种）；Proposal 状态机定义不一致；EventBus 有两套不同实现 |
| 5. 实现风险 | **有条件通过** | 单层 Orchestrator 风险可控；Incident 协议从 Phase 1 临时方案到 Phase 2+ 正式方案的迁移路径未定义 |
| 6. MVP 可执行性 | **有条件通过** | 关键路径清晰；WorldState 实体模型和 LLM prompt 模板需前置定义；2-3 周工期对单人开发偏紧 |

---

## 二、逐维度详细分析

### 维度 1：核心目标保持性

**判定：通过**

逐一核对原文档声明的 4 个核心目标：

**目标 1：多个智能体长期协作，而非一次性对话式调用**

- AgentCore 统一循环（perceive → think → act → observe → compress → idle_claim）完整保留
- TaskBoard 持久任务管理保留，CAS 乐观锁认领机制保留
- EventBus 独立定义并增强（补齐了原文遗漏的显式组件定义）
- 四梯队渐进接入计划提供了清晰的多 Agent 协作演进路径
- **结论：目标完整保留**

**目标 2：新增智能体不耦合修改现有智能体**

- Manifest 注册制完整保留
- AgentRegistry 能力发现接口明确定义（`find_by_capabilities`）
- 单层 Orchestrator 通过能力发现动态分派，不硬编码 Agent 列表
- 控制平面精简文档 5.3 节明确展示了动态路由代码
- **结论：目标完整保留，且通过能力发现机制进一步强化**

**目标 3：语言决策与真实状态解耦**

- SimPy 替代自建仿真层，WorldState 独立于 Agent 对话
- ActionExecutor 保持为唯一状态写入点
- WorldState 写入 ACL（WriteToken + ContextVar）将"架构纪律"升级为"技术强制"
- PolicyArbiter 确定性规则引擎（P1-P4 不引入 LLM）保留
- **结论：目标完整保留，且安全层从纪律约束升级为技术屏障**

**目标 4：可解释、可审计、可回放、可评测**

- trace_id 贯穿设计保留，且修补了 4 个传播盲区（P2P 通信、心跳异常、自治认领、定时触发）
- Record/Replay Proxy 提供了具体的 LLM 录制/回放机制
- 双模式回放（状态回放 + 全真回放）将原文"声称可回放"落实为可实现的技术方案
- MetricsCollector 接口定义覆盖原文 20.3 节全部 10 个核心指标
- **结论：目标完整保留并显著增强**

---

### 维度 2：技术选型兼容性

**判定：有条件通过**

#### 2.1 SimPy + asyncio — 有显著兼容性缺口

**问题**：SimPy 4.x 是纯同步库，其核心 API（`env.run()`、`env.step()`、`env.process()`）使用 Python 生成器（`yield`），而非 `async/await` 协程。FastAPI 运行在 asyncio 事件循环上。

Agent 梯队文档 3.2.1 节展示的 `SimulationWorld` 类使用同步调用：

```python
def step(self, until: float | None = None) -> None:
    if until is not None:
        self.env.run(until=until)  # 阻塞调用！
    else:
        self.env.step()            # 阻塞调用！
```

在 FastAPI 的异步上下文中直接调用这些方法会阻塞事件循环。

**修正建议**：
```python
import asyncio

async def step_async(self, until: float | None = None) -> None:
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, self.step, until)
```

或者使用独立线程运行 SimPy 环境，通过 `asyncio.Queue` 与 async 世界通信。需在架构中明确 SimPy 线程模型。

**严重程度**：中 — 不阻塞 MVP 开发（MVP 可用单线程串行执行），但 Phase 1 引入 WebSocket 推送后必须解决。

#### 2.2 transitions + Pydantic v2 — 兼容

Agent 梯队文档的实现方案将 `transitions.Machine` 挂载在 `ProposalStateMachine` 包装类上（`model=self`），而非直接挂载在 Pydantic `Proposal` 模型上，通过 `on_state_change` 回调同步状态。这避免了 transitions 直接设置 Pydantic 模型属性的验证冲突。

**结论**：方案可行，无兼容性问题。

#### 2.3 LiteLLM + instructor — 兼容

`instructor.from_litellm(acompletion)` 是 instructor 库的标准用法。instructor >= 1.3 对 LiteLLM 后端有明确支持。

**注意**：requirements.txt 版本约束 `instructor>=1.3` 过于宽松。instructor 1.x 到 2.x 有 breaking changes。建议锁定为 `instructor>=1.3,<2.0`。

#### 2.4 pyee + asyncio — 基本兼容，缺少错误边界

`pyee.asyncio.AsyncIOEventEmitter` 是稳定的 asyncio 事件发射器。但文档中的 EventBus 实现未处理 handler 异常：

```python
# 当前代码（agent-tiering 3.2.5）
self._emitter.emit(topic, message)  # handler 异常会传播到哪里？
```

pyee 的默认行为是将未捕获异常通过 `error` 事件传播。如果没有 `error` 事件监听器，异常会被静默吞掉。

**修正建议**：EventBus 必须注册全局 error handler，将异常写入 EventLog 并通知 SessionManager。

#### 2.5 SQLAlchemy async + aiosqlite — 依赖缺失

requirements.txt 中只列了 `sqlalchemy>=2.0`，但未包含 `aiosqlite`。Agent 梯队文档 3.2.4 节的代码使用同步 SQLAlchemy API（`create_engine`、`Session`、`db.execute`），但在 async FastAPI 上下文中，这些同步调用同样会阻塞事件循环。

**两种修正方案**：

方案 A（推荐 MVP）：继续使用同步 SQLAlchemy，所有数据库操作通过 `run_in_executor` 调用：
```
# 无需额外依赖
sqlalchemy>=2.0
```

方案 B（推荐 Phase 1+）：迁移到异步 SQLAlchemy：
```
sqlalchemy[asyncio]>=2.0
aiosqlite>=0.20
```

---

### 维度 3：组件完整性

**判定：有条件通过**

#### 3.1 覆盖度分析

| 阶段 | 所需组件 | 精简方案覆盖 | 状态 |
|------|---------|-------------|------|
| MVP | AgentCore | 定义完整（循环规范 + RecordReplayProxy 集成） | 完整 |
| MVP | AgentRegistry | 接口 + 实现定义完整 | 完整 |
| MVP | TaskBoard | 持久层（SQLAlchemy）+ CAS 乐观锁完整 | 完整 |
| MVP | SessionManager | 提及但无接口定义 | **缺接口** |
| MVP | EventBus | 有两套实现（见维度 4） | 需统一 |
| MVP | EventLog | structlog + JSONL 方案明确 | 完整 |
| MVP | ActionExecutor | 占位实现 + WriteToken 集成 | 完整 |
| MVP | Orchestrator | 完整职责定义 + 代码示例 | 完整 |
| MVP | LightingAgent | manifest 定义 + 验收场景 | 完整 |
| Phase 1 | ProtocolManager | transitions 实现方案完整 | 完整 |
| Phase 1 | PolicyArbiter | 确定性规则引擎 + RuleEngine 接口 | 完整 |
| Phase 1 | ContextManager | 压缩策略 + tiktoken 方案明确 | 完整 |
| Phase 1 | ToolRegistry | 安全分级 + 权限控制 | 完整 |
| Phase 1 | WebSocket 推送 | 仅提及，无集成设计 | **缺集成** |
| Phase 2 | ConstraintStore | 完整接口定义 | 完整 |
| Phase 2 | MetricsCollector | 完整接口 + 10 个指标 | 完整 |
| Phase 2 | Incident 协议 | transitions 状态机定义 | 完整 |
| Phase 3 | UserContext Agent | manifest + 内部模块化设计 | 完整 |
| Phase 3 | FaultDiagnosis Agent | 职责描述，无 manifest | **缺 manifest** |

#### 3.2 关键缺失

**缺失 1：WorldState 实体模型弱化**

原文档 14.2 节定义了 11 个核心实体：House, Room, Device, Sensor, Occupant, Scenario, Constraint, ActionProposal, PolicyDecision, SimulationEvent, StateSnapshot。

精简方案中 SimPy `SimulationWorld` 只展示了 `devices: dict[str, DeviceState]`，缺少：
- `Room` 模型（设备归属、空间关系）
- `Sensor` 模型（传感器读数、报警阈值）
- `Occupant` 模型（住户状态、位置）
- `House` 模型（电路容量、安防模式）

这些实体在 AtomicArbiter 的组合安全检查中被隐式使用（如 `ws.get_device(p.device_id).room_id`、`ws.get_circuit_capacity()`），但未定义数据模型。

**缺失 2：ScenarioRunner 无接口**

组件清单中列出了 ScenarioRunner，但三份精简文档均未给出其接口定义或场景脚本格式。MVP 可以暂缓，但 Phase 2 场景测试时需要。

**缺失 3：SessionManager 无接口**

SessionManager 在多处被引用（创建 session、指定 SimulationMode、签发 WriteToken），但无独立接口定义。

#### 3.3 依赖关系

无循环依赖。依赖方向清晰：

```
AgentCore → EventBus → EventLog
AgentCore → ToolRegistry
AgentCore → ContextManager → tiktoken
AgentCore → RecordReplayProxy → LLMCacheStore → SQLite
Orchestrator → AgentRegistry, TaskBoard, EventBus, RequestResponseManager
PolicyArbiter → RuleEngine, ConstraintStore, WorldState
ActionExecutor → WorldState (WriteToken)
AtomicArbiter → PolicyArbiter, WorldState
```

---

### 维度 4：精简一致性

**判定：不通过**

发现以下跨文档矛盾：

#### 4.1 消息类型（kind）枚举不一致 — 严重

| 文档 | kind 枚举 | 数量 |
|------|----------|------|
| 控制平面精简（2.1 节） | event, request, proposal, heartbeat | **4 种** |
| Agent 梯队（3.2.5 节 EventMessage） | event, request, response, proposal, decision, incident | **6 种** |
| 回放安全（6.1 节 Message） | event, request, response, proposal, decision, incident, heartbeat | **7 种** |

控制平面文档明确将 7 种精简为 4 种（response 合并入 request，decision 合并入 proposal，incident 延后），但另外两份文档仍使用原始的 6-7 种枚举。

**影响**：开发团队无法确定统一消息模型的最终形态。如果按控制平面文档的 4 种实现，Agent 梯队和回放安全文档中的代码需要大面积修改。

**修正方案**：以控制平面精简文档的 4 种为准（该文档给出了最详细的 JSON Schema 和迁移方案），统一修改 Agent 梯队文档 3.2.5 节和回放安全文档 6.1 节的 Message 模型。

#### 4.2 Proposal 状态机定义不一致 — 中等

| 文档 | Proposal 状态枚举 |
|------|-------------------|
| 控制平面精简（3.4 节） | drafted, submitted, approved, rejected, **modified**, executed, confirmed, failed (Phase 1: 8 个) |
| Agent 梯队（3.2.2 节） | drafted, submitted, approved, rejected, executed, confirmed, failed (7 个，**缺少 modified**) |

控制平面文档定义了 `modified` 状态（对应 `approved_with_modification`），这是 Phase 1 的核心状态。但 Agent 梯队文档的 transitions 实现中完全遗漏了 `modified` 状态和对应的转换规则。

**修正方案**：在 Agent 梯队文档 3.2.2 节的 `ProposalState` 和 `ProposalStateMachine.TRANSITIONS` 中补充：
```python
MODIFIED = "modified"
# 补充转换
{"trigger": "modify", "source": "submitted", "dest": "modified"},
{"trigger": "execute", "source": "modified", "dest": "executed"},
```

#### 4.3 EventBus 实现不一致 — 中等

三份文档给出了三种不同的 EventBus 设计：

| 文档 | 实现方式 | 核心差异 |
|------|---------|---------|
| 控制平面（1.3 节） | 原生 asyncio.Queue + dict[str, list[Callable]] | Mailbox 用 asyncio.Queue，有 `request()` 方法 |
| Agent 梯队（3.2.5 节） | pyee.AsyncIOEventEmitter | Mailbox 通过 topic 前缀 `mailbox.{agent_id}` 模拟 |
| Agent 梯队（4.1 节） | ABC 接口 IEventBus | 仅定义接口，无实现 |

**修正方案**：
- 以 Agent 梯队 4.1 节的 `IEventBus` 接口为准（最完整的契约定义）
- 实现层采用 pyee 方案（Agent 梯队 3.2.5 节），补充控制平面 1.3 节的 `request()` 方法
- 删除控制平面 1.3 节的独立实现，改为引用 IEventBus 接口

#### 4.4 组件层级归属不一致 — 轻微

| 组件 | 控制平面文档 | Agent 梯队文档 |
|------|------------|--------------|
| EventBus | 控制平面 | 运行时层（跨层使用） |
| Scheduler | 已删除 | 控制平面（APScheduler 复用） |

控制平面文档删除了独立 Scheduler，但 Agent 梯队文档的完整组件清单（第 5 节）中又列出了 `Scheduler [APScheduler 复用]`。

**修正方案**：统一采用控制平面文档的决策——删除独立 Scheduler，调度功能由 EventBus 定时事件 + SessionManager 覆盖。如果确需 cron 调度，在 Phase 2+ 按需引入 APScheduler。

---

### 维度 5：实现风险

**判定：有条件通过**

#### 5.1 单层 Orchestrator 负载上限 — 低风险

当前设计 < 8 个 Agent 的单机仿真，单层 Orchestrator 的负载包括：
- 目标分解（1 次 LLM 调用/目标）
- 能力发现（内存查表，< 1ms）
- 任务创建（SQLite 写入，< 10ms）
- 提案汇聚（EventBus 订阅，异步）

瓶颈在于 LLM 调用延迟（1-5 秒），而非 Orchestrator 本身。在 Agent 数量 < 20 的范围内，单层 Orchestrator 不会成为瓶颈。

**缓解措施**（已有）：Agent 梯队文档保留了"如果未来某个模块复杂度激增，可以再拆分为独立 Agent"的渐进式拆分路径。

#### 5.2 消息类型合并后的语义模糊 — 低风险

使用 `request` + `status=null` 表示请求、`status="fulfilled"` 表示响应，虽然增加认知负担，但控制平面文档 2.2-2.3 节给出了清晰的示例和枚举定义。只要团队遵循规范，语义模糊风险可控。

**建议**：在代码中定义辅助函数：
```python
def is_request(msg: Message) -> bool: return msg.kind == "request" and msg.status is None
def is_response(msg: Message) -> bool: return msg.kind == "request" and msg.status is not None
```

#### 5.3 延后组件导致后期重构 — 中风险

| 延后组件 | 当前替代方案 | 迁移到正式方案的重构范围 |
|---------|------------|----------------------|
| Incident 协议 | event + topic="incident.*" | **中等**：需引入 IncidentStateMachine，修改事件处理链路 |
| ReplayEngine | EventLog 积累数据 | **低**：StateReplayEngine 和 FullReplayEngine 的设计已完成 |
| deferred/escalated/withdrawn/retry 状态 | Phase 1 不实现 | **低**：只需在 PHASE1_TRANSITIONS 中添加新转换 |

**关键风险点**：Incident 协议的迁移。Phase 1 用 `event + topic="incident.*"` 临时替代，但到 Phase 2 引入正式 Incident 协议时，需要：
1. 引入 IncidentStateMachine（transitions 实现已就绪）
2. 修改 EventBus 中 incident 事件的处理逻辑
3. 迁移历史 incident 事件数据格式

**修正建议**：在 Phase 1 的 incident 事件 payload 中预留 `incident_status` 字段（默认 "detected"），为 Phase 2 迁移降低成本。

#### 5.4 Record/Replay Proxy 性能影响 — 低风险

- LIVE 模式：SHA-256 哈希 + SQLite INSERT，额外延迟 < 5ms（LLM 调用本身 1-5 秒）
- REPLAY 模式：SQLite SELECT（主键或索引查询），延迟 < 1ms
- 存储开销：每次 LLM 调用约 2-10KB（请求+响应），1000 次调用约 2-10MB

性能影响完全可忽略。

#### 5.5 AtomicArbiter 仲裁窗口延迟 — 低风险

提案在窗口期内缓冲，延迟 = 1 个 tick 周期。关键在于 tick 周期的配置。仿真系统中 tick 周期是可控的（不受实时时钟约束），因此延迟不构成实际风险。

---

### 维度 6：MVP 可执行性

**判定：有条件通过**

#### 6.1 关键路径分析

```
Week 1: 基础层
├── Day 1-2: 数据模型定义（Message, Task, DeviceState, WorldState 实体模型）
├── Day 3:   EventLog（structlog + JSONL）+ EventBus（pyee 实现）
├── Day 4:   AgentRegistry（注册 + 查询 + 心跳）
└── Day 5:   TaskBoard（SQLAlchemy + CAS 乐观锁）+ SessionManager

Week 2: 运行时层
├── Day 1:   LLM Client（LiteLLM + instructor 集成）
├── Day 2:   AgentCore（最小循环）+ RecordReplayProxy（LIVE + DRYRUN）
├── Day 3:   ToolRegistry（最小实现）+ ActionExecutor（占位实现）
├── Day 4:   Orchestrator（目标分解 + 能力发现 + 任务分派）
└── Day 5:   LightingAgent（manifest + think 逻辑 + 提案生成）

Week 3: 集成与验收
├── Day 1-2: 端到端集成（用户目标 → Orchestrator → Lighting → 提案 → 执行 → EventLog）
├── Day 3:   FastAPI 最小 API（POST /sessions, POST /sessions/{id}/goals）
├── Day 4:   集成测试 + trace_id 贯穿验证
└── Day 5:   缓冲/修复
```

**并行可能性**：
- Week 1 的 EventLog/EventBus 与 AgentRegistry/TaskBoard 可并行开发（无依赖）
- Week 2 的 LLM Client 与 ToolRegistry 可并行开发
- Orchestrator 和 LightingAgent 必须串行（LightingAgent 依赖 Orchestrator 分发任务）

#### 6.2 阻塞开发的未定义接口

| 未定义项 | 阻塞的组件 | 严重程度 |
|---------|-----------|---------|
| WorldState 实体模型（Room, Sensor, Occupant） | ActionExecutor、AtomicArbiter 组合安全检查 | **高** — MVP 必须至少定义 Device + Room |
| SessionManager 接口 | AgentCore 初始化、WriteToken 签发 | **中** — MVP 可用最小实现 |
| LLM system prompt 模板 | Orchestrator、LightingAgent 的 think() | **中** — 直接影响端到端验证质量 |
| 场景脚本格式 | ScenarioRunner | **低** — MVP 不需要 |

#### 6.3 工期评估

- **单人全栈开发**：3 周紧凑但可行（前提：开发者熟悉 Python asyncio + LLM API + SQLAlchemy）
- **双人开发**：2 周可行（基础层 + 运行时层并行）
- **风险缓冲**：LLM prompt 调优可能消耗额外 2-3 天

---

## 三、关键修正项

### 必须修正（阻塞开发）

#### M1：统一消息类型枚举（阻塞所有组件开发）

**问题**：三份文档的 `kind` 枚举不一致（4 种 vs 6 种 vs 7 种）。

**修正**：
- 以控制平面精简文档的 **4 种** 为准：`event`、`request`、`proposal`、`heartbeat`
- 修改 Agent 梯队文档 3.2.5 节 `EventMessage.kind` 注释
- 修改回放安全文档 6.1 节 `Message.kind` 注释
- 所有文档中 `response` 改为 `request + status="fulfilled"`
- 所有文档中 `decision` 改为 `proposal + status="approved"/"rejected"/"modified"`
- 所有文档中 `incident` 改为 `event + topic="incident.*"`（Phase 1）

#### M2：补齐 Proposal 状态机的 modified 状态（阻塞 ProtocolManager 开发）

**问题**：Agent 梯队文档 3.2.2 节遗漏 `modified` 状态。

**修正**：在 `ProposalState` 枚举和 `ProposalStateMachine.TRANSITIONS` 中补充：
```python
MODIFIED = "modified"

# 新增转换
{"trigger": "modify", "source": "submitted", "dest": "modified"},
{"trigger": "execute", "source": "modified", "dest": "executed"},
```

#### M3：定义 WorldState 最小实体模型（阻塞 ActionExecutor 开发）

**问题**：SimPy `SimulationWorld` 只有 `devices` dict，缺少 Room 等实体。

**修正**：在 SimulationWorld 中补充最小实体模型：
```python
class RoomState(BaseModel):
    room_id: str
    room_type: str  # bedroom, living_room, kitchen, bathroom
    devices: list[str] = []  # device_id 列表

class SensorState(BaseModel):
    sensor_id: str
    sensor_type: str  # temperature, humidity, light, motion
    room_id: str
    value: float
    unit: str

class HouseState(BaseModel):
    house_id: str
    rooms: dict[str, RoomState]
    circuit_capacity_watts: float = 5000.0
    security_armed: bool = False

class SimulationWorld:
    def __init__(self):
        self.env = simpy.Environment()
        self.house: HouseState = HouseState(house_id="default", rooms={})
        self.devices: dict[str, DeviceState] = {}
        self.sensors: dict[str, SensorState] = {}
```

### 建议修正（不阻塞但影响质量）

#### R1：统一 EventBus 实现方案

**建议**：以 Agent 梯队 4.1 节 `IEventBus` 接口为契约，以 3.2.5 节 pyee 实现为标准实现，从控制平面 1.3 节补充 `request()` 方法到 pyee 实现中。删除控制平面 1.3 节的独立 asyncio.Queue 实现。

#### R2：明确 SimPy + asyncio 集成方案

**建议**：在架构文档中增加一节"SimPy 线程模型"，明确 SimPy 环境运行在独立线程中，通过 `asyncio.run_in_executor()` 桥接。

#### R3：补充 aiosqlite 或明确同步数据库策略

**建议**：MVP 阶段使用同步 SQLAlchemy + `run_in_executor`，Phase 1 迁移到 `sqlalchemy[asyncio]` + `aiosqlite`。在 requirements.txt 中添加注释说明。

#### R4：为 Phase 2 incident 迁移预留字段

**建议**：Phase 1 的 incident 事件 payload 中预留 `incident_status` 字段：
```python
await bus.publish(Message(
    kind="event",
    topic="incident.device.fault",
    payload={
        "incident_status": "detected",  # 预留，Phase 2 迁移时直接使用
        "device_id": "...",
        "fault_type": "...",
    },
))
```

#### R5：补充 pyee EventBus 错误边界

**建议**：在 EventBus 初始化时注册全局 error handler：
```python
class EventBus:
    def __init__(self, event_log: EventLog):
        self._emitter = AsyncIOEventEmitter()
        self._emitter.on("error", self._on_handler_error)

    async def _on_handler_error(self, error: Exception):
        logger.error(f"EventBus handler error: {error}", exc_info=True)
        await self._event_log.append(event_type="system.error", payload={"error": str(error)})
```

#### R6：锁定 instructor 版本范围

**建议**：将 `instructor>=1.3` 改为 `instructor>=1.3,<2.0`，避免未来 2.x breaking changes。

#### R7：统一 Scheduler 组件决策

**建议**：从 Agent 梯队文档第 5 节的组件清单中删除 `Scheduler [APScheduler 复用]`，与控制平面文档的删除决策保持一致。如果 Phase 2+ 确实需要 cron 调度能力，届时再引入。

---

## 四、最终精简后的 MVP 开发计划

### 1. 项目目录结构

```
smart_home/
├── src/                          # 现有前端（冻结）
├── public/                       # 现有静态资源（冻结）
├── backend/                      # 新增：Python 后端
│   ├── __init__.py
│   ├── main.py                   # FastAPI 应用入口
│   ├── config.py                 # 配置管理（SimulationMode, LLM 配置等）
│   │
│   ├── models/                   # Pydantic 数据模型
│   │   ├── __init__.py
│   │   ├── message.py            # UnifiedMessage（4 种 kind）
│   │   ├── task.py               # Task + TaskStatus
│   │   ├── proposal.py           # Proposal + ProposalStatus（含 modified）
│   │   ├── device.py             # DeviceState, SensorState, RoomState, HouseState
│   │   ├── agent_manifest.py     # AgentInfo / AgentManifest
│   │   ├── constraint.py         # ConstraintContext
│   │   └── trace.py              # TraceEvent, LLMCacheKey, LLMCacheEntry
│   │
│   ├── runtime/                  # 运行时层
│   │   ├── __init__.py
│   │   ├── agent_core.py         # AgentCore 最小循环
│   │   ├── tool_registry.py      # ToolRegistry（最小实现）
│   │   ├── context_manager.py    # ContextManager（占位，Phase 1 完善）
│   │   ├── llm_client.py         # LiteLLM + instructor 封装
│   │   └── record_replay.py      # RecordReplayProxy + LLMCacheStore
│   │
│   ├── control/                  # 控制平面
│   │   ├── __init__.py
│   │   ├── orchestrator.py       # Orchestrator（单层编排）
│   │   ├── agent_registry.py     # AgentRegistry（注册 + 查询 + 心跳）
│   │   ├── task_board.py         # TaskBoard（SQLAlchemy + CAS）
│   │   ├── event_bus.py          # EventBus（pyee 实现 IEventBus）
│   │   ├── session_manager.py    # SessionManager（创建/销毁/隔离）
│   │   └── policy_arbiter.py     # PolicyArbiter（MVP: 占位通过）
│   │
│   ├── simulation/               # 仿真层
│   │   ├── __init__.py
│   │   ├── world_state.py        # SimulationWorld（SimPy + 实体模型）
│   │   ├── action_executor.py    # ActionExecutor（占位实现 + WriteToken）
│   │   └── event_log.py          # EventLog（structlog + JSONL）
│   │
│   ├── agents/                   # 领域 Agent 实现
│   │   ├── __init__.py
│   │   ├── orchestrator_agent.py # OrchestratorAgent
│   │   └── lighting_agent.py     # LightingAgent
│   │
│   ├── api/                      # API 层
│   │   ├── __init__.py
│   │   ├── routes_session.py     # POST /sessions, GET /sessions/{id}
│   │   └── routes_goal.py        # POST /sessions/{id}/goals
│   │
│   └── db/                       # 数据库
│       ├── __init__.py
│       ├── tables.py             # SQLAlchemy ORM 模型（TaskRow, LLMCacheRow）
│       └── connection.py         # 数据库连接管理
│
├── tests/                        # 测试
│   ├── __init__.py
│   ├── test_agent_core.py        # AgentCore 循环测试
│   ├── test_event_bus.py         # EventBus pub/sub + P2P 测试
│   ├── test_task_board.py        # TaskBoard CAS 乐观锁测试
│   ├── test_proposal_sm.py       # Proposal 状态机转换测试
│   ├── test_record_replay.py     # RecordReplayProxy 三种模式测试
│   └── test_e2e_lighting.py      # 端到端：用户目标 → Lighting 执行 → EventLog
│
├── docs/                         # 文档（已有）
├── requirements.txt              # Python 依赖
├── pyproject.toml                # 项目元数据
├── package.json                  # 前端依赖（已有·冻结）
└── README.md                     # 已有
```

### 2. requirements.txt

```txt
# === 仿真 ===
simpy==4.1.1

# === 状态机 ===
transitions==0.9.2

# === LLM ===
litellm==1.49.1
instructor==1.6.4

# === 数据库 ===
sqlalchemy==2.0.36

# === 事件总线 ===
pyee==12.1.1

# === 上下文管理 ===
tiktoken==0.8.0

# === 日志 ===
structlog==24.4.0

# === API ===
fastapi==0.115.6
uvicorn[standard]==0.32.1

# === 数据模型 ===
pydantic==2.10.3

# === 开发与测试 ===
pytest==8.3.4
pytest-asyncio==0.24.0
httpx==0.28.1
ruff==0.8.4
```

**版本锁定说明**：
- 所有版本锁定到具体 patch 版本，避免隐式升级导致兼容性问题
- `instructor` 锁定 1.6.x，避免 2.x breaking changes
- `uvicorn[standard]` 包含 websockets 依赖，无需单独安装
- `httpx` 用于 FastAPI TestClient 的异步测试

### 3. 开发顺序（关键路径 + 并行）

```
                    Week 1                         Week 2                         Week 3
                ┌─────────────────────┐    ┌─────────────────────┐    ┌─────────────────────┐
   关键路径     │ models/ → event_log │    │ agent_core →        │    │ 端到端集成 →        │
   (串行)      │ → event_bus →       │    │ record_replay →     │    │ FastAPI API →       │
                │ agent_registry →    │    │ orchestrator →      │    │ 集成测试 →          │
                │ task_board →        │    │ lighting_agent      │    │ 验收                │
                │ session_manager     │    │                     │    │                     │
                └─────────────────────┘    └─────────────────────┘    └─────────────────────┘

   可并行      │ world_state（SimPy） │    │ tool_registry       │    │ test_e2e_lighting   │
   (与上方     │ action_executor     │    │ context_manager     │    │ 文档更新            │
    并行)      │ db/tables.py        │    │ llm_client          │    │                     │
                │                     │    │ policy_arbiter(占位)│    │                     │
```

**串行依赖链（关键路径）**：
1. `models/` — 所有其他模块依赖数据模型
2. `event_log.py` — EventBus 依赖 EventLog 持久化
3. `event_bus.py` — AgentCore、Orchestrator 均依赖
4. `agent_registry.py` — Orchestrator 能力发现依赖
5. `task_board.py` + `session_manager.py` — Orchestrator 任务管理依赖
6. `agent_core.py` — Agent 运行的基础
7. `orchestrator.py` → `lighting_agent.py` — 端到端验证

**可并行项**：
- `world_state.py` + `action_executor.py` 与控制平面组件并行开发
- `tool_registry.py` + `llm_client.py` 与 agent_core 并行开发
- 所有单元测试与对应组件同步编写

### 4. MVP 验收标准

#### 验收测试 1：Session 生命周期

```
给定：系统已启动
当：通过 API 创建仿真 session（POST /api/sessions）
则：
  - 返回唯一 session_id
  - SessionManager 中可查到该 session
  - WorldState 已初始化（含默认 House + 至少 1 个 Room + bedroom_light_1 设备）
  - EventLog 记录 session.created 事件（含 trace_id）

当：通过 API 销毁该 session（DELETE /api/sessions/{id}）
则：
  - SessionManager 中该 session 标记为 destroyed
  - 相关 Agent 已注销
```

#### 验收测试 2：Agent 注册与发现

```
给定：session 已创建
当：OrchestratorAgent 和 LightingAgent 通过 manifest 注册到 AgentRegistry
则：
  - AgentRegistry.find_by_capabilities(["lighting_control"]) 返回 LightingAgent
  - AgentRegistry.find_by_capabilities(["nonexistent"]) 返回空列表
  - 两个 Agent 均为 "online" 状态
```

#### 验收测试 3：端到端照明控制（核心验收场景）

```
给定：session 已创建，OrchestratorAgent + LightingAgent 已注册，bedroom_light_1 初始状态 power=off
当：用户提交目标 "打开卧室灯"（POST /api/sessions/{id}/goals）
则：
  1. Orchestrator 接收目标，分解为 lighting 子任务
  2. TaskBoard 中出现该任务（status=pending → in_progress）
  3. LightingAgent 通过 AgentCore 循环执行：
     a. perceive: 从 EventBus 收到任务通知
     b. think: 调用 LLM（通过 RecordReplayProxy），生成照明提案
     c. act: 提交 proposal（status=submitted）
  4. PolicyArbiter（占位）批准提案（status=approved）
  5. ActionExecutor 执行动作，更新 WorldState：
     bedroom_light_1.power = "on", brightness > 0
  6. Proposal 状态流转完成（status=confirmed）
  7. TaskBoard 任务标记完成（status=completed）
  8. EventLog 包含完整 trace 链（≥ 5 条事件），所有事件携带相同 trace_id

验证 trace 完整性：
  - 按 trace_id 查询 EventLog，事件类型包括：
    task.created, proposal.submitted, proposal.approved,
    device.changed, task.completed
```

#### 验收测试 4：DRYRUN 模式（无 LLM 调用）

```
给定：session 以 DRYRUN 模式创建
当：执行与测试 3 相同的流程
则：
  - LightingAgent.think() 返回 mock 响应
  - 不产生实际 LLM API 调用
  - 端到端流程仍然完成（mock 提案 → 占位仲裁 → 执行）
  - EventLog 记录完整
```

#### 验收测试 5：TaskBoard CAS 乐观锁

```
给定：TaskBoard 中有一个 pending 任务（version=1）
当：两个 Agent 同时尝试认领（claim_task with expected_version=1）
则：
  - 恰好一个返回 success=True（version 变为 2）
  - 另一个返回 success=False（conflict）
  - 任务 owner 为第一个成功者
```

### 5. 从 MVP 到 Phase 1 的增量清单

| # | 增量项 | 描述 | 依赖 MVP 组件 | 预估工作量 |
|---|--------|------|-------------|-----------|
| 1 | HVAC Agent | 温控领域 Agent（manifest + think 逻辑 + 传感器订阅） | AgentCore, AgentRegistry | 3 天 |
| 2 | ProtocolManager | transitions 状态机（Proposal-Approval 完整 8 状态） | models/proposal.py | 2 天 |
| 3 | PolicyArbiter 真实实现 | 确定性规则引擎（安全 + 权限两级），替换占位实现 | RuleEngine（新建） | 3 天 |
| 4 | RuleEngine | PolicyArbiter 内部组件，规则匹配 + 优先级排序 | models/constraint.py | 2 天 |
| 5 | ContextManager | 上下文压缩（tiktoken 计数 + 摘要策略 + 身份重注入） | AgentCore | 2 天 |
| 6 | ToolRegistry 安全分级 | READ_ONLY / PROPOSE / EXECUTE 三级权限 | ToolRegistry | 1 天 |
| 7 | 跨域协调 | Orchestrator 支持多 Agent 提案汇聚 + 冲突检测 | Orchestrator, PolicyArbiter | 2 天 |
| 8 | WebSocket 推送 | state.snapshot, device.changed, task.changed 事件推送 | EventBus, FastAPI | 2 天 |
| 9 | SimPy-asyncio 桥接 | SimPy 线程模型 + asyncio 桥接（run_in_executor） | SimulationWorld | 1 天 |
| 10 | REPLAY 模式 | RecordReplayProxy REPLAY 模式 + LLMCacheStore SQLite 实现 | record_replay.py | 2 天 |
| 11 | Phase 1 集成测试 | 睡眠模式场景（Orchestrator + Lighting + HVAC 联动） | 以上全部 | 3 天 |

**Phase 1 总预估**：约 3 周（单人）或 2 周（双人并行）

**Phase 1 验收场景**：用户说"启动睡眠模式"→ Orchestrator 分解为 Lighting 子任务 + HVAC 子任务 → 两个 Agent 分别生成提案 → PolicyArbiter 进行规则仲裁（安全 + 权限）→ 批准执行 → WebSocket 推送状态变化。
