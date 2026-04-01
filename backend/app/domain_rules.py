from __future__ import annotations

from typing import Iterable

from .models import RoomType


RESOURCE_VERSION = "v0.1.6"
FURNITURE_WALL_PREFERENCES = frozenset({"north", "south", "east", "west", "longest", "center"})
ROOM_TYPE_LABELS = {
    RoomType.BEDROOM: "卧室",
    RoomType.LIVING_ROOM: "客厅",
    RoomType.KITCHEN: "厨房",
    RoomType.BATHROOM: "卫生间",
    RoomType.CORRIDOR: "走廊",
    RoomType.GENERIC: "通用房间",
}


def default_room_name(room_type: RoomType, index: int) -> str:
    """统一默认命名，避免解析链和 AI 层各自维护一套中文映射。"""
    return f"{ROOM_TYPE_LABELS.get(room_type, ROOM_TYPE_LABELS[RoomType.GENERIC])} {index}"


def merge_warnings(*groups: Iterable[str]) -> list[str]:
    """统一 warning 去重入口，保持顺序稳定，避免多模块重复堆叠。"""
    merged: list[str] = []
    for group in groups:
        for warning in group:
            text = str(warning).strip()
            if text and text not in merged:
                merged.append(text)
    return merged
