# Agent 梯队精简与组件依赖优化方案

> 历史说明  
> 当前状态：历史讨论稿，不作为当前实现依据。  
> 当前唯一主设计稿：`docs/floorplan-to-3d-minimum-implementation.md`

版本：v1.0
日期：2026-03-24
状态：精简方案（基于架构文档 v1.1 评审反馈）

---

## 1. Agent 四梯队接入计划

### 1.1 现状问题

原架构文档在阶段三一次性引入 4 个领域 Agent（Orchestrator + Lighting + HVAC + Security + Energy），阶段四再引入 3 个上下文 Agent（Habit + Health + FaultDiagnosis），总计 8 个 Agent。这带来以下问题：

- 一次性上线 4 个领域 Agent，调试和集成测试复杂度过高
- Habit 和 Health 职责重叠（睡眠、舒适度），独立维护增加耦合
- 所有 Agent 依赖的控制平面组件必须在 Agent 之前全部就绪，导致前几个阶段"只建基础设施、没有可见成果"

### 1.2 精简后的四梯队计划

```
梯队一（MVP）    → Orchestrator + Lighting                     = 2 个 Agent
梯队二（Phase 1） → + HVAC                                     = 3 个 Agent
梯队三（Phase 2） → + Security + Energy(规则模块，非独立 Agent)  = 4 个 Agent
梯队四（Phase 3） → + UserContext(合并 Habit+Health) + FaultDiagnosis = 6 个 Agent
```

---

#### 梯队一：MVP — Orchestrator + Lighting

| Agent | 精确职责 | 接入理由 |
|-------|---------|---------|
| **Orchestrator** | 1. 接收用户目标，分解为单域任务<br>2. 管理 TaskBoard 中的任务生命周期<br>3. 将 Agent 提案转交占位仲裁<br>4. 输出含 trace_id 的端到端日志 | 系统入口，所有其他 Agent 都依赖它进行任务分发；MVP 阶段只需最小功能子集（无跨域协调） |
| **Lighting** | 1. 根据任务生成照明参数提案（亮度、色温、开关）<br>2. 解析房间上下文选择照明策略<br>3. 通过 Proposal 协议提交动作<br>4. 响应仿真层执行结果并记录 | 照明是最简单的单设备域，状态模型简单（on/off + brightness + color_temp），最适合验证端到端链路 |

**MVP 验收场景**：用户说"打开卧室灯"→ Orchestrator 分解任务 → Lighting 生成提案 → 占位仲裁通过 → ActionExecutor 执行 → EventLog 记录完整 trace。

**MVP 阶段依赖的最小组件集**：
- AgentCore（最小循环）
- AgentRegistry（注册 + 查询）
- TaskBoard（无依赖图版本）
- SessionManager（创建/销毁）
- EventBus（最小 pub/sub）
- ActionExecutor（占位实现）
- EventLog（append-only）

---

#### 梯队二：Phase 1 — + HVAC

| Agent | 精确职责 | 接入理由 |
|-------|---------|---------|
| **HVAC** | 1. 根据温度/湿度传感器数据生成温控提案<br>2. 管理空调、加湿器、新风系统参数<br>3. 处理与 Lighting 的跨域协调（如睡眠场景）<br>4. 响应能耗约束调整运行参数<br>5. 支持舒适度评分计算 | 引入 HVAC 后首次出现**跨域协调**需求（照明+温控联动），驱动 ProtocolManager 和 PolicyArbiter 的真实实现 |

**Phase 1 验收场景**：用户说"启动睡眠模式"→ Orchestrator 分解为 Lighting 子任务 + HVAC 子任务 → 两个 Agent 分别生成提案 → PolicyArbiter 进行冲突检测（如能耗总量）→ 批准执行。

**Phase 1 新增组件**：
- ProtocolManager（request-response + proposal-approval 状态机）
- PolicyArbiter（确定性规则引擎，安全/权限两级）
- ContextManager（上下文压缩）
- ToolRegistry（工具注册与权限控制）

---

#### 梯队三：Phase 2 — + Security + Energy(规则模块)

| Agent | 精确职责 | 接入理由 |
|-------|---------|---------|
| **Security** | 1. 管理门锁、安防传感器、摄像头状态<br>2. 执行离家/在家/夜间模式切换<br>3. 生成入侵告警与 Incident<br>4. 与 Lighting 联动（如离家关灯）<br>5. 安全约束具有最高优先级（P1） | Security 引入**安全约束**这一最高优先级维度，驱动 PolicyArbiter 完整实现全部 7 级优先级；同时引入 Incident 协议 |
| **Energy（规则模块）** | 1. 定义能耗预算上限规则<br>2. 计算当前功率总和与预算差距<br>3. 输出能耗约束（ConstraintContext）<br>4. 峰谷时段标记 | Energy **不作为独立 Agent**，而是作为 PolicyArbiter 内的规则模块实现。理由：能耗约束本质上是仲裁规则，不需要 LLM 推理，纯计算即可 |

**为什么 Energy 不是独立 Agent**：
- Energy 的核心逻辑是"当前功率 > 预算 → 拒绝/降级提案"，这是确定性规则
- 不需要 LLM 进行推理
- 作为 PolicyArbiter 的内置模块可以零延迟执行
- 省去一个 Agent 的心跳、上下文、Mailbox 开销

**Phase 2 新增组件**：
- RuleEngine（PolicyArbiter 内部）
- ConstraintStore（约束持久化）
- Incident 协议完整实现
- MetricsCollector（开始采集评测指标）

---

#### 梯队四：Phase 3 — + UserContext + FaultDiagnosis

| Agent | 精确职责 | 接入理由 |
|-------|---------|---------|
| **UserContext**（合并 Habit+Health） | 1. 分析用户作息规律与行为模式<br>2. 输出时段约束与场景偏好预测<br>3. 评估舒适度阈值与健康风险<br>4. 发布标准化 ConstraintContext<br>5. 维护用户画像长期记忆 | 依赖前三个梯队的全部基础设施；需要足够多的设备数据和场景执行历史才能进行有意义的模式分析 |
| **FaultDiagnosis** | 1. 监听设备故障和传感器异常事件<br>2. 分析故障根因（关联分析）<br>3. 生成恢复建议和降级策略<br>4. 管理 Incident 生命周期<br>5. 输出诊断报告 | 故障诊断需要所有设备类型的 Agent 都已接入，才能进行跨域关联分析；依赖 Incident 协议和 MetricsCollector |

**Phase 3 验收场景**：UserContext 根据历史数据预测"用户通常 22:30 入睡"→ 发布 ConstraintContext → PolicyArbiter 在 22:00 自动触发睡眠准备 → Lighting+HVAC 协同执行。

---

### 1.3 梯队依赖关系图

```
梯队一 (MVP)
  Orchestrator ─── 依赖 → AgentCore, AgentRegistry, TaskBoard, EventBus
  Lighting     ─── 依赖 → AgentCore, ActionExecutor

梯队二 (Phase 1)
  HVAC ─── 依赖 → 梯队一全部 + ProtocolManager, PolicyArbiter
           跨域依赖 → Lighting（睡眠/离家等联动场景）

梯队三 (Phase 2)
  Security ─── 依赖 → 梯队二全部 + Incident 协议
               触发 → PolicyArbiter 安全级别规则完善
  Energy   ─── 寄生于 → PolicyArbiter.RuleEngine

梯队四 (Phase 3)
  UserContext    ─── 依赖 → 梯队三全部 + ConstraintStore + 历史数据
  FaultDiagnosis ─── 依赖 → 梯队三全部 + MetricsCollector + Incident 协议
```

---

## 2. Habit + Health 合并方案

### 2.1 合并原因

| 维度 | Habit Agent | Health Agent | 重叠程度 |
|------|------------|-------------|---------|
| 睡眠分析 | 分析入睡/起床时间规律 | 评估睡眠质量条件 | **高度重叠** |
| 舒适度 | 偏好的温度/亮度 | 健康安全的温度/亮度 | **高度重叠** |
| 数据源 | 传感器 + 操作记录 | 传感器 + 环境数据 | **相同数据源** |
| 输出形式 | ConstraintContext | ConstraintContext | **完全相同** |
| 时间维度 | 日/周规律预测 | 实时阈值监控 | 中度重叠 |

两个 Agent 使用相同的数据源、产出相同格式的约束，且核心关注点（睡眠、温度、光照）高度重合。独立运行会导致：
- 对同一传感器数据的重复订阅和处理
- 两套约束可能互相冲突（如 Habit 建议 24°C，Health 要求 ≤22°C）
- 需要额外的仲裁逻辑来协调两者输出

### 2.2 合并后的 UserContext Agent

#### Manifest 设计

```json
{
  "agent_id": "user_context_agent",
  "role": "context_reasoner",
  "capabilities": [
    "habit_pattern_analyzer",
    "comfort_constraint_provider",
    "sleep_schedule_predictor",
    "health_risk_assessor",
    "user_preference_profiler"
  ],
  "publishes": [
    "user_context.constraint.updated",
    "user_context.habit.predicted",
    "user_context.risk.alerted",
    "user_context.profile.changed"
  ],
  "subscribes": [
    "sensor.temperature.changed",
    "sensor.humidity.changed",
    "sensor.light.changed",
    "sensor.motion.changed",
    "device.*.operated",
    "scenario.*.started",
    "scenario.*.ended"
  ],
  "request_handlers": [
    "predict_schedule",
    "assess_comfort",
    "get_user_preferences",
    "assess_health_risk"
  ],
  "proposal_scope": "advisory",
  "internal_modules": [
    "habit_analyzer",
    "health_monitor",
    "preference_store",
    "constraint_synthesizer"
  ]
}
```

#### 内部模块化设计

```python
from pydantic import BaseModel, Field
from enum import Enum
from typing import Protocol

class UserContextAgent:
    """合并后的用户上下文 Agent，内部按模块划分职责。"""

    def __init__(self):
        self.habit_analyzer = HabitAnalyzer()       # 原 Habit Agent 核心逻辑
        self.health_monitor = HealthMonitor()        # 原 Health Agent 核心逻辑
        self.preference_store = PreferenceStore()    # 用户偏好持久化
        self.constraint_synth = ConstraintSynthesizer()  # 约束合成与冲突消解

    async def think(self, context: AgentContext) -> list[ConstraintContext]:
        """统一思考入口，内部委派给各模块。"""
        # 1. 各模块独立分析
        habit_constraints = await self.habit_analyzer.analyze(context)
        health_constraints = await self.health_monitor.evaluate(context)
        preferences = await self.preference_store.get_active(context)

        # 2. 约束合成：health 优先级 > habit 优先级 > preference
        merged = self.constraint_synth.merge(
            health_constraints,   # priority 3 (健康)
            habit_constraints,    # priority 6 (偏好)
            preferences           # priority 7 (体验)
        )
        return merged
```

### 2.3 能力对照表

| 能力 | 原 Habit Agent | 原 Health Agent | 合并后 UserContext Agent |
|------|---------------|----------------|------------------------|
| 作息规律分析 | 主要职责 | - | `habit_analyzer` 模块 |
| 睡眠窗口预测 | 主要职责 | 辅助评估 | `habit_analyzer` 模块（综合两者逻辑） |
| 舒适度阈值评估 | 偏好层面 | 健康层面 | `constraint_synthesizer` 统一输出 |
| 环境健康风险告警 | - | 主要职责 | `health_monitor` 模块 |
| 时段约束输出 | 主要职责 | 时段依赖 | `constraint_synthesizer` 统一输出 |
| 用户偏好记忆 | 隐含 | 隐含 | `preference_store` 模块（显式持久化） |
| ConstraintContext 发布 | 独立发布 | 独立发布 | **统一发布，内部已消解冲突** |

### 2.4 关注点分离策略

合并不意味着混为一团。内部通过四个独立模块保持关注点分离：

```
UserContextAgent
 ├── HabitAnalyzer        # 只关心时间模式和行为规律
 │    ├── analyze_schedule()
 │    └── predict_next_event()
 ├── HealthMonitor         # 只关心健康阈值和风险
 │    ├── evaluate_comfort()
 │    └── check_risk()
 ├── PreferenceStore       # 只关心用户显式偏好
 │    ├── update_preference()
 │    └── get_active()
 └── ConstraintSynthesizer # 只关心约束合成与冲突消解
      ├── merge()
      └── resolve_conflict()
```

每个模块可以独立测试、独立演进。如果未来某个模块复杂度激增（如 HealthMonitor 需要接入医疗设备），可以再拆分为独立 Agent，manifest 的 `capabilities` 字段支持这种渐进式拆分。

---

## 3. 自建 vs 复用对照表

### 3.1 完整对照表

原文档中 18 个显式组件 + EventLog = 19 个：

| # | 组件 | 层级 | 决策 | 替代方案 | 理由 | 集成复杂度 |
|---|------|------|------|---------|------|-----------|
| 1 | **AgentCore** | 运行时 | 自建 | - | 核心循环是系统灵魂，必须自主控制 perceive→think→act→observe 流程 | - |
| 2 | **AgentRegistry** | 控制平面 | 自建 | - | manifest 注册制是架构核心机制，与心跳治理深度耦合，无合适替代 | - |
| 3 | **TaskBoard** | 控制平面 | **持久层复用** | SQLAlchemy + SQLite | 任务 CRUD 和 CAS 乐观锁可直接用 SQLAlchemy ORM 实现，业务逻辑自建 | 低 |
| 4 | **ProtocolManager** | 控制平面 | **复用** | `transitions` | 状态机（pending→acknowledged→responded 等）是 transitions 库的标准场景 | 低 |
| 5 | **PolicyArbiter** | 控制平面 | 自建 | - | 安全仲裁是系统最关键治理组件，优先级规则必须完全可控，不能依赖外部 | - |
| 6 | **SessionManager** | 控制平面 | 自建 | - | 仿真 session 生命周期与状态隔离是领域特定逻辑，无标准库可替代 | - |
| 7 | **Scheduler** | 控制平面 | **复用** | `APScheduler` | 定时触发、Cron 表达式、interval 调度是 APScheduler 的标准能力 | 低 |
| 8 | **ToolRegistry** | 运行时 | 自建 | - | 工具注册与 agent 级权限绑定是领域特定逻辑，代码量小（~100行），不值得引入依赖 | - |
| 9 | **ContextManager** | 运行时 | **部分复用** | `tiktoken` | token 计数用 tiktoken；压缩策略和身份重注入是领域逻辑需自建 | 低 |
| 10 | **SkillLoader** | 运行时 | **删除** | `importlib`（标准库） | 第一阶段 Agent 数量少，技能直接硬编码在 Agent 中；动态加载用 importlib 即可 | - |
| 11 | **MailboxAdapter** | 运行时 | **合并** | 合并入 EventBus | Mailbox 本质是 point-to-point 消息，可作为 EventBus 的定向投递模式实现 | - |
| 12 | **BackgroundJobManager** | 运行时 | **复用** | `asyncio.TaskGroup` | Python 3.11+ 的 TaskGroup 原生支持后台任务管理与异常处理 | 极低 |
| 13 | **WorldState** | 仿真层 | **复用** | `SimPy` 环境 | SimPy 的 Environment + Store/Resource 可建模房屋/房间/设备状态 | 中 |
| 14 | **EventScheduler** | 仿真层 | **复用** | `SimPy` | SimPy 的离散事件调度是其核心能力，完美匹配仿真时间推进需求 | 中 |
| 15 | **ActionExecutor** | 仿真层 | 自建 | - | 动作执行涉及仲裁结果消费和 WorldState 写入，是系统关键控制点 | - |
| 16 | **ScenarioRunner** | 仿真层 | 自建 | - | 场景脚本加载与执行是领域特定逻辑 | - |
| 17 | **ReplayEngine** | 仿真层 | **延迟** | Phase 5 再实现 | 回放依赖完整的 EventLog，前四个阶段积累数据即可 | - |
| 18 | **EventLog** | 仿真层 | **复用** | `structlog` + JSONL | 结构化日志用 structlog，存储为 JSONL 文件，查询用简单文件扫描 | 低 |
| 19 | **LLM 调用层** | 运行时 | **复用** | `LiteLLM` + `instructor` | LLM 调用不应自建，LiteLLM 统一多模型接口，instructor 保证结构化输出 | 低 |

**统计**：
- 自建：7 个（AgentCore, AgentRegistry, PolicyArbiter, SessionManager, ToolRegistry, ActionExecutor, ScenarioRunner）
- 复用：8 个（TaskBoard 持久层, ProtocolManager, Scheduler, ContextManager 部分, BackgroundJobManager, WorldState+EventScheduler, EventLog, LLM 调用层）
- 删除/合并：2 个（SkillLoader 删除, MailboxAdapter 合并入 EventBus）
- 延迟：1 个（ReplayEngine）
- 新增（缺失补齐）：4 个（EventBus, RuleEngine, MetricsCollector, ConstraintStore）

### 3.2 核心复用方案详解

#### 3.2.1 SimPy 替代 WorldState + EventScheduler + 时间模型

```python
import simpy
from pydantic import BaseModel, Field
from typing import Any

class DeviceState(BaseModel):
    """设备状态模型。"""
    device_id: str
    device_type: str  # light | hvac | door_lock | sensor | window
    properties: dict[str, Any] = Field(default_factory=dict)
    room_id: str
    rated_power_watts: float = 0.0  # 额定功率（用于 AtomicArbiter 功率校验）

class SensorState(BaseModel):
    """传感器状态模型（M3: 补齐缺失实体）。"""
    sensor_id: str
    sensor_type: str  # temperature | humidity | light_level | motion | smoke
    room_id: str
    value: float = 0.0
    unit: str = ""  # °C | % | lux | bool
    threshold_min: float | None = None  # 报警下限
    threshold_max: float | None = None  # 报警上限

class RoomState(BaseModel):
    """房间状态模型（M3: 补齐缺失实体）。"""
    room_id: str
    room_type: str  # bedroom | living_room | kitchen | bathroom | hallway
    devices: list[str] = Field(default_factory=list)   # device_id 列表
    sensors: list[str] = Field(default_factory=list)    # sensor_id 列表

class HouseState(BaseModel):
    """房屋状态模型（M3: 补齐缺失实体）。"""
    house_id: str = "default"
    rooms: dict[str, RoomState] = Field(default_factory=dict)
    circuit_capacity_watts: float = 5000.0  # 电路额定容量
    security_armed: bool = False             # 安防模式是否激活

class SimulationWorld:
    """基于 SimPy 的仿真世界，替代自建 WorldState + EventScheduler。

    包含完整实体模型：House → Room → Device + Sensor（M3 补齐）。
    """

    def __init__(self):
        self.env = simpy.Environment()
        self.house: HouseState = HouseState()
        self.devices: dict[str, DeviceState] = {}
        self.sensors: dict[str, SensorState] = {}
        self.event_log: list[dict] = []
        self.session_id: str = ""

    def register_room(self, room: RoomState) -> None:
        """注册房间到房屋模型。"""
        self.house.rooms[room.room_id] = room

    def register_device(self, device: DeviceState) -> None:
        """注册设备，同时关联到所属房间。"""
        self.devices[device.device_id] = device
        if device.room_id in self.house.rooms:
            room = self.house.rooms[device.room_id]
            if device.device_id not in room.devices:
                room.devices.append(device.device_id)

    def register_sensor(self, sensor: SensorState) -> None:
        """注册传感器，同时关联到所属房间。"""
        self.sensors[sensor.sensor_id] = sensor
        if sensor.room_id in self.house.rooms:
            room = self.house.rooms[sensor.room_id]
            if sensor.sensor_id not in room.sensors:
                room.sensors.append(sensor.sensor_id)

    def get_device(self, device_id: str) -> DeviceState | None:
        """查询设备状态（AtomicArbiter 组合安全检查使用）。"""
        return self.devices.get(device_id)

    def get_total_power_load(self) -> float:
        """计算当前总功率负载。"""
        return sum(
            d.rated_power_watts for d in self.devices.values()
            if d.properties.get("power") == "on"
        )

    def get_circuit_capacity(self) -> float:
        """获取电路额定容量。"""
        return self.house.circuit_capacity_watts

    def is_security_armed(self) -> bool:
        """查询安防模式是否激活。"""
        return self.house.security_armed

    def schedule_action(
        self, delay: float, action_id: str, device_id: str, params: dict
    ) -> simpy.Event:
        """调度一个延迟执行的设备动作。"""
        def _execute(env: simpy.Environment):
            yield env.timeout(delay)
            device = self.devices[device_id]
            device.properties.update(params)
            self.event_log.append({
                "sim_time": env.now,
                "action_id": action_id,
                "device_id": device_id,
                "params": params,
                "event_type": "device.changed",
            })

        return self.env.process(_execute(self.env))

    def step(self, until: float | None = None) -> None:
        """推进仿真时钟。"""
        if until is not None:
            self.env.run(until=until)
        else:
            self.env.step()

    @property
    def now(self) -> float:
        return self.env.now

# 使用示例
world = SimulationWorld()
world.session_id = "sim_20260312_01"

# M3: 先注册房间，再注册设备和传感器
world.register_room(RoomState(room_id="bedroom", room_type="bedroom"))
world.register_device(DeviceState(
    device_id="bedroom_light_1",
    device_type="light",
    properties={"power": "off", "brightness": 0, "color_temp": 4000},
    room_id="bedroom",
    rated_power_watts=60.0,
))
world.register_sensor(SensorState(
    sensor_id="bedroom_temp_1",
    sensor_type="temperature",
    room_id="bedroom",
    value=24.0, unit="°C",
    threshold_min=16.0, threshold_max=30.0,
))

world.schedule_action(
    delay=0,
    action_id="act_101",
    device_id="bedroom_light_1",
    params={"power": "on", "brightness": 20, "color_temp": 2700},
)
world.step()
# world.devices["bedroom_light_1"].properties == {"power": "on", "brightness": 20, "color_temp": 2700}
# world.get_total_power_load() == 60.0
# world.house.rooms["bedroom"].devices == ["bedroom_light_1"]
```

**集成要点**：
- SimPy 的 `Environment` 替代自建时间模型和事件调度
- `env.timeout()` 天然支持设备动作延迟
- `env.run(until=)` 支持暂停和单步执行
- 回放能力通过 event_log 的重新投放实现

---

#### 3.2.2 transitions 替代 ProtocolManager 状态机

```python
from transitions import Machine
from pydantic import BaseModel, Field
from enum import Enum
from datetime import datetime

class ProposalState(str, Enum):
    DRAFTED = "drafted"
    SUBMITTED = "submitted"
    APPROVED = "approved"
    REJECTED = "rejected"
    MODIFIED = "modified"       # M2: approved_with_modification
    EXECUTED = "executed"
    CONFIRMED = "confirmed"
    FAILED = "failed"

class Proposal(BaseModel):
    """动作提案模型。"""
    proposal_id: str
    agent_id: str
    device_id: str
    operation: str
    params: dict
    state: ProposalState = ProposalState.DRAFTED
    trace_id: str
    created_at: float  # sim_time
    decided_at: float | None = None
    decision_reason: str | None = None

class ProposalStateMachine:
    """基于 transitions 的 Proposal-Approval 协议状态机。"""

    TRANSITIONS = [
        {"trigger": "submit", "source": "drafted", "dest": "submitted"},
        {"trigger": "approve", "source": "submitted", "dest": "approved"},
        {"trigger": "reject", "source": "submitted", "dest": "rejected"},
        {"trigger": "modify", "source": "submitted", "dest": "modified"},  # M2: approved_with_modification
        {"trigger": "execute", "source": "approved", "dest": "executed"},
        {"trigger": "execute", "source": "modified", "dest": "executed"},  # M2: modified 也可执行
        {"trigger": "confirm", "source": "executed", "dest": "confirmed"},
        {"trigger": "fail", "source": "executed", "dest": "failed"},
    ]

    def __init__(self, proposal: Proposal):
        self.proposal = proposal
        self.machine = Machine(
            model=self,
            states=[s.value for s in ProposalState],
            transitions=self.TRANSITIONS,
            initial=proposal.state.value,
            send_event=True,
            after_state_change="on_state_change",
        )

    @property
    def state(self) -> str:
        # transitions 在 model 上设置 state 属性
        return self.machine.model.state

    def on_state_change(self) -> None:
        """状态变更回调，用于写入 EventLog。"""
        self.proposal.state = ProposalState(self.state)

# 使用示例
proposal = Proposal(
    proposal_id="prop_001",
    agent_id="lighting_agent",
    device_id="bedroom_light_1",
    operation="set_state",
    params={"brightness": 20},
    trace_id="tr_abc123",
    created_at=1710000000,
)
sm = ProposalStateMachine(proposal)
sm.submit()   # drafted -> submitted
sm.approve()  # submitted -> approved
sm.execute()  # approved -> executed
sm.confirm()  # executed -> confirmed
```

同理，Request-Response 和 Incident 协议也用 `transitions` 建模：

```python
class RequestStateMachine:
    TRANSITIONS = [
        {"trigger": "acknowledge", "source": "pending", "dest": "acknowledged"},
        {"trigger": "respond", "source": "acknowledged", "dest": "responded"},
        {"trigger": "timeout", "source": "pending", "dest": "timed_out"},
        {"trigger": "reject", "source": "pending", "dest": "rejected"},
    ]

class IncidentStateMachine:
    TRANSITIONS = [
        {"trigger": "triage", "source": "detected", "dest": "triaged"},
        {"trigger": "assign", "source": "triaged", "dest": "assigned"},
        {"trigger": "mitigate", "source": "assigned", "dest": "mitigated"},
        {"trigger": "resolve", "source": "mitigated", "dest": "resolved"},
        {"trigger": "escalate", "source": "detected", "dest": "escalated"},
    ]
```

---

#### 3.2.3 LiteLLM + instructor 替代自建 LLM 调用层

```python
import instructor
from litellm import acompletion
from pydantic import BaseModel, Field

# instructor 包装 litellm，保证结构化输出
client = instructor.from_litellm(acompletion)

class LightingProposal(BaseModel):
    """Lighting Agent 的结构化输出。"""
    device_id: str = Field(description="目标设备 ID")
    power: str = Field(description="开关状态", pattern="^(on|off)$")
    brightness: int = Field(ge=0, le=100, description="亮度百分比")
    color_temp: int = Field(ge=2000, le=6500, description="色温(K)")
    reasoning: str = Field(description="决策原因")

async def lighting_think(
    system_prompt: str,
    user_context: str,
    model: str = "claude-sonnet-4-6-20250514",
) -> LightingProposal:
    """Lighting Agent 的 think 步骤，使用 instructor 保证结构化输出。"""
    response = await client.create(
        model=model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_context},
        ],
        response_model=LightingProposal,
        max_retries=2,
    )
    return response

# 使用示例
proposal = await lighting_think(
    system_prompt="你是照明控制专家...",
    user_context="用户要求启动卧室睡眠模式，当前亮度80%，色温5000K",
)
# proposal.brightness == 20, proposal.color_temp == 2700, etc.
```

**优势**：
- LiteLLM 统一 OpenAI/Anthropic/本地模型接口，一行代码切换模型
- instructor 用 Pydantic 模型约束 LLM 输出，自带重试和验证
- 省去自建 prompt 模板引擎、输出解析、重试逻辑
- 天然支持 async，与 AgentCore 异步循环兼容

---

#### 3.2.4 SQLAlchemy 替代自建 TaskBoard 持久化（含 CAS 乐观锁）

```python
from sqlalchemy import create_engine, Column, Integer, String, JSON, select, update
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

class Base(DeclarativeBase):
    pass

class TaskRow(Base):
    """任务板数据模型。"""
    __tablename__ = "tasks"

    id = Column(Integer, primary_key=True, autoincrement=True)
    subject = Column(String, nullable=False)
    description = Column(String, default="")
    status = Column(String, default="pending")  # pending | in_progress | completed | failed
    owner = Column(String, nullable=True)
    blocked_by = Column(JSON, default=list)
    priority = Column(String, default="normal")
    session_id = Column(String, nullable=False)
    result_ref = Column(String, nullable=True)
    trace_id = Column(String, nullable=True)
    version = Column(Integer, default=1, nullable=False)

# CAS 乐观锁认领
def claim_task(
    db: Session,
    task_id: int,
    agent_id: str,
    expected_version: int,
) -> bool:
    """
    Compare-And-Swap 乐观锁认领任务。
    只有 status=pending 且 version 匹配时才能认领成功。
    """
    result = db.execute(
        update(TaskRow)
        .where(
            TaskRow.id == task_id,
            TaskRow.status == "pending",
            TaskRow.version == expected_version,
        )
        .values(
            owner=agent_id,
            status="in_progress",
            version=expected_version + 1,
        )
    )
    db.commit()
    return result.rowcount == 1  # True=认领成功, False=冲突

# 初始化
engine = create_engine("sqlite:///smart_home.db", echo=False)
Base.metadata.create_all(engine)
SessionLocal = sessionmaker(bind=engine)

# 使用示例
with SessionLocal() as db:
    # 创建任务
    task = TaskRow(
        subject="prepare_sleep_mode",
        description="协同卧室照明、温度策略",
        priority="high",
        session_id="sim_20260312_01",
        trace_id="tr_abc123",
    )
    db.add(task)
    db.commit()

    # Agent 认领
    success = claim_task(db, task_id=task.id, agent_id="lighting_agent", expected_version=1)
    # success == True, task.version 现在是 2
```

---

#### 3.2.5 pyee 替代自建 EventBus

```python
from pyee.asyncio import AsyncIOEventEmitter
from pydantic import BaseModel, Field
from typing import Any, Callable, Awaitable
from collections import defaultdict
import logging

logger = logging.getLogger(__name__)

class EventMessage(BaseModel):
    """统一事件消息模型（精简为 4 种 kind）。"""
    message_id: str
    correlation_id: str | None = None  # response 关联 request，decision 关联 proposal
    session_id: str
    kind: str  # event | request | proposal | heartbeat（4 种）
    status: str | None = None  # request: null/fulfilled/rejected/error; proposal: drafted/submitted/approved/rejected/modified/...
    from_agent: str
    to_agent: str | None = None  # None = broadcast
    topic: str
    payload: dict[str, Any] = Field(default_factory=dict)
    sim_time: float
    trace_id: str
    ttl_ms: int = 5000
    require_ack: bool = False

class EventBus:
    """
    基于 pyee 的事件总线，替代自建 EventBus。
    支持 pub/sub 广播和 point-to-point 定向投递（替代 MailboxAdapter）。
    """

    def __init__(self):
        self._emitter = AsyncIOEventEmitter()
        self._dead_letters: list[EventMessage] = []

    def subscribe(
        self,
        topic: str,
        handler: Callable[[EventMessage], Awaitable[None]],
    ) -> None:
        """订阅主题。"""
        self._emitter.on(topic, handler)

    def unsubscribe(
        self,
        topic: str,
        handler: Callable[[EventMessage], Awaitable[None]],
    ) -> None:
        """取消订阅。"""
        self._emitter.remove_listener(topic, handler)

    async def publish(self, message: EventMessage) -> None:
        """
        发布消息。
        - to_agent 为 None 时广播到 topic 所有订阅者
        - to_agent 非 None 时投递到 agent 专属 mailbox topic
        """
        topic = message.topic
        if message.to_agent:
            # point-to-point: 投递到 agent 的 mailbox topic
            topic = f"mailbox.{message.to_agent}"

        listeners = self._emitter.listeners(topic)
        if not listeners:
            logger.warning(f"Dead letter: topic={topic}, msg_id={message.message_id}")
            self._dead_letters.append(message)
            return

        self._emitter.emit(topic, message)

    def get_dead_letters(self) -> list[EventMessage]:
        """获取死信队列（无人消费的消息）。"""
        letters = self._dead_letters.copy()
        self._dead_letters.clear()
        return letters

# 使用示例
bus = EventBus()

async def on_device_changed(msg: EventMessage):
    print(f"[{msg.from_agent}] {msg.topic}: {msg.payload}")

bus.subscribe("device.changed", on_device_changed)

await bus.publish(EventMessage(
    message_id="msg_001",
    session_id="sim_01",
    kind="event",
    from_agent="action_executor",
    topic="device.changed",
    payload={"device_id": "bedroom_light_1", "brightness": 20},
    sim_time=1710000000,
    trace_id="tr_abc",
))
```

---

## 4. 补齐缺失组件定义

### 4.1 EventBus

**层级归属**：运行时层（跨层使用，控制平面和仿真层均依赖）

**核心职责**：
- 统一消息路由（广播 + 定向投递）
- 替代原 MailboxAdapter 的 point-to-point 能力
- 死信队列管理
- 消息 TTL 过期清理

```python
from abc import ABC, abstractmethod
from typing import Callable, Awaitable, Any
from pydantic import BaseModel, Field


class EventMessage(BaseModel):
    """统一事件消息（精简为 4 种 kind）。"""
    message_id: str
    correlation_id: str | None = None  # response 关联 request，decision 关联 proposal
    session_id: str
    kind: str  # event | request | proposal | heartbeat（4 种）
    status: str | None = None  # request: null/fulfilled/rejected/error; proposal: 状态机流转
    from_agent: str
    to_agent: str | None = None
    topic: str
    payload: dict[str, Any] = Field(default_factory=dict)
    sim_time: float
    trace_id: str
    ttl_ms: int = 5000
    require_ack: bool = False


EventHandler = Callable[[EventMessage], Awaitable[None]]


class IEventBus(ABC):
    """EventBus 核心接口。"""

    @abstractmethod
    async def publish(self, message: EventMessage) -> None:
        """
        发布消息。

        路由规则：
        1. message.to_agent 非空 → 投递到 mailbox.{to_agent} 主题（point-to-point）
        2. message.to_agent 为空 → 广播到 message.topic 的所有订阅者
        3. 无订阅者时进入死信队列
        4. sim_time + ttl_ms 过期的消息直接丢弃并记录
        """
        ...

    @abstractmethod
    def subscribe(self, topic: str, handler: EventHandler) -> None:
        """
        订阅主题。

        Agent 注册时自动订阅 manifest 中声明的 subscribes 列表。
        同时自动订阅 mailbox.{agent_id} 用于接收定向消息。
        """
        ...

    @abstractmethod
    def unsubscribe(self, topic: str, handler: EventHandler) -> None:
        """取消订阅。Agent 注销时自动取消所有订阅。"""
        ...

    @abstractmethod
    def get_dead_letters(self) -> list[EventMessage]:
        """
        获取并清空死信队列。

        死信产生条件：
        1. 目标主题无任何订阅者
        2. 定向投递目标 agent 不在线
        """
        ...

    @abstractmethod
    def topic_subscriber_count(self, topic: str) -> int:
        """查询某主题的订阅者数量，用于监控。"""
        ...
```

---

### 4.2 RuleEngine

**层级归属**：控制平面 → PolicyArbiter 内部组件

**与 PolicyArbiter 的关系**：
- PolicyArbiter 是对外的仲裁接口
- RuleEngine 是 PolicyArbiter 的内部执行引擎
- PolicyArbiter 负责接收提案、注入约束、返回裁决
- RuleEngine 负责规则匹配、优先级排序、冲突消解

```python
from abc import ABC, abstractmethod
from pydantic import BaseModel, Field
from enum import Enum
from typing import Any


class RulePriority(int, Enum):
    """与架构文档 12.2 约束优先级对齐。"""
    SAFETY = 1
    PRIVACY = 2
    HEALTH = 3
    DEVICE_PROTECTION = 4
    ENERGY = 5
    USER_PREFERENCE = 6
    EXPERIENCE = 7


class RuleCondition(BaseModel):
    """规则条件。"""
    dimension: str = Field(description="设备属性路径，如 'lighting.brightness'")
    operator: str = Field(description="比较算符: eq|ne|gt|lt|gte|lte|in|not_in|range")
    value: Any = Field(description="比较值或范围 {'min': x, 'max': y}")


class Rule(BaseModel):
    """规则数据模型。"""
    rule_id: str
    name: str
    description: str
    priority: RulePriority
    conditions: list[RuleCondition] = Field(description="ALL 条件必须同时满足")
    action: str = Field(description="reject | approve | modify | defer")
    modification: dict[str, Any] | None = Field(
        default=None,
        description="action=modify 时的参数修改建议"
    )
    enabled: bool = True
    source: str = Field(description="规则来源: system | energy_module | user_config")


class RuleEvalResult(BaseModel):
    """单条规则评估结果。"""
    rule_id: str
    matched: bool
    action: str
    priority: RulePriority
    reason: str


class IRuleEngine(ABC):
    """规则引擎核心接口。"""

    @abstractmethod
    def add_rule(self, rule: Rule) -> None:
        """添加规则。如果 rule_id 已存在则更新。"""
        ...

    @abstractmethod
    def remove_rule(self, rule_id: str) -> bool:
        """删除规则。返回是否存在并已删除。"""
        ...

    @abstractmethod
    def get_rule(self, rule_id: str) -> Rule | None:
        """按 ID 查询规则。"""
        ...

    @abstractmethod
    def list_rules(
        self,
        priority: RulePriority | None = None,
        source: str | None = None,
        enabled_only: bool = True,
    ) -> list[Rule]:
        """列出规则，支持按优先级和来源过滤。"""
        ...

    @abstractmethod
    def evaluate(
        self,
        proposal_params: dict[str, Any],
        active_constraints: list[dict],
        world_state_snapshot: dict[str, Any],
    ) -> list[RuleEvalResult]:
        """
        评估提案是否违反规则。

        返回所有匹配的规则评估结果，按 priority 升序排列（数值越小优先级越高）。
        PolicyArbiter 根据最高优先级的匹配规则做出最终裁决。

        优先级映射：
        - P1-P4（safety ~ device_protection）→ 确定性规则，不引入 LLM
        - P5-P7（energy ~ experience）→ 可选 LLM 辅助打分
        """
        ...

    @abstractmethod
    def update_rule(self, rule_id: str, updates: dict[str, Any]) -> bool:
        """部分更新规则字段。返回是否存在并已更新。"""
        ...
```

---

### 4.3 MetricsCollector

**层级归属**：运行时层（跨层采集）

**采集指标**（对应架构文档 20.3）：

| 指标名 | 类型 | 来源 | 采集时机 |
|--------|------|------|---------|
| task_success_rate | gauge | TaskBoard | 任务完成时 |
| decision_latency_ms | histogram | PolicyArbiter | 裁决完成时 |
| execution_latency_ms | histogram | ActionExecutor | 动作执行完成时 |
| safety_violation_count | counter | PolicyArbiter | 安全规则拒绝时 |
| energy_consumption_wh | gauge | WorldState | 每个仿真步 |
| comfort_score | gauge | UserContext | 约束评估时 |
| conflict_resolution_rate | gauge | PolicyArbiter | 冲突消解时 |
| fault_recovery_time_ms | histogram | FaultDiagnosis | Incident 解决时 |
| invalid_action_count | counter | ActionExecutor | 动作验证失败时 |
| llm_cost_per_scenario | counter | AgentCore | LLM 调用完成时 |

```python
from abc import ABC, abstractmethod
from pydantic import BaseModel, Field
from enum import Enum
from typing import Any


class MetricType(str, Enum):
    COUNTER = "counter"      # 只增不减
    GAUGE = "gauge"          # 可增可减
    HISTOGRAM = "histogram"  # 分布统计


class MetricPoint(BaseModel):
    """单个指标数据点。"""
    name: str
    metric_type: MetricType
    value: float
    labels: dict[str, str] = Field(default_factory=dict)
    session_id: str
    sim_time: float
    trace_id: str | None = None


class MetricQuery(BaseModel):
    """指标查询条件。"""
    name: str
    session_id: str
    sim_time_start: float | None = None
    sim_time_end: float | None = None
    labels: dict[str, str] | None = None


class MetricSummary(BaseModel):
    """指标聚合结果。"""
    name: str
    count: int
    sum: float
    avg: float
    min: float
    max: float
    p50: float | None = None
    p95: float | None = None
    p99: float | None = None


class IMetricsCollector(ABC):
    """指标采集器核心接口。"""

    @abstractmethod
    def increment(
        self,
        name: str,
        value: float = 1.0,
        labels: dict[str, str] | None = None,
        session_id: str = "",
        sim_time: float = 0.0,
        trace_id: str | None = None,
    ) -> None:
        """递增计数器。"""
        ...

    @abstractmethod
    def set_gauge(
        self,
        name: str,
        value: float,
        labels: dict[str, str] | None = None,
        session_id: str = "",
        sim_time: float = 0.0,
        trace_id: str | None = None,
    ) -> None:
        """设置仪表值。"""
        ...

    @abstractmethod
    def observe(
        self,
        name: str,
        value: float,
        labels: dict[str, str] | None = None,
        session_id: str = "",
        sim_time: float = 0.0,
        trace_id: str | None = None,
    ) -> None:
        """记录直方图观测值（如延迟）。"""
        ...

    @abstractmethod
    def query(self, query: MetricQuery) -> list[MetricPoint]:
        """
        查询指标数据点。

        存储方式：内存缓冲 + 定期刷写到 SQLite。
        - 实时查询从内存缓冲读取
        - 历史查询从 SQLite 读取
        - 缓冲区大小默认 10000 条，超出后自动刷写
        """
        ...

    @abstractmethod
    def summarize(self, query: MetricQuery) -> MetricSummary:
        """聚合查询，返回统计摘要。"""
        ...

    @abstractmethod
    def flush(self) -> int:
        """将内存缓冲刷写到持久层，返回刷写条数。"""
        ...
```

---

### 4.4 ConstraintStore

**层级归属**：控制平面（PolicyArbiter 依赖）

**核心职责**：
- 存储所有 Agent 发布的 ConstraintContext
- 按维度和优先级查询有效约束
- 自动过期清理（基于 valid_until_sim_time）
- 为 PolicyArbiter 提供当前活跃约束集

```python
from abc import ABC, abstractmethod
from pydantic import BaseModel, Field
from typing import Any


class ConstraintContext(BaseModel):
    """标准化约束对象（与架构文档 10.5 对齐）。"""
    constraint_id: str
    source_agent: str
    constraint_type: str = Field(description="range | forbidden | required")
    dimension: str = Field(description="设备属性路径，如 'lighting.brightness'")
    value: Any = Field(description="约束值: {'min': x, 'max': y} | bool | specific_value")
    priority: int = Field(ge=1, le=7, description="1=安全 ... 7=体验")
    valid_until_sim_time: float = Field(description="过期仿真时间戳")
    reason: str
    session_id: str


class IConstraintStore(ABC):
    """约束存储核心接口。"""

    @abstractmethod
    def put(self, constraint: ConstraintContext) -> None:
        """
        存储或更新约束。

        如果 constraint_id 已存在，执行覆盖更新。
        写入后自动通过 EventBus 发布 constraint.updated 事件。
        """
        ...

    @abstractmethod
    def get(self, constraint_id: str) -> ConstraintContext | None:
        """按 ID 查询约束。"""
        ...

    @abstractmethod
    def query_active(
        self,
        session_id: str,
        sim_time: float,
        dimension: str | None = None,
        max_priority: int | None = None,
    ) -> list[ConstraintContext]:
        """
        查询当前有效的约束集。

        过滤条件：
        1. valid_until_sim_time > sim_time（未过期）
        2. dimension 匹配（如果指定）
        3. priority <= max_priority（如果指定）

        返回结果按 priority 升序排列（数值越小优先级越高）。
        PolicyArbiter 在评估提案前调用此方法获取活跃约束。
        """
        ...

    @abstractmethod
    def remove(self, constraint_id: str) -> bool:
        """手动移除约束。返回是否存在并已移除。"""
        ...

    @abstractmethod
    def cleanup_expired(self, session_id: str, sim_time: float) -> int:
        """
        清理过期约束。

        删除所有 valid_until_sim_time <= sim_time 的约束。
        由 EventScheduler 在每次时间推进后自动调用。
        返回清理的约束数量。
        """
        ...

    @abstractmethod
    def list_by_agent(self, agent_id: str, session_id: str) -> list[ConstraintContext]:
        """查询某 Agent 发布的所有约束（含过期），用于调试和审计。"""
        ...
```

---

## 5. 精简后的完整组件清单

### 按层级汇总

```
展示层
  └── Vue 3 + TresJS/Three.js 3D UI          [现有·冻结至 Phase 5]

API 层
  └── FastAPI + WebSocket                      [自建·Phase 1 起]

控制平面（Control Plane）
  ├── Orchestrator                             [自建]  核心协调器
  ├── AgentRegistry                            [自建]  manifest 注册 + 心跳治理
  ├── TaskBoard                                [自建 + SQLAlchemy 持久层]  任务 CRUD + CAS 乐观锁
  ├── ProtocolManager                          [transitions 复用]  3 种协议状态机
  ├── PolicyArbiter                            [自建]  仲裁入口
  │    ├── RuleEngine                          [自建·内部组件]  确定性规则评估
  │    └── Energy Module                       [自建·内部模块]  能耗预算规则(梯队三)
  ├── ConstraintStore                          [自建]  约束存储/查询/过期清理
  ├── SessionManager                           [自建]  仿真 session 隔离
  └── Scheduler                                [APScheduler 复用]  定时触发

运行时（Runtime）
  ├── AgentCore                                [自建]  统一 Agent 循环
  ├── ToolRegistry                             [自建]  工具注册 + 权限
  ├── ContextManager                           [自建 + tiktoken]  压缩 + 身份重注入
  ├── EventBus                                 [pyee 复用]  pub/sub + mailbox
  ├── LLM Client                               [LiteLLM + instructor 复用]  统一模型调用
  ├── MetricsCollector                         [自建]  指标采集 + 聚合查询
  ├── BackgroundJobManager                     [asyncio.TaskGroup]  后台任务
  └── EventLog                                 [structlog + JSONL]  结构化事件日志

仿真层（Simulation）
  ├── SimulationWorld (WorldState+EventScheduler) [SimPy 复用]  世界状态 + 事件调度
  ├── ActionExecutor                           [自建]  动作执行 + 验证
  ├── ScenarioRunner                           [自建]  场景脚本执行
  └── ReplayEngine                             [延迟至 Phase 5]  事件回放
```

### 删除/合并的组件

| 原组件 | 处理 | 说明 |
|--------|------|------|
| SkillLoader | **删除** | 前期 Agent 少，技能硬编码；需要时用 importlib |
| MailboxAdapter | **合并入 EventBus** | point-to-point 投递作为 EventBus 的 mailbox.{agent_id} 主题 |

### 统计总览

| 类别 | 数量 | 说明 |
|------|------|------|
| 自建 | 12 | AgentCore, AgentRegistry, PolicyArbiter, RuleEngine, Energy Module, ConstraintStore, SessionManager, ToolRegistry, ContextManager(部分), MetricsCollector, ActionExecutor, ScenarioRunner |
| 复用 | 9 | SQLAlchemy, transitions, APScheduler, tiktoken, pyee, LiteLLM, instructor, SimPy, structlog |
| asyncio 标准库 | 1 | TaskGroup 替代 BackgroundJobManager |
| 延迟 | 1 | ReplayEngine |
| 删除/合并 | 2 | SkillLoader, MailboxAdapter |

---

## 6. 依赖清单（requirements.txt 预览）

```txt
# 仿真
simpy>=4.1

# 状态机
transitions>=0.9

# LLM
litellm>=1.40
instructor>=1.3

# 数据库
sqlalchemy>=2.0

# 事件总线
pyee>=12.0

# 上下文管理
tiktoken>=0.7

# 日志
structlog>=24.0

# 定时调度
apscheduler>=3.10

# API
fastapi>=0.111
uvicorn>=0.30
websockets>=12.0

# 数据模型
pydantic>=2.7

# 开发
pytest>=8.0
pytest-asyncio>=0.23
```
