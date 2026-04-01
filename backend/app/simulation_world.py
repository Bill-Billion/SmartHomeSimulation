from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from .models import (
    ActionProposal,
    DeviceState,
    LightOperation,
    RoomState,
    SceneSpec,
    WorldState,
    utc_now_iso,
)


class WorldEngine(Protocol):
    """世界模型抽象接口，后续替换 SimPy 时只需要替换引擎实现。"""

    def build_initial_state(self, *, session_id: str, scene: SceneSpec) -> WorldState:
        """从 SceneSpec 派生初始世界状态。"""

    def apply_proposal(
        self,
        *,
        state: WorldState,
        proposal: ActionProposal,
    ) -> tuple[DeviceState, DeviceState]:
        """应用动作提案并返回变更前后设备状态。"""


@dataclass
class LightweightWorldEngine:
    """第一版轻量世界模型：只建模照明域，保持最小可运行闭环。"""

    default_off_color_temp: int = 3500

    def build_initial_state(self, *, session_id: str, scene: SceneSpec) -> WorldState:
        rooms: list[RoomState] = []
        devices: list[DeviceState] = []

        for room in scene.rooms:
            light_id = f"light_{room.room_id}_main"
            rooms.append(
                RoomState(
                    room_id=room.room_id,
                    room_name=room.name,
                    room_type=room.room_type,
                    primary_light_id=light_id,
                    device_ids=[light_id],
                )
            )
            devices.append(
                DeviceState(
                    device_id=light_id,
                    room_id=room.room_id,
                    name=f"{room.name}主灯",
                    is_on=False,
                    brightness=0,
                    color_temp=self.default_off_color_temp,
                )
            )

        return WorldState(
            session_id=session_id,
            scene_id=scene.scene_id,
            rooms=rooms,
            devices=devices,
            updated_at=utc_now_iso(),
        )

    def apply_proposal(
        self,
        *,
        state: WorldState,
        proposal: ActionProposal,
    ) -> tuple[DeviceState, DeviceState]:
        device = next((item for item in state.devices if item.device_id == proposal.target_device_id), None)
        if device is None:
            raise ValueError("目标设备不存在，无法执行动作。")

        before = device.model_copy(deep=True)
        if proposal.operation == LightOperation.TURN_ON:
            brightness = _clamp_int(proposal.brightness if proposal.brightness is not None else 60, 1, 100)
            color_temp = _clamp_int(proposal.color_temp if proposal.color_temp is not None else 4000, 2700, 6500)
            device.is_on = True
            device.brightness = brightness
            device.color_temp = color_temp
        elif proposal.operation == LightOperation.TURN_OFF:
            device.is_on = False
            device.brightness = 0
            if proposal.color_temp is not None:
                device.color_temp = _clamp_int(proposal.color_temp, 2700, 6500)
            elif before.color_temp:
                device.color_temp = before.color_temp
            else:
                device.color_temp = self.default_off_color_temp
        else:
            raise ValueError("动作类型不受支持，当前仅支持灯光开关。")

        state.updated_at = utc_now_iso()
        return before, device.model_copy(deep=True)


def _clamp_int(raw_value: int, lower: int, upper: int) -> int:
    return max(lower, min(upper, int(raw_value)))
