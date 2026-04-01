from __future__ import annotations

import threading
from dataclasses import dataclass, field
from uuid import uuid4

from .domain_rules import merge_warnings
from .llm_enhancer import LlmRequestConfig
from .models import (
    ActionProposal,
    AgentTask,
    CreateSimulationSessionRequest,
    ExecutionRecord,
    SimulationCommandRequest,
    SimulationEvent,
    SimulationEventsPage,
    SimulationSession,
    SimulationSessionStatus,
    utc_now_iso,
)
from .simulation_agents import AgentRegistry, LightingAgent, Orchestrator
from .simulation_world import LightweightWorldEngine, WorldEngine
from .storage import FileStorage


@dataclass
class ActionExecutor:
    """动作执行器：首版唯一允许写入世界状态的组件。"""

    world_engine: WorldEngine

    def execute(self, *, session: SimulationSession, proposal: ActionProposal) -> ExecutionRecord:
        before, after = self.world_engine.apply_proposal(
            state=session.world_state,
            proposal=proposal,
        )
        session.updated_at = utc_now_iso()
        return ExecutionRecord(
            execution_id=f"exec_{uuid4().hex[:12]}",
            trace_id=proposal.trace_id,
            session_id=session.session_id,
            proposal_id=proposal.proposal_id,
            success=True,
            message="动作执行完成。",
            before=before,
            after=after,
            created_at=utc_now_iso(),
        )


@dataclass
class EventLog:
    """最小事件日志：按序写入文件，支持游标分页读取。"""

    storage: FileStorage

    def append(
        self,
        *,
        session_id: str,
        trace_id: str,
        kind: str,
        actor: str,
        message: str,
        payload: dict,
    ) -> SimulationEvent:
        event = SimulationEvent(
            event_id=f"evt_{uuid4().hex[:12]}",
            trace_id=trace_id,
            session_id=session_id,
            sequence=0,
            kind=kind,
            actor=actor,
            message=message,
            payload=payload,
            created_at=utc_now_iso(),
        )
        return self.storage.append_simulation_event(event)

    def read_page(self, *, session_id: str, cursor: int, limit: int) -> SimulationEventsPage:
        events, next_cursor, has_more = self.storage.load_simulation_events(
            session_id,
            cursor=cursor,
            limit=limit,
        )
        return SimulationEventsPage(events=events, next_cursor=next_cursor, has_more=has_more)

@dataclass
class SessionManager:
    """会话管理器：创建和读取仿真会话。"""

    storage: FileStorage
    world_engine: WorldEngine

    def create(self, *, request: CreateSimulationSessionRequest) -> SimulationSession:
        scene = self.storage.load_scene(request.scene_id)
        session_id = f"sim_{uuid4().hex[:12]}"
        world_state = self.world_engine.build_initial_state(session_id=session_id, scene=scene)
        now = utc_now_iso()
        session = SimulationSession(
            session_id=session_id,
            scene_id=request.scene_id,
            status=SimulationSessionStatus.ACTIVE,
            world_state=world_state,
            created_at=now,
            updated_at=now,
            warnings=[],
        )
        self.storage.save_simulation_session(session)
        return session

    def get(self, *, session_id: str) -> SimulationSession:
        return self.storage.load_simulation_session(session_id)

    def save(self, *, session: SimulationSession) -> None:
        self.storage.save_simulation_session(session)


@dataclass
class SimulationRuntime:
    """阶段 2 最小仿真运行时：单域照明闭环。"""

    storage: FileStorage
    world_engine: WorldEngine = field(default_factory=LightweightWorldEngine)
    _EVENT_LOG_WARNING: str = "事件日志写入失败，已跳过但不影响仿真主流程。"

    def __post_init__(self) -> None:
        self.registry = AgentRegistry()
        self.orchestrator = Orchestrator(self.registry)
        self.lighting_agent = LightingAgent()
        self.executor = ActionExecutor(self.world_engine)
        self.sessions = SessionManager(self.storage, self.world_engine)
        self.events = EventLog(self.storage)
        self._session_locks: dict[str, threading.Lock] = {}
        self._session_locks_guard = threading.Lock()

    def create_session(self, *, request: CreateSimulationSessionRequest) -> SimulationSession:
        session = self.sessions.create(request=request)
        log_ok = self._append_event_best_effort(
            session_id=session.session_id,
            trace_id=f"trace_{uuid4().hex[:12]}",
            kind="session.created",
            actor="session_manager",
            message="仿真会话已创建。",
            payload={"scene_id": session.scene_id},
        )
        if not log_ok:
            self._merge_event_log_warning(session)
        return session

    def get_session(self, *, session_id: str) -> SimulationSession:
        return self.sessions.get(session_id=session_id)

    def execute_command(
        self,
        *,
        session_id: str,
        request: SimulationCommandRequest,
    ) -> tuple[SimulationSession, ExecutionRecord]:
        session_lock = self._get_session_lock(session_id)
        with session_lock:
            session = self.sessions.get(session_id=session_id)
            if session.status != SimulationSessionStatus.ACTIVE:
                raise ValueError("当前会话已关闭，无法执行命令。")

            command = request.command.strip()
            if not command:
                raise ValueError("命令不能为空，请输入“打开卧室灯”这类指令。")

            trace_id = f"trace_{uuid4().hex[:12]}"
            event_log_failed = not self._append_event_best_effort(
                session_id=session.session_id,
                trace_id=trace_id,
                kind="command.received",
                actor="user",
                message="接收到用户命令。",
                payload={"command": command},
            )

            task = self.orchestrator.create_task(
                session=session,
                command=command,
                trace_id=trace_id,
            )
            event_log_failed = (
                not self._append_event_best_effort(
                    session_id=task.session_id,
                    trace_id=task.trace_id,
                    kind="task.created",
                    actor="orchestrator",
                    message="任务已分配给照明智能体。",
                    payload={
                        "task_id": task.task_id,
                        "operation": task.operation.value,
                        "target_room_id": task.target_room_id,
                        "target_device_id": task.target_device_id,
                    },
                )
                or event_log_failed
            )
            proposal, proposal_warnings = self._build_proposal(session=session, task=task, request=request)
            event_log_failed = (
                not self._append_event_best_effort(
                    session_id=session.session_id,
                    trace_id=proposal.trace_id,
                    kind="proposal.created",
                    actor=proposal.agent_id,
                    message="照明提案已生成。",
                    payload={
                        "proposal_id": proposal.proposal_id,
                        "operation": proposal.operation.value,
                        "target_device_id": proposal.target_device_id,
                        "brightness": proposal.brightness,
                        "color_temp": proposal.color_temp,
                        "llm_used": proposal.llm_used,
                    },
                )
                or event_log_failed
            )
            execution = self.executor.execute(session=session, proposal=proposal)

            if proposal_warnings:
                session.warnings = merge_warnings(session.warnings, proposal_warnings)
            if event_log_failed:
                session.warnings = merge_warnings(session.warnings, [self._EVENT_LOG_WARNING])
            session.updated_at = utc_now_iso()
            self.sessions.save(session=session)
            # 先持久化 world_state，再写执行事件，避免“事件已写入但状态未落盘”的半提交态。
            if not self._append_event_best_effort(
                session_id=execution.session_id,
                trace_id=execution.trace_id,
                kind="action.executed",
                actor="action_executor",
                message=execution.message,
                payload={
                    "execution_id": execution.execution_id,
                    "proposal_id": execution.proposal_id,
                    "success": execution.success,
                    "before": execution.before.model_dump(mode="json") if execution.before else None,
                    "after": execution.after.model_dump(mode="json") if execution.after else None,
                },
            ):
                self._merge_event_log_warning(session)
            return session, execution

    def read_events(self, *, session_id: str, cursor: int, limit: int) -> SimulationEventsPage:
        return self.events.read_page(session_id=session_id, cursor=max(cursor, 0), limit=max(min(limit, 200), 1))

    def _build_proposal(
        self,
        *,
        session: SimulationSession,
        task: AgentTask,
        request: SimulationCommandRequest,
    ) -> tuple[ActionProposal, list[str]]:
        llm_config = LlmRequestConfig(
            enabled=request.llm_enabled,
            base_url=request.llm_base_url,
            model=request.llm_model,
            api_key=request.llm_api_key,
        )
        return self.lighting_agent.propose(session=session, task=task, llm_config=llm_config)

    def _get_session_lock(self, session_id: str) -> threading.Lock:
        with self._session_locks_guard:
            lock = self._session_locks.get(session_id)
            if lock is None:
                lock = threading.Lock()
                self._session_locks[session_id] = lock
            return lock

    def _append_event_best_effort(
        self,
        *,
        session_id: str,
        trace_id: str,
        kind: str,
        actor: str,
        message: str,
        payload: dict,
    ) -> bool:
        try:
            self.events.append(
                session_id=session_id,
                trace_id=trace_id,
                kind=kind,
                actor=actor,
                message=message,
                payload=payload,
            )
            return True
        except Exception:  # noqa: BLE001
            return False

    def _merge_event_log_warning(self, session: SimulationSession) -> None:
        next_warnings = merge_warnings(session.warnings, [self._EVENT_LOG_WARNING])
        if next_warnings == session.warnings:
            return
        session.warnings = next_warnings
        session.updated_at = utc_now_iso()
        try:
            self.sessions.save(session=session)
        except Exception:  # noqa: BLE001
            # 事件日志本身是降级能力，警告写回失败时不反向打断主流程。
            return
