from __future__ import annotations

import json
import re
from dataclasses import dataclass
from uuid import uuid4

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .llm_api_utils import extract_json_object, extract_message_content, normalize_chat_endpoint
from .llm_enhancer import LlmRequestConfig
from .models import (
    ActionProposal,
    AgentTask,
    LightOperation,
    RoomState,
    RoomType,
    SimulationSession,
    utc_now_iso,
)


@dataclass
class AgentRegistry:
    """最小常驻 Agent 注册中心，首版固定两个 Agent。"""

    def __post_init__(self) -> None:
        self._agents = {
            "orchestrator": {"role": "task_router"},
            "lighting_agent": {"role": "lighting_specialist"},
        }

    def has_agent(self, agent_id: str) -> bool:
        return agent_id in self._agents

    def list_agent_ids(self) -> list[str]:
        return sorted(self._agents.keys())


class Orchestrator:
    """首版编排器：只处理单意图灯光命令。"""

    def __init__(self, registry: AgentRegistry):
        self._registry = registry

    def create_task(self, *, session: SimulationSession, command: str, trace_id: str) -> AgentTask:
        if not self._registry.has_agent("lighting_agent"):
            raise ValueError("照明智能体未注册，无法分配任务。")

        intent = _parse_lighting_command(command, session.world_state.rooms)
        return AgentTask(
            task_id=f"task_{uuid4().hex[:12]}",
            trace_id=trace_id,
            session_id=session.session_id,
            command=command.strip(),
            operation=intent.operation,
            target_room_id=intent.target_room_id,
            target_device_id=intent.target_device_id,
            created_at=utc_now_iso(),
        )


@dataclass(frozen=True)
class ParsedLightingIntent:
    operation: LightOperation
    target_room_id: str
    target_device_id: str


class LightingAgent:
    """首版照明智能体：规则优先，LLM 只做参数微调。"""

    def propose(
        self,
        *,
        session: SimulationSession,
        task: AgentTask,
        llm_config: LlmRequestConfig,
    ) -> tuple[ActionProposal, list[str]]:
        room = next((item for item in session.world_state.rooms if item.room_id == task.target_room_id), None)
        if room is None:
            raise ValueError("目标房间不存在，无法生成灯光提案。")

        brightness, color_temp, reason = _default_lighting_parameters(task.operation, room.room_type)
        warnings: list[str] = []
        llm_used = False

        if task.operation == LightOperation.TURN_ON and llm_config.enabled:
            if not llm_config.ready():
                warnings.append("AI 微调配置不完整，已按规则参数执行。")
            else:
                try:
                    adjustment = _request_lighting_adjustment(
                        config=llm_config,
                        command=task.command,
                        room=room,
                        default_brightness=brightness,
                        default_color_temp=color_temp,
                    )
                    brightness = adjustment.brightness
                    color_temp = adjustment.color_temp
                    if adjustment.reason:
                        reason = f"{reason}；AI微调：{adjustment.reason}"
                    llm_used = True
                except LightingLlmFailure as exc:
                    warnings.append(exc.user_warning)
                except Exception:  # noqa: BLE001
                    warnings.append("AI 微调异常，已按规则参数执行。")

        return (
            ActionProposal(
                proposal_id=f"proposal_{uuid4().hex[:12]}",
                trace_id=task.trace_id,
                task_id=task.task_id,
                agent_id="lighting_agent",
                target_device_id=task.target_device_id,
                operation=task.operation,
                brightness=brightness,
                color_temp=color_temp,
                reason=reason,
                llm_used=llm_used,
            ),
            warnings,
        )


def _default_lighting_parameters(operation: LightOperation, room_type: RoomType) -> tuple[int, int, str]:
    if operation == LightOperation.TURN_OFF:
        return 0, 3500, "规则策略：关灯。"

    brightness_by_room = {
        RoomType.BEDROOM: 35,
        RoomType.LIVING_ROOM: 65,
        RoomType.KITCHEN: 75,
        RoomType.BATHROOM: 70,
        RoomType.CORRIDOR: 45,
        RoomType.GENERIC: 55,
    }
    color_temp_by_room = {
        RoomType.BEDROOM: 3000,
        RoomType.LIVING_ROOM: 3800,
        RoomType.KITCHEN: 4300,
        RoomType.BATHROOM: 4200,
        RoomType.CORRIDOR: 3600,
        RoomType.GENERIC: 3800,
    }
    return (
        brightness_by_room.get(room_type, 55),
        color_temp_by_room.get(room_type, 3800),
        "规则策略：按房间类型选择亮度和色温。",
    )


def _parse_lighting_command(command: str, rooms: list[RoomState]) -> ParsedLightingIntent:
    normalized = command.strip().lower()
    if not normalized:
        raise ValueError("命令不能为空，请输入“打开卧室灯”这类指令。")

    if not any(token in normalized for token in ("灯", "light", "lights")):
        raise ValueError("当前只支持灯光命令，请在指令中包含“灯”或“light”。")

    if re.search(r"(关闭|关掉|关上|关灯|turn off|switch off|\boff\b)", normalized):
        operation = LightOperation.TURN_OFF
    elif re.search(r"(打开|开启|开灯|turn on|switch on|\bon\b)", normalized):
        operation = LightOperation.TURN_ON
    else:
        raise ValueError("当前只支持“打开/关闭 + 房间 + 灯”的命令格式。")

    room = _match_room(command, rooms)
    if room is None:
        if len(rooms) == 1:
            room = rooms[0]
        else:
            raise ValueError("没有识别到目标房间，请明确写出房间名，例如“打开卧室灯”。")

    return ParsedLightingIntent(
        operation=operation,
        target_room_id=room.room_id,
        target_device_id=room.primary_light_id,
    )


def _match_room(command: str, rooms: list[RoomState]) -> RoomState | None:
    lowered = command.lower()
    for room in sorted(rooms, key=lambda item: len(item.room_name), reverse=True):
        if room.room_name and room.room_name.lower() in lowered:
            return room
        if room.room_id.lower() in lowered:
            return room

    room_type_tokens = {
        RoomType.BEDROOM: ("卧室", "bedroom"),
        RoomType.LIVING_ROOM: ("客厅", "living room", "living"),
        RoomType.KITCHEN: ("厨房", "kitchen"),
        RoomType.BATHROOM: ("卫生间", "洗手间", "toilet", "bathroom", "wc"),
        RoomType.CORRIDOR: ("走廊", "过道", "corridor", "hallway"),
        RoomType.GENERIC: (),
    }
    for room_type, tokens in room_type_tokens.items():
        if not tokens:
            continue
        if any(token in lowered for token in tokens):
            candidates = [room for room in rooms if room.room_type == room_type]
            if candidates:
                return candidates[0]
    return None


class LightingAdjustment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    brightness: int = Field(ge=1, le=100)
    color_temp: int = Field(ge=2700, le=6500)
    reason: str | None = None


class LightingLlmFailure(RuntimeError):
    def __init__(self, *, user_warning: str):
        super().__init__(user_warning)
        self.user_warning = user_warning


def _request_lighting_adjustment(
    *,
    config: LlmRequestConfig,
    command: str,
    room: RoomState,
    default_brightness: int,
    default_color_temp: int,
) -> LightingAdjustment:
    endpoint = normalize_chat_endpoint(config.base_url or "")
    prompt = (
        "你是照明参数微调器。你只能返回 JSON，不要包含 markdown。\n"
        "只允许输出字段：brightness(1-100), color_temp(2700-6500), reason。\n"
        "不能修改开关动作，只能微调亮度和色温。\n"
        f"用户命令：{command}\n"
        f"房间：{room.room_name} ({room.room_type.value})\n"
        f"规则默认亮度：{default_brightness}\n"
        f"规则默认色温：{default_color_temp}\n"
    )
    payload = {
        "model": config.model,
        "temperature": 0.1,
        "max_tokens": 220,
        "messages": [
            {"role": "system", "content": "你是严格 JSON 输出助手。"},
            {"role": "user", "content": prompt},
        ],
    }

    try:
        with httpx.Client(timeout=12.0) as client:
            response = client.post(
                endpoint,
                headers={
                    "Authorization": f"Bearer {config.api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
            response.raise_for_status()
    except httpx.TimeoutException as exc:
        raise LightingLlmFailure(user_warning="AI 微调请求超时，已按规则参数执行。") from exc
    except httpx.ConnectError as exc:
        raise LightingLlmFailure(user_warning="AI 微调服务连接失败，已按规则参数执行。") from exc
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code
        if status in {401, 403}:
            raise LightingLlmFailure(user_warning="AI 微调鉴权失败，已按规则参数执行。") from exc
        if status == 404:
            raise LightingLlmFailure(user_warning="AI 微调接口地址不兼容，已按规则参数执行。") from exc
        raise LightingLlmFailure(user_warning="AI 微调服务异常，已按规则参数执行。") from exc
    except httpx.HTTPError as exc:
        raise LightingLlmFailure(user_warning="AI 微调服务调用失败，已按规则参数执行。") from exc

    try:
        response_payload = response.json()
    except ValueError as exc:
        raise LightingLlmFailure(user_warning="AI 微调响应不是 JSON，已按规则参数执行。") from exc

    try:
        content = extract_message_content(response_payload)
        data = extract_json_object(content)
        return LightingAdjustment.model_validate(data)
    except (ValueError, json.JSONDecodeError, ValidationError) as exc:
        raise LightingLlmFailure(user_warning="AI 微调输出格式不合法，已按规则参数执行。") from exc
