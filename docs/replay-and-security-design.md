# 确定性回放机制与安全增强方案

> 历史说明  
> 当前状态：历史讨论稿，不作为当前实现依据。  
> 当前唯一主设计稿：`docs/floorplan-to-3d-minimum-implementation.md`

版本：v1.0
日期：2026-03-24
状态：补充设计稿（针对架构评审缺陷 #1 #2 #3 修补）

---

## 1. 设计背景

原始架构文档声称系统"可回放"，但未解决以下关键问题：

1. **LLM 响应不确定性**：相同输入调用 LLM 会得到不同输出，无法实现确定性回放。
2. **组合安全分析缺失**：`PolicyArbiter` 逐提案仲裁，无法检测并发提案组合产生的不安全状态。
3. **WorldState 缺技术级写屏障**："不可绕过仲裁层"是架构纪律而非技术强制。
4. **trace_id 存在 4 个传播盲区**。

本文档为上述缺陷提供完整技术方案。

---

## 2. LLM Record/Replay Proxy 完整设计

### 2.1 SimulationMode 枚举

```python
from enum import Enum

class SimulationMode(Enum):
    """仿真运行模式"""
    LIVE = "live"        # 正常调用 LLM，同时录制请求/响应到缓存
    REPLAY = "replay"    # 从缓存读取 LLM 响应，跳过实际 API 调用
    DRYRUN = "dryrun"    # 使用 Mock 响应，不调用 LLM 也不读缓存
```

模式切换通过 `SessionManager` 在创建 session 时指定，运行期间不可变更：

```python
session = SessionManager.create(
    scenario="sleep_mode",
    mode=SimulationMode.REPLAY,
    replay_source="session_20260312_01",  # REPLAY 模式必填：源 session_id
)
```

### 2.2 LLMResponseCache 数据模型

#### 2.2.1 缓存键设计

缓存键 = 对 LLM 请求的**规范化输入**取 SHA-256 哈希。规范化过程剔除易变字段（时间戳、request_id），保留语义决定性字段：

```python
import hashlib
import json
from pydantic import BaseModel, Field
from typing import Any


class LLMCacheKey(BaseModel):
    """缓存键的规范化输入，用于生成稳定的 input_hash"""
    agent_id: str
    model_name: str
    messages: list[dict[str, Any]]       # 完整 messages 数组
    tools: list[dict[str, Any]] | None = None  # 工具定义列表
    temperature: float = 0.0
    max_tokens: int | None = None

    def to_hash(self) -> str:
        """生成稳定的 SHA-256 哈希。

        关键：使用 sort_keys + ensure_ascii 保证序列化确定性。
        messages 中的 sim_time、timestamp 等易变字段在构造前由调用方剔除。
        """
        canonical = json.dumps(
            self.model_dump(exclude_none=True),
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
```

**易变字段剔除规则**：

| 字段 | 处理方式 |
|------|----------|
| `sim_time` | 保留（是决策语义的一部分） |
| `timestamp`（wall clock） | 剔除 |
| `request_id` / `message_id` | 剔除 |
| `session_id` | 剔除（同一场景不同 session 应能复用缓存） |
| `trace_id` | 剔除 |

#### 2.2.2 缓存值结构

```python
from datetime import datetime


class LLMCacheEntry(BaseModel):
    """一条完整的 LLM 请求/响应缓存记录"""
    input_hash: str                          # SHA-256 of LLMCacheKey
    session_id: str                          # 录制时的 session_id
    agent_id: str
    sequence_number: int                     # 该 agent 在本 session 中的第 N 次 LLM 调用
    request: dict[str, Any]                  # 完整 LLM 请求体（含 messages, tools, params）
    response: dict[str, Any]                 # 完整 LLM 响应体（含 choices, usage）
    model_name: str
    input_tokens: int
    output_tokens: int
    latency_ms: float                        # 实际 API 耗时
    recorded_at: datetime                    # wall clock 录制时间
    sim_time: float                          # 录制时的仿真时间
```

#### 2.2.3 存储方式

**主存储：SQLite（结构化查询 + 事务安全）**

```sql
CREATE TABLE llm_cache (
    input_hash     TEXT NOT NULL,
    session_id     TEXT NOT NULL,
    agent_id       TEXT NOT NULL,
    sequence_number INTEGER NOT NULL,
    request        TEXT NOT NULL,    -- JSON
    response       TEXT NOT NULL,    -- JSON
    model_name     TEXT NOT NULL,
    input_tokens   INTEGER,
    output_tokens  INTEGER,
    latency_ms     REAL,
    recorded_at    TEXT NOT NULL,
    sim_time       REAL NOT NULL,
    PRIMARY KEY (session_id, agent_id, sequence_number)
);

CREATE INDEX idx_cache_hash ON llm_cache(input_hash);
CREATE INDEX idx_cache_session ON llm_cache(session_id, agent_id);
```

**辅助存储：JSONL（流式追加，便于导出和外部工具分析）**

每行一个 `LLMCacheEntry` 的 JSON 序列化，文件名 = `{session_id}_llm_cache.jsonl`。

#### 2.2.4 缓存命中策略

| 模式 | 命中策略 |
|------|----------|
| **精确匹配**（默认） | 按 `(session_id, agent_id, sequence_number)` 查找，要求回放 session 与录制 session 的调用顺序完全一致 |
| **哈希匹配**（降级） | 按 `input_hash` 查找，允许调用顺序不同但输入相同的情况复用缓存 |

精确匹配失败时自动降级到哈希匹配，哈希匹配也失败时抛出 `ReplayCacheMiss` 异常，由上层决定是否终止回放或切换到 LIVE 模式。

```python
class ReplayCacheMiss(Exception):
    """回放模式下缓存未命中"""
    def __init__(self, agent_id: str, sequence_number: int, input_hash: str):
        self.agent_id = agent_id
        self.sequence_number = sequence_number
        self.input_hash = input_hash
        super().__init__(
            f"Cache miss: agent={agent_id} seq={sequence_number} hash={input_hash[:16]}..."
        )
```

### 2.3 RecordReplayProxy 核心实现

```python
from typing import Protocol


class LLMClient(Protocol):
    """LLM 客户端接口"""
    async def chat_completion(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        model: str = "default",
        temperature: float = 0.0,
        max_tokens: int | None = None,
    ) -> dict[str, Any]: ...


class RecordReplayProxy:
    """LLM 调用的录制/回放代理。

    透明包装 LLMClient，根据 SimulationMode 切换行为：
    - LIVE：调用真实 LLM + 写入缓存
    - REPLAY：从缓存读取，不调用 LLM
    - DRYRUN：返回 Mock 响应
    """

    def __init__(
        self,
        real_client: LLMClient,
        cache_store: "LLMCacheStore",
        mode: SimulationMode,
        session_id: str,
        replay_source_session_id: str | None = None,
    ):
        self._real_client = real_client
        self._cache = cache_store
        self._mode = mode
        self._session_id = session_id
        self._replay_source = replay_source_session_id
        # 每个 agent_id -> 当前 sequence_number 的计数器
        self._sequence_counters: dict[str, int] = {}

    def _next_sequence(self, agent_id: str) -> int:
        count = self._sequence_counters.get(agent_id, 0)
        self._sequence_counters[agent_id] = count + 1
        return count

    async def chat_completion(
        self,
        agent_id: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        model: str = "default",
        temperature: float = 0.0,
        max_tokens: int | None = None,
        sim_time: float = 0.0,
    ) -> dict[str, Any]:
        """统一入口：根据模式分派到 _live / _replay / _dryrun"""
        seq = self._next_sequence(agent_id)
        cache_key = LLMCacheKey(
            agent_id=agent_id,
            model_name=model,
            messages=self._strip_volatile(messages),
            tools=tools,
            temperature=temperature,
            max_tokens=max_tokens,
        )

        if self._mode == SimulationMode.LIVE:
            return await self._live(agent_id, seq, cache_key, messages, tools, model, temperature, max_tokens, sim_time)
        elif self._mode == SimulationMode.REPLAY:
            return await self._replay(agent_id, seq, cache_key)
        else:  # DRYRUN
            return self._dryrun(agent_id, seq, cache_key)

    async def _live(
        self, agent_id: str, seq: int, cache_key: LLMCacheKey,
        messages, tools, model, temperature, max_tokens, sim_time,
    ) -> dict[str, Any]:
        """LIVE 模式：调用真实 LLM，录制响应"""
        import time
        start = time.monotonic()
        response = await self._real_client.chat_completion(
            messages=messages, tools=tools, model=model,
            temperature=temperature, max_tokens=max_tokens,
        )
        latency_ms = (time.monotonic() - start) * 1000

        entry = LLMCacheEntry(
            input_hash=cache_key.to_hash(),
            session_id=self._session_id,
            agent_id=agent_id,
            sequence_number=seq,
            request=cache_key.model_dump(),
            response=response,
            model_name=model,
            input_tokens=response.get("usage", {}).get("prompt_tokens", 0),
            output_tokens=response.get("usage", {}).get("completion_tokens", 0),
            latency_ms=latency_ms,
            recorded_at=datetime.utcnow(),
            sim_time=sim_time,
        )
        await self._cache.write(entry)
        return response

    async def _replay(self, agent_id: str, seq: int, cache_key: LLMCacheKey) -> dict[str, Any]:
        """REPLAY 模式：从缓存读取录制的响应"""
        source = self._replay_source or self._session_id
        # 策略 1：精确匹配 (session_id, agent_id, sequence_number)
        entry = await self._cache.read_exact(source, agent_id, seq)
        if entry is not None:
            return entry.response

        # 策略 2：哈希匹配降级
        entry = await self._cache.read_by_hash(source, cache_key.to_hash())
        if entry is not None:
            return entry.response

        raise ReplayCacheMiss(agent_id, seq, cache_key.to_hash())

    def _dryrun(self, agent_id: str, seq: int, cache_key: LLMCacheKey) -> dict[str, Any]:
        """DRYRUN 模式：返回 Mock 响应（无操作提案）"""
        return {
            "choices": [{
                "message": {
                    "role": "assistant",
                    "content": f"[DRYRUN] Agent {agent_id} seq={seq}: no action proposed.",
                    "tool_calls": [],
                }
            }],
            "usage": {"prompt_tokens": 0, "completion_tokens": 0},
            "model": "dryrun-mock",
        }

    @staticmethod
    def _strip_volatile(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """剔除 messages 中的易变字段，保留语义字段"""
        VOLATILE_KEYS = {"timestamp", "request_id", "message_id", "session_id", "trace_id"}
        stripped = []
        for msg in messages:
            clean = {}
            for k, v in msg.items():
                if k in VOLATILE_KEYS:
                    continue
                if isinstance(v, dict):
                    clean[k] = {kk: vv for kk, vv in v.items() if kk not in VOLATILE_KEYS}
                else:
                    clean[k] = v
            stripped.append(clean)
        return stripped
```

#### LLMCacheStore 存储接口

```python
class LLMCacheStore:
    """LLM 缓存的存储层（SQLite 实现）"""

    def __init__(self, db_path: str):
        self._db_path = db_path

    async def write(self, entry: LLMCacheEntry) -> None:
        """写入一条缓存记录（INSERT OR REPLACE）"""
        ...

    async def read_exact(
        self, session_id: str, agent_id: str, sequence_number: int
    ) -> LLMCacheEntry | None:
        """精确匹配：按 (session_id, agent_id, sequence_number) 查找"""
        ...

    async def read_by_hash(
        self, session_id: str, input_hash: str
    ) -> LLMCacheEntry | None:
        """哈希匹配降级：按 (session_id, input_hash) 查找第一条匹配"""
        ...

    async def list_by_session(self, session_id: str) -> list[LLMCacheEntry]:
        """列出指定 session 的所有缓存记录，按 sequence_number 排序"""
        ...
```

### 2.4 对 AgentCore 循环的影响

只有 `think()` 步骤受影响，其余步骤（perceive / act / observe / compress / idle_claim）不受影响：

```python
class AgentCore:
    """AgentCore 最小循环（仅展示 think 步骤的模式切换）"""

    def __init__(
        self,
        agent_id: str,
        llm_proxy: RecordReplayProxy,  # 注入代理而非原始 LLM 客户端
        tool_registry: "ToolRegistry",
        context_manager: "ContextManager",
        # ...
    ):
        self._agent_id = agent_id
        self._llm = llm_proxy  # 关键：使用 proxy 替代 real client
        self._tools = tool_registry
        self._ctx = context_manager

    async def think(self, sim_time: float) -> dict[str, Any]:
        """调用 LLM 进行推理。

        通过 RecordReplayProxy 透明切换：
        - LIVE: 真实调用 + 录制
        - REPLAY: 从缓存读取
        - DRYRUN: Mock 响应

        AgentCore 无需感知当前模式，模式切换完全由 proxy 封装。
        """
        messages = self._ctx.build_messages()
        tools = self._tools.get_tool_definitions(self._agent_id)

        response = await self._llm.chat_completion(
            agent_id=self._agent_id,
            messages=messages,
            tools=tools,
            model=self._model_name,
            temperature=0.0,   # 录制时建议 temperature=0 提高可复现性
            sim_time=sim_time,
        )
        return response

    async def run_loop(self, sim_time: float):
        """完整的 agent 循环 — perceive/act/observe 不受模式影响"""
        await self.perceive()           # 不受影响：从 Mailbox + EventBus 读取
        self.inject_context(sim_time)   # 不受影响：注入 WorldState 摘要
        llm_response = await self.think(sim_time)  # 唯一受影响的步骤
        await self.act(llm_response)    # 不受影响：解析输出，调用工具
        await self.observe()            # 不受影响：收集结果，写入 EventLog
        self.compress_if_needed()       # 不受影响
        await self.idle_claim()         # 不受影响
```

**设计要点**：

- `AgentCore` 持有的是 `RecordReplayProxy` 而非原始 `LLMClient`，模式切换对 agent 代码完全透明。
- `temperature=0.0` 是录制时的推荐值，降低 LLM 输出随机性（但不保证完全一致，所以缓存仍然必须）。
- `perceive()` / `act()` / `observe()` 在 REPLAY 模式下仍然正常运行——它们操作的是 WorldState、EventBus、ToolRegistry，这些组件在回放时由 ReplayEngine 驱动。

---

## 3. 两种回放模式设计

### 3.1 状态回放（State Replay）

**目的**：快速重现仿真结果，用于 UI 展示和结果复查。不重现决策过程。

```python
from pydantic import BaseModel, Field


class StateChange(BaseModel):
    """WorldState 的一次原子变更记录"""
    change_id: str
    session_id: str
    sim_time: float
    device_id: str
    property_path: str        # 如 "bedroom_light_1.brightness"
    old_value: Any
    new_value: Any
    caused_by: str            # action_id 或 event_id
    trace_id: str


class StateReplayEngine:
    """状态回放引擎：按 sim_time 顺序重放 WorldState 变更序列。

    特性：
    - 不调用 LLM
    - 不重建 AgentCore 循环
    - 不重现决策过程
    - 速度快（仅受 I/O 和 WorldState 写入速度限制）

    适用场景：UI 时间轴展示、结果复查、前端回放控制器
    """

    def __init__(self, world_state: "WorldState", event_log: "EventLog"):
        self._ws = world_state
        self._log = event_log

    async def load_changes(self, session_id: str) -> list[StateChange]:
        """从 EventLog 加载指定 session 的所有 StateChange，按 sim_time 排序"""
        raw_events = await self._log.query(
            session_id=session_id,
            event_type="state.changed",
            order_by="sim_time",
        )
        return [StateChange.model_validate(e.payload) for e in raw_events]

    async def replay(
        self,
        session_id: str,
        speed: float = 1.0,         # 回放速度倍率
        start_time: float | None = None,
        end_time: float | None = None,
        on_change: "Callable[[StateChange], Awaitable[None]] | None" = None,
    ) -> None:
        """执行状态回放。

        按 sim_time 顺序逐条应用 StateChange 到 WorldState。
        支持速度倍率、时间范围过滤和变更回调（用于推送 WebSocket 事件）。
        """
        changes = await self.load_changes(session_id)

        for change in changes:
            if start_time is not None and change.sim_time < start_time:
                continue
            if end_time is not None and change.sim_time > end_time:
                break

            # 应用状态变更
            self._ws.apply_change(
                device_id=change.device_id,
                property_path=change.property_path,
                value=change.new_value,
            )

            # 通知订阅者（如 WebSocket 推送）
            if on_change:
                await on_change(change)

    async def seek(self, session_id: str, target_time: float) -> None:
        """跳转到指定仿真时间点，批量应用该时间点之前的所有变更"""
        changes = await self.load_changes(session_id)
        self._ws.reset()
        for change in changes:
            if change.sim_time > target_time:
                break
            self._ws.apply_change(
                device_id=change.device_id,
                property_path=change.property_path,
                value=change.new_value,
            )
```

### 3.2 全真回放（Full Replay）

**目的**：重现完整的多智能体决策链，用于决策审计、调试和评测。

```python
class FullReplayEngine:
    """全真回放引擎：重建 AgentCore 循环，用缓存 LLM 响应替代实际 API 调用。

    特性：
    - 重建完整 AgentCore 循环（perceive → think → act → observe）
    - think() 步骤使用 RecordReplayProxy(REPLAY) 从缓存读取 LLM 响应
    - 其他步骤正常执行（WorldState、EventBus、ToolRegistry 均真实运行）
    - 重现完整决策链和协议交互
    - 速度受限于 WorldState 更新和协议处理（但远快于实际 LLM 调用）

    适用场景：决策审计、调试、评测基准对比
    """

    def __init__(
        self,
        session_manager: "SessionManager",
        agent_registry: "AgentRegistry",
        cache_store: LLMCacheStore,
        real_client: LLMClient,  # 缓存未命中时的降级客户端
    ):
        self._session_mgr = session_manager
        self._registry = agent_registry
        self._cache = cache_store
        self._real_client = real_client

    async def replay(
        self,
        source_session_id: str,
        on_cache_miss: str = "raise",  # "raise" | "fallback_live" | "skip"
    ) -> "ReplayResult":
        """执行全真回放。

        Args:
            source_session_id: 要回放的源 session ID
            on_cache_miss: 缓存未命中策略
                - "raise": 抛出 ReplayCacheMiss 终止回放
                - "fallback_live": 降级为实际 LLM 调用（结果可能不同）
                - "skip": 跳过该步骤，使用 DRYRUN mock

        Returns:
            ReplayResult 包含回放统计和偏差报告
        """
        # 1. 创建新的回放 session，复制源 session 的初始配置
        replay_session = await self._session_mgr.create(
            scenario=await self._get_source_scenario(source_session_id),
            mode=SimulationMode.REPLAY,
            replay_source=source_session_id,
        )

        # 2. 为每个 agent 创建 RecordReplayProxy(REPLAY)
        agents = await self._registry.list_agents(source_session_id)
        proxied_agents: dict[str, AgentCore] = {}
        for agent_manifest in agents:
            proxy = RecordReplayProxy(
                real_client=self._real_client,
                cache_store=self._cache,
                mode=SimulationMode.REPLAY,
                session_id=replay_session.session_id,
                replay_source_session_id=source_session_id,
            )
            core = AgentCore(
                agent_id=agent_manifest.agent_id,
                llm_proxy=proxy,
                tool_registry=replay_session.tool_registry,
                context_manager=replay_session.context_manager,
            )
            proxied_agents[agent_manifest.agent_id] = core

        # 3. 按源 session 的事件时间线驱动回放
        source_events = await self._cache.list_by_session(source_session_id)
        result = ReplayResult(source_session_id=source_session_id)

        for event in source_events:
            agent = proxied_agents.get(event.agent_id)
            if agent is None:
                continue
            try:
                await agent.run_loop(sim_time=event.sim_time)
                result.steps_replayed += 1
            except ReplayCacheMiss as e:
                result.cache_misses.append(e)
                if on_cache_miss == "raise":
                    raise
                elif on_cache_miss == "fallback_live":
                    # 降级为 LIVE 调用，标记偏差
                    result.diverged_steps.append(event.sequence_number)
                # "skip" 则直接跳过

        return result


class ReplayResult(BaseModel):
    """全真回放的结果报告"""
    source_session_id: str
    steps_replayed: int = 0
    cache_misses: list[Any] = Field(default_factory=list)
    diverged_steps: list[int] = Field(default_factory=list)
    state_diff: dict[str, Any] | None = None  # 回放后与源 session 最终状态的差异
```

### 3.3 两种模式对比

| 维度 | 状态回放 | 全真回放 |
|------|----------|----------|
| 数据源 | WorldState 变化事件序列 | EventLog + LLM 缓存 |
| LLM 调用 | 无 | 从缓存读取 |
| 决策过程 | 不重现 | 完整重现 |
| 速度 | 极快（毫秒级/步） | 较快（省去 LLM 延迟，但需执行完整循环） |
| 确定性 | 完全确定 | 依赖缓存完整性 |
| 适用场景 | UI 展示、结果复查 | 决策审计、调试、评测 |

---

## 4. 原子仲裁窗口设计（组合安全分析）

### 4.1 问题描述

当前 `PolicyArbiter` 逐提案仲裁，存在以下风险：

- **Lighting Agent** 提案：关闭卧室灯
- **Security Agent** 提案：关闭卧室门锁
- 两个提案分别通过安全校验，但组合效果 = **卧室全黑且门锁失效**，构成安全隐患

### 4.2 核心机制

```python
from enum import Enum


class ArbitrationWindowState(Enum):
    COLLECTING = "collecting"  # 收集窗口期，接受提案
    ANALYZING = "analyzing"    # 分析窗口期，执行组合安全检查
    DISPATCHING = "dispatching"  # 下发窗口期，批量下发仲裁结果


class ArbitrationWindow(BaseModel):
    """原子仲裁窗口：与仿真 tick 对齐"""
    window_id: str
    session_id: str
    tick_number: int              # 对齐的仿真 tick
    sim_time_start: float
    sim_time_end: float
    state: ArbitrationWindowState = ArbitrationWindowState.COLLECTING
    proposals: list["ActionProposal"] = Field(default_factory=list)
    decisions: list["PolicyDecision"] = Field(default_factory=list)


class CompositionRule(BaseModel):
    """组合安全规则"""
    rule_id: str
    description: str
    severity: int                  # 1=critical 2=warning 3=info
    check_fn_name: str             # 对应的检查函数名


class AtomicArbiter:
    """原子仲裁器：在仲裁窗口内收集所有提案，执行组合安全分析后批量下发。

    时间粒度：与仿真 tick 对齐。每个 tick 结束时：
    1. 关闭收集窗口
    2. 对窗口内所有提案执行组合安全分析
    3. 批量下发仲裁结果
    4. 开启下一个窗口
    """

    # ---------- 组合安全规则 ----------

    COMPOSITION_RULES: list[CompositionRule] = [
        CompositionRule(
            rule_id="CR-001",
            description="同一房间不可同时全灭灯 + 解锁门",
            severity=1,
            check_fn_name="_check_dark_room_unlocked",
        ),
        CompositionRule(
            rule_id="CR-002",
            description="总功率不可超过电路额定容量",
            severity=1,
            check_fn_name="_check_power_overload",
        ),
        CompositionRule(
            rule_id="CR-003",
            description="温度设定与窗户状态冲突（空调制热 + 窗户开启）",
            severity=2,
            check_fn_name="_check_hvac_window_conflict",
        ),
        CompositionRule(
            rule_id="CR-004",
            description="安防模式激活时不可解锁任何门",
            severity=1,
            check_fn_name="_check_security_mode_unlock",
        ),
        CompositionRule(
            rule_id="CR-005",
            description="同一设备在同一窗口内收到矛盾指令",
            severity=2,
            check_fn_name="_check_contradictory_commands",
        ),
    ]

    def __init__(
        self,
        policy_arbiter: "PolicyArbiter",
        world_state: "WorldState",
    ):
        self._policy = policy_arbiter
        self._ws = world_state
        self._current_window: ArbitrationWindow | None = None

    def open_window(self, tick_number: int, sim_time: float) -> ArbitrationWindow:
        """在 tick 开始时打开新的仲裁窗口"""
        self._current_window = ArbitrationWindow(
            window_id=f"aw_{tick_number}",
            session_id=self._ws.session_id,
            tick_number=tick_number,
            sim_time_start=sim_time,
            sim_time_end=sim_time,  # tick 结束时更新
            state=ArbitrationWindowState.COLLECTING,
        )
        return self._current_window

    def submit_proposal(self, proposal: "ActionProposal") -> None:
        """在收集窗口期内提交提案（缓冲而非立即仲裁）"""
        if self._current_window is None:
            raise RuntimeError("No arbitration window open")
        if self._current_window.state != ArbitrationWindowState.COLLECTING:
            raise RuntimeError(f"Window is in {self._current_window.state} state, not collecting")
        self._current_window.proposals.append(proposal)

    async def close_and_arbitrate(self) -> list["PolicyDecision"]:
        """关闭收集窗口，执行组合安全分析 + 逐提案仲裁，返回仲裁结果。

        流程：
        1. 执行组合安全分析，标记冲突提案
        2. 对非冲突提案逐一执行 PolicyArbiter 常规仲裁
        3. 对冲突提案按优先级择优或全部拒绝
        4. 批量下发结果
        """
        window = self._current_window
        if window is None:
            return []

        window.state = ArbitrationWindowState.ANALYZING
        proposals = window.proposals

        # --- 第一步：组合安全分析 ---
        conflicts: list[tuple[str, list["ActionProposal"]]] = []
        for rule in self.COMPOSITION_RULES:
            check_fn = getattr(self, rule.check_fn_name)
            conflicting = check_fn(proposals, self._ws)
            if conflicting:
                conflicts.append((rule.rule_id, conflicting))

        # 标记冲突提案
        conflicted_ids: set[str] = set()
        for rule_id, conflicting_proposals in conflicts:
            for p in conflicting_proposals:
                conflicted_ids.add(p.proposal_id)

        # --- 第二步：逐提案仲裁（非冲突提案走常规流程）---
        decisions: list["PolicyDecision"] = []
        for proposal in proposals:
            if proposal.proposal_id in conflicted_ids:
                # 冲突提案：按优先级择优（保留高优先级，拒绝低优先级）
                decision = self._resolve_conflict(proposal, conflicts)
            else:
                # 常规仲裁
                decision = await self._policy.arbitrate(proposal, self._ws)
            decisions.append(decision)

        # --- 第三步：批量下发 ---
        window.state = ArbitrationWindowState.DISPATCHING
        window.decisions = decisions
        self._current_window = None
        return decisions

    # ---------- 组合安全检查函数 ----------

    def _check_dark_room_unlocked(
        self, proposals: list["ActionProposal"], ws: "WorldState"
    ) -> list["ActionProposal"] | None:
        """CR-001: 检测同一房间内同时关灯+解锁门"""
        room_light_off: dict[str, "ActionProposal"] = {}
        room_door_unlock: dict[str, "ActionProposal"] = {}

        for p in proposals:
            device = ws.get_device(p.device_id)
            if device is None:
                continue
            room_id = device.room_id
            if device.device_type == "light" and p.params.get("power") == "off":
                room_light_off[room_id] = p
            if device.device_type == "door_lock" and p.params.get("locked") is False:
                room_door_unlock[room_id] = p

        conflicting = []
        for room_id in set(room_light_off) & set(room_door_unlock):
            conflicting.extend([room_light_off[room_id], room_door_unlock[room_id]])
        return conflicting or None

    def _check_power_overload(
        self, proposals: list["ActionProposal"], ws: "WorldState"
    ) -> list["ActionProposal"] | None:
        """CR-002: 检测总功率超过电路额定容量"""
        total_delta = 0.0
        power_proposals = []
        for p in proposals:
            device = ws.get_device(p.device_id)
            if device is None:
                continue
            if p.params.get("power") == "on":
                total_delta += device.rated_power_watts
                power_proposals.append(p)

        current_load = ws.get_total_power_load()
        if current_load + total_delta > ws.get_circuit_capacity():
            return power_proposals
        return None

    def _check_hvac_window_conflict(
        self, proposals: list["ActionProposal"], ws: "WorldState"
    ) -> list["ActionProposal"] | None:
        """CR-003: 空调制热/制冷与窗户开启冲突"""
        hvac_proposals = []
        window_open_proposals = []
        for p in proposals:
            device = ws.get_device(p.device_id)
            if device is None:
                continue
            if device.device_type == "hvac" and p.params.get("mode") in ("heating", "cooling"):
                hvac_proposals.append(p)
            if device.device_type == "window" and p.params.get("open") is True:
                window_open_proposals.append(p)

        # 检查是否同一房间
        hvac_rooms = {ws.get_device(p.device_id).room_id for p in hvac_proposals if ws.get_device(p.device_id)}
        window_rooms = {ws.get_device(p.device_id).room_id for p in window_open_proposals if ws.get_device(p.device_id)}
        if hvac_rooms & window_rooms:
            return hvac_proposals + window_open_proposals
        return None

    def _check_security_mode_unlock(
        self, proposals: list["ActionProposal"], ws: "WorldState"
    ) -> list["ActionProposal"] | None:
        """CR-004: 安防模式激活时解锁门"""
        if not ws.is_security_armed():
            return None
        unlock_proposals = [
            p for p in proposals
            if ws.get_device(p.device_id) and ws.get_device(p.device_id).device_type == "door_lock"
            and p.params.get("locked") is False
        ]
        return unlock_proposals or None

    def _check_contradictory_commands(
        self, proposals: list["ActionProposal"], ws: "WorldState"
    ) -> list["ActionProposal"] | None:
        """CR-005: 同一设备在同一窗口内收到矛盾指令"""
        device_proposals: dict[str, list["ActionProposal"]] = {}
        for p in proposals:
            device_proposals.setdefault(p.device_id, []).append(p)

        contradictions = []
        for device_id, props in device_proposals.items():
            if len(props) > 1:
                # 检查是否存在矛盾（如一个开灯一个关灯）
                param_sets = [frozenset(p.params.items()) for p in props]
                if len(set(param_sets)) > 1:
                    contradictions.extend(props)
        return contradictions or None

    def _resolve_conflict(
        self, proposal: "ActionProposal", conflicts: list[tuple[str, list["ActionProposal"]]]
    ) -> "PolicyDecision":
        """解决冲突提案：查找该提案涉及的所有规则，按最高严重级别处理"""
        max_severity = 3
        triggered_rules = []
        for rule_id, conflicting in conflicts:
            if any(p.proposal_id == proposal.proposal_id for p in conflicting):
                rule = next(r for r in self.COMPOSITION_RULES if r.rule_id == rule_id)
                max_severity = min(max_severity, rule.severity)
                triggered_rules.append(rule_id)

        if max_severity == 1:
            return PolicyDecision(
                proposal_id=proposal.proposal_id,
                result="rejected",
                reason=f"组合安全冲突（规则：{', '.join(triggered_rules)}）",
            )
        else:
            return PolicyDecision(
                proposal_id=proposal.proposal_id,
                result="approved_with_modification",
                reason=f"组合安全警告（规则：{', '.join(triggered_rules)}），建议人工复核",
            )
```

### 4.3 与 PolicyArbiter 的集成

`AtomicArbiter` 作为 `PolicyArbiter` 的**前置包装器**（而非内部实现），在 `EventScheduler` 的 tick 推进逻辑中插入：

```python
class EventScheduler:
    """仿真事件调度器（仅展示与仲裁窗口的集成）"""

    def __init__(self, atomic_arbiter: AtomicArbiter):
        self._arbiter = atomic_arbiter

    async def advance_tick(self, tick_number: int, sim_time: float):
        """推进一个仿真 tick"""
        # 1. 打开仲裁窗口
        self._arbiter.open_window(tick_number, sim_time)

        # 2. 执行所有 agent 的 run_loop（期间提案通过 submit_proposal 缓冲）
        await self._run_all_agents(sim_time)

        # 3. 关闭窗口，执行组合安全分析 + 批量仲裁
        decisions = await self._arbiter.close_and_arbitrate()

        # 4. 执行已批准的动作
        for decision in decisions:
            if decision.result in ("approved", "approved_with_modification"):
                await self._action_executor.execute(decision)
```

**性能影响**：

- 组合安全分析的复杂度 = O(R * P)，R = 规则数量，P = 窗口内提案数量
- 典型场景下 R <= 10，P <= 20，分析耗时可忽略（< 1ms）
- 唯一额外延迟：提案在窗口期内被缓冲而非立即仲裁，延迟 = 1 个 tick 周期

---

## 5. WorldState 写入 ACL 设计

### 5.1 技术强制方案

#### 5.1.1 写入令牌机制

`WorldState` 的所有写入操作必须携带 `WriteToken`，只有 `ActionExecutor` 持有有效令牌：

```python
import secrets
from contextvars import ContextVar


class WriteToken(BaseModel):
    """WorldState 写入令牌"""
    token_id: str = Field(default_factory=lambda: secrets.token_hex(16))
    holder: str               # 持有者标识，如 "action_executor"
    session_id: str
    permissions: set[str]     # 允许的操作类型，如 {"device.set_state", "sensor.update"}
    issued_at: datetime
    expires_at: datetime | None = None

    def is_valid(self) -> bool:
        if self.expires_at and datetime.utcnow() > self.expires_at:
            return False
        return True


class WriteViolation(BaseModel):
    """违规写入记录"""
    violation_id: str
    session_id: str
    caller: str               # 尝试写入的模块/agent 标识
    operation: str             # 尝试的操作
    timestamp: datetime
    stack_trace: str           # 调用栈信息


# 用于在调用链中传递写入令牌的 ContextVar
_current_write_token: ContextVar[WriteToken | None] = ContextVar("_current_write_token", default=None)


class WorldState:
    """带写入 ACL 的 WorldState（仅展示写屏障部分）"""

    def __init__(self, session_id: str, violation_handler: "ViolationHandler"):
        self._session_id = session_id
        self._devices: dict[str, Any] = {}
        self._valid_tokens: dict[str, WriteToken] = {}
        self._violation_handler = violation_handler

    def issue_token(self, holder: str, permissions: set[str]) -> WriteToken:
        """签发写入令牌（仅限 SessionManager 在初始化时调用）"""
        token = WriteToken(
            holder=holder,
            session_id=self._session_id,
            permissions=permissions,
            issued_at=datetime.utcnow(),
        )
        self._valid_tokens[token.token_id] = token
        return token

    def revoke_token(self, token_id: str) -> None:
        """吊销写入令牌"""
        self._valid_tokens.pop(token_id, None)

    def _check_write_permission(self, operation: str, caller: str) -> None:
        """写屏障：检查当前上下文是否持有有效写入令牌"""
        token = _current_write_token.get()

        if token is None:
            self._report_violation(caller, operation, "No write token in context")
            raise PermissionError(f"WorldState write denied: no token (caller={caller}, op={operation})")

        if token.token_id not in self._valid_tokens:
            self._report_violation(caller, operation, "Token revoked or unknown")
            raise PermissionError(f"WorldState write denied: invalid token (caller={caller})")

        if not token.is_valid():
            self._report_violation(caller, operation, "Token expired")
            raise PermissionError(f"WorldState write denied: expired token (caller={caller})")

        if operation not in token.permissions:
            self._report_violation(caller, operation, f"Operation not in permissions: {token.permissions}")
            raise PermissionError(f"WorldState write denied: no permission for {operation}")

    def _report_violation(self, caller: str, operation: str, reason: str) -> None:
        """记录违规写入并触发告警"""
        import traceback
        violation = WriteViolation(
            violation_id=secrets.token_hex(8),
            session_id=self._session_id,
            caller=caller,
            operation=operation,
            timestamp=datetime.utcnow(),
            stack_trace=traceback.format_stack()[-5:],
        )
        self._violation_handler.handle(violation)

    # ---------- 写入操作（全部受保护）----------

    def set_device_state(self, device_id: str, property_path: str, value: Any) -> None:
        """设置设备状态（受写屏障保护）"""
        self._check_write_permission("device.set_state", caller=f"device:{device_id}")
        # 实际写入逻辑
        ...

    def update_sensor(self, sensor_id: str, reading: Any) -> None:
        """更新传感器读数（受写屏障保护）"""
        self._check_write_permission("sensor.update", caller=f"sensor:{sensor_id}")
        ...

    # ---------- 读取操作（无限制）----------

    def get_device(self, device_id: str) -> Any:
        """读取设备状态（无需令牌）"""
        return self._devices.get(device_id)

    def get_snapshot(self) -> dict[str, Any]:
        """获取当前完整状态快照（无需令牌）"""
        ...
```

#### 5.1.2 ActionExecutor 的写入令牌使用

```python
class ActionExecutor:
    """动作执行器：唯一持有 WorldState 写入令牌的组件"""

    def __init__(self, world_state: WorldState, write_token: WriteToken):
        self._ws = world_state
        self._token = write_token  # 在 SessionManager 初始化时注入

    async def execute(self, decision: "PolicyDecision") -> "ExecutionResult":
        """执行仲裁后的动作。

        通过 ContextVar 设置写入令牌，确保只有在此上下文中的写操作才有权限。
        """
        token_ctx = _current_write_token.set(self._token)
        try:
            proposal = decision.original_proposal
            self._ws.set_device_state(
                device_id=proposal.device_id,
                property_path=proposal.property_path,
                value=proposal.params,
            )
            return ExecutionResult(
                action_id=proposal.action_id,
                success=True,
            )
        finally:
            _current_write_token.reset(token_ctx)
```

### 5.2 ToolRegistry 安全分级

```python
class ToolSecurityLevel(Enum):
    """工具安全分级"""
    READ_ONLY = "read_only"    # 仅读取状态，无副作用
    PROPOSE = "propose"        # 可生成提案，但不直接修改状态
    EXECUTE = "execute"        # 可直接修改状态（仅限 ActionExecutor 使用的内部工具）


class ToolDefinition(BaseModel):
    """工具定义（含安全分级）"""
    tool_name: str
    description: str
    parameters: dict[str, Any]
    security_level: ToolSecurityLevel
    allowed_roles: set[str]   # 允许使用此工具的 agent role 集合


class AgentRole(Enum):
    """Agent 角色分类"""
    ORCHESTRATOR = "orchestrator"
    DOMAIN_EXECUTOR = "domain_executor"      # Lighting, HVAC, Security, Energy
    CONTEXT_REASONER = "context_reasoner"    # Habit, Health, FaultDiagnosis
    ACTION_EXECUTOR = "action_executor"      # 内部组件，非 LLM agent


# Agent Role → 允许的 Tool Security Level 映射
ROLE_TOOL_PERMISSIONS: dict[AgentRole, set[ToolSecurityLevel]] = {
    AgentRole.ORCHESTRATOR: {ToolSecurityLevel.READ_ONLY, ToolSecurityLevel.PROPOSE},
    AgentRole.DOMAIN_EXECUTOR: {ToolSecurityLevel.READ_ONLY, ToolSecurityLevel.PROPOSE},
    AgentRole.CONTEXT_REASONER: {ToolSecurityLevel.READ_ONLY},
    AgentRole.ACTION_EXECUTOR: {ToolSecurityLevel.READ_ONLY, ToolSecurityLevel.PROPOSE, ToolSecurityLevel.EXECUTE},
}


class SecureToolRegistry:
    """带安全分级的工具注册表"""

    def __init__(self):
        self._tools: dict[str, ToolDefinition] = {}

    def register(self, tool: ToolDefinition) -> None:
        self._tools[tool.tool_name] = tool

    def get_tools_for_agent(self, agent_id: str, agent_role: AgentRole) -> list[ToolDefinition]:
        """返回该 agent 有权使用的工具列表"""
        allowed_levels = ROLE_TOOL_PERMISSIONS.get(agent_role, set())
        return [
            t for t in self._tools.values()
            if t.security_level in allowed_levels
            and (not t.allowed_roles or agent_role.value in t.allowed_roles)
        ]

    async def call(
        self, tool_name: str, params: dict, caller_agent_id: str, caller_role: AgentRole
    ) -> dict[str, Any]:
        """安全调用工具：先校验权限，再执行"""
        tool = self._tools.get(tool_name)
        if tool is None:
            return {"success": False, "error": f"Tool not found: {tool_name}"}

        allowed_levels = ROLE_TOOL_PERMISSIONS.get(caller_role, set())
        if tool.security_level not in allowed_levels:
            # 违规调用：记录并拒绝
            return {
                "success": False,
                "error": f"Permission denied: {caller_role.value} cannot use {tool.security_level.value} tool",
                "violation": True,
            }

        # 实际执行
        result = await self._execute_tool(tool_name, params)
        return {"success": True, "result": result}
```

**典型工具分级示例**：

| 工具名 | 安全级别 | 允许角色 |
|--------|----------|----------|
| `get_device_state` | READ_ONLY | 全部 |
| `get_room_temperature` | READ_ONLY | 全部 |
| `query_event_log` | READ_ONLY | 全部 |
| `submit_proposal` | PROPOSE | orchestrator, domain_executor |
| `send_constraint` | PROPOSE | context_reasoner |
| `set_device_state` | EXECUTE | action_executor |
| `update_sensor` | EXECUTE | action_executor |

---

## 6. trace_id 盲区修补

### 6.1 盲区一：P2P 通信的 trace 传播

**问题**：agent 间 pub/sub 和 request/response 直接通信时，trace_id 可能在消息转发中丢失。

**方案**：在统一消息模型中**强制 trace_id 字段**，消息总线拒绝接受 trace_id 为空的消息。

```python
class Message(BaseModel):
    """统一消息模型（强制 trace_id，精简为 4 种 kind）"""
    message_id: str
    trace_id: str              # 必填，不允许为空
    correlation_id: str | None = None
    session_id: str
    kind: str                  # event | request | proposal | heartbeat（4 种）
    status: str | None = None  # request: null/fulfilled/rejected/error; proposal: 状态机流转
    from_agent: str
    to_agent: str | None = None  # None = 广播
    topic: str
    payload: dict[str, Any]
    timestamp: float
    ttl_ms: int = 5000
    require_ack: bool = False


class TracePropagationMixin:
    """trace_id 传播辅助，所有需要发送消息的组件混入此类"""

    def propagate_trace(self, incoming_message: Message, outgoing_payload: dict) -> str:
        """从入站消息继承 trace_id。

        规则：
        - 如果当前操作是响应某条入站消息 → 继承其 trace_id
        - 如果是独立发起的新消息 → 生成新 trace_id
        """
        return incoming_message.trace_id

    @staticmethod
    def new_trace_id() -> str:
        """生成新的 trace_id"""
        import uuid
        return f"tr_{uuid.uuid4().hex[:12]}"


class EventBus:
    """事件总线（强制 trace_id 校验）"""

    async def publish(self, message: Message) -> None:
        if not message.trace_id:
            raise ValueError(f"Message {message.message_id} missing trace_id — rejected by EventBus")
        # 发布逻辑...

    async def send(self, message: Message) -> None:
        """P2P 消息发送（同样强制 trace_id）"""
        if not message.trace_id:
            raise ValueError(f"P2P message {message.message_id} missing trace_id")
        # 投递到目标 agent 的 Mailbox...
```

### 6.2 盲区二：心跳异常的追踪

**问题**：心跳超时触发的 `agent.degraded` / `agent.offline` 事件没有 trace_id，无法关联到具体的影响链。

**方案**：为心跳监控生成**监控级 trace_id**，将异常检测 -> 状态变更 -> 任务重分配串联起来。

```python
class HeartbeatMonitor:
    """心跳监控器（带 trace 传播）"""

    async def check_heartbeats(self) -> None:
        """周期性检查所有 agent 心跳状态"""
        for agent_id, last_beat in self._last_heartbeats.items():
            elapsed = self._now() - last_beat

            if elapsed > self._warning_threshold:
                # 生成监控 trace_id，贯穿后续所有关联操作
                trace_id = f"tr_hb_{agent_id}_{int(self._now())}"

                if elapsed > self._offline_threshold:
                    await self._handle_offline(agent_id, trace_id)
                else:
                    await self._handle_degraded(agent_id, trace_id)

    async def _handle_offline(self, agent_id: str, trace_id: str) -> None:
        """agent 下线处理：标记 offline + 释放任务 + 发布事件（全部携带 trace_id）"""
        # 1. 标记 offline
        await self._registry.set_status(agent_id, "offline", trace_id=trace_id)

        # 2. 释放该 agent 持有的任务（携带同一 trace_id）
        released_tasks = await self._task_board.release_tasks_by_owner(
            owner=agent_id, trace_id=trace_id
        )

        # 3. 发布事件
        await self._event_bus.publish(Message(
            message_id=f"msg_offline_{agent_id}",
            trace_id=trace_id,  # 关键：携带监控 trace_id
            session_id=self._session_id,
            kind="event",  # Phase 1: incident 用 event + topic="incident.*" 临时替代
            from_agent="heartbeat_monitor",
            topic="incident.agent.offline",
            payload={
                "incident_status": "detected",  # R4: 为 Phase 2 incident 迁移预留
                "agent_id": agent_id,
                "released_tasks": [t.id for t in released_tasks],
            },
            timestamp=self._now(),
        ))
```

### 6.3 盲区三：自治认领任务的 trace 继承

**问题**：agent 在 `idle_claim()` 阶段自主认领任务时，新的执行链没有 trace_id，无法与任务的创建链路关联。

**方案**：任务自身携带 `origin_trace_id`，认领时继承。

```python
class Task(BaseModel):
    """任务模型（增加 trace 字段）"""
    id: int
    subject: str
    description: str
    status: str
    owner: str | None = None
    blocked_by: list[int] = Field(default_factory=list)
    priority: str = "normal"
    session_id: str
    version: int = 1
    origin_trace_id: str      # 创建该任务时的 trace_id（任务创建者设置）
    active_trace_id: str | None = None  # 当前执行链的 trace_id（认领时生成）


class TaskBoard:
    """任务板（带 trace 继承）"""

    async def claim_task(
        self, task_id: int, agent_id: str, expected_version: int
    ) -> tuple[bool, Task]:
        """CAS 认领任务，同时为新执行链生成 trace_id"""
        task = await self._get_task(task_id)
        if task.status != "pending" or task.version != expected_version:
            return False, task

        # 认领时生成新的 active_trace_id，但保留 origin_trace_id 关联
        task.owner = agent_id
        task.status = "in_progress"
        task.version += 1
        task.active_trace_id = f"tr_claim_{agent_id}_{task_id}_{task.version}"

        # 记录 trace 继承关系
        await self._event_log.append(
            event_type="trace.inherited",
            trace_id=task.active_trace_id,
            payload={
                "parent_trace_id": task.origin_trace_id,
                "task_id": task_id,
                "claimed_by": agent_id,
            },
        )
        return True, task
```

### 6.4 盲区四：定时器触发场景的 trace 生成

**问题**：`EventScheduler` 中的定时触发事件（如每日 22:00 自动启动睡眠模式）没有用户操作作为 trace 起点，导致 trace 链断裂。

**方案**：定时器触发时生成**调度级 trace_id**，格式区分于用户触发。

```python
class ScheduledTrigger(BaseModel):
    """定时触发器定义"""
    trigger_id: str
    cron_expression: str       # 如 "0 22 * * *"
    scenario: str              # 如 "sleep_mode"
    enabled: bool = True


class EventScheduler:
    """仿真事件调度器（带定时器 trace 生成）"""

    async def fire_trigger(self, trigger: ScheduledTrigger, sim_time: float) -> None:
        """定时器触发：生成调度级 trace_id，启动完整因果链"""
        # 调度级 trace_id 格式：tr_sched_{trigger_id}_{sim_time}
        trace_id = f"tr_sched_{trigger.trigger_id}_{int(sim_time)}"

        # 记录定时触发事件（trace 链的起点）
        await self._event_log.append(
            event_type="trigger.fired",
            trace_id=trace_id,
            payload={
                "trigger_id": trigger.trigger_id,
                "scenario": trigger.scenario,
                "cron": trigger.cron_expression,
                "sim_time": sim_time,
            },
        )

        # 将场景启动命令注入 Orchestrator，携带 trace_id
        await self._event_bus.publish(Message(
            message_id=f"msg_trigger_{trigger.trigger_id}",
            trace_id=trace_id,
            session_id=self._session_id,
            kind="event",
            from_agent="event_scheduler",
            topic=f"scenario.{trigger.scenario}.triggered",
            payload={"trigger_id": trigger.trigger_id},
            timestamp=sim_time,
        ))
```

### 6.5 trace_id 修补总结

| 盲区 | 根因 | 修补方案 | trace_id 格式 |
|------|------|----------|---------------|
| P2P 通信 | 消息转发未强制携带 trace_id | EventBus 强制校验，拒绝无 trace 消息 | 继承入站消息的 trace_id |
| 心跳异常 | 监控事件无因果起点 | 为心跳异常生成监控级 trace_id | `tr_hb_{agent_id}_{timestamp}` |
| 自治认领 | 认领操作未关联任务创建链路 | Task 携带 origin_trace_id，认领时生成 active_trace_id | `tr_claim_{agent}_{task}_{ver}` |
| 定时触发 | 无用户操作作为 trace 起点 | 定时器触发时生成调度级 trace_id | `tr_sched_{trigger}_{sim_time}` |

---

## 7. 附录：与原始架构的集成矩阵

| 本文档组件 | 影响的原始架构组件 | 修改类型 |
|------------|-------------------|----------|
| RecordReplayProxy | AgentCore.think() | 注入（proxy 模式，无侵入） |
| LLMCacheStore | 数据存储层 | 新增 SQLite 表 |
| StateReplayEngine | ReplayEngine | 细化（给出状态回放具体实现） |
| FullReplayEngine | ReplayEngine | 细化（给出全真回放具体实现） |
| AtomicArbiter | PolicyArbiter | 前置包装（不修改 PolicyArbiter 内部） |
| WorldState ACL | WorldState | 增强（添加写屏障） |
| SecureToolRegistry | ToolRegistry | 增强（添加安全分级） |
| trace_id 修补 | Message, EventBus, TaskBoard, EventScheduler | 增强（强制 trace 传播） |
