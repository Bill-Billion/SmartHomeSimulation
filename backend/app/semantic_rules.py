from __future__ import annotations

import math
import re
from collections import defaultdict
from dataclasses import dataclass

from .domain_rules import default_room_name
from .geometry import point_in_polygon_xy
from .models import RoomSpec, RoomType, WallKind, WallSegment


@dataclass(frozen=True)
class RoomSemanticHint:
    """统一承载 OCR 和 DXF 文本提取出的房间语义线索。"""

    room_type: RoomType
    center: tuple[float, float]
    raw_text: str
    source: str


@dataclass(frozen=True)
class RoomSemanticProfile:
    """汇总房间几何和拓扑特征，避免语义判定回头污染主几何链。"""

    room_id: str
    polygon: list[tuple[float, float]]
    area: float
    area_rank: int
    area_ratio: float
    aspect: float
    compactness: float
    centroid: tuple[float, float]
    diagonal: float
    outer_exposure_ratio: float
    outer_contact: bool
    neighbors: tuple[str, ...]


def _normalize_room_semantic_text(raw_text: str) -> RoomType | None:
    """只保留房间语义相关词，尺寸、轴号和编号一律丢弃。"""
    text = raw_text.strip()
    if not text:
        return None

    ascii_token_list = [token for token in re.split(r"[^a-z]+", text.lower()) if token]
    ascii_tokens = set(ascii_token_list)
    compact_ascii = "".join(ascii_token_list)
    chinese_text = re.sub(r"[^\u4e00-\u9fff]", "", text)

    if "厨房" in chinese_text or "kitchen" in ascii_tokens or "kitchen" in compact_ascii:
        return RoomType.KITCHEN
    if (
        "卫生间" in chinese_text
        or "洗手间" in chinese_text
        or "厕所" in chinese_text
        or {"bath", "room"} <= ascii_tokens
        or "bathroom" in ascii_tokens
        or "bathroom" in compact_ascii
        or "toilet" in ascii_tokens
        or "wc" in ascii_tokens
        or "washroom" in ascii_tokens
        or "restroom" in ascii_tokens
    ):
        return RoomType.BATHROOM
    if "客厅" in chinese_text or "living" in ascii_tokens or "livingroom" in compact_ascii:
        return RoomType.LIVING_ROOM
    if (
        "卧室" in chinese_text
        or "bedroom" in ascii_tokens
        or "bedroom" in compact_ascii
        or {"bed", "room"} <= ascii_tokens
    ):
        return RoomType.BEDROOM
    if (
        "走廊" in chinese_text
        or "过道" in chinese_text
        or "corridor" in ascii_tokens
        or "hall" in ascii_tokens
        or "hallway" in ascii_tokens
        or "corridor" in compact_ascii
        or "hallway" in compact_ascii
    ):
        return RoomType.CORRIDOR
    return None


def _apply_room_semantics(
    rooms: list[RoomSpec],
    outer_walls: list[WallSegment],
    inner_walls: list[WallSegment],
    room_edge_map: dict[str, list[str]],
    semantic_hints: list[RoomSemanticHint] | tuple[RoomSemanticHint, ...],
) -> tuple[list[RoomSpec], list[str]]:
    """把几何、拓扑和文本证据合并到房间语义层，不改公开接口。"""
    if not rooms:
        return rooms, []

    profiles = _build_room_semantic_profiles(rooms, outer_walls, inner_walls, room_edge_map)
    hint_assignments = _assign_semantic_hints_to_rooms(profiles, semantic_hints)
    base_scores = {
        room.room_id: _base_room_semantic_scores(
            profiles[room.room_id],
            hint_assignments.get(room.room_id, []),
        )
        for room in rooms
    }
    provisional_types = {
        room_id: max(scores.items(), key=lambda item: item[1])[0]
        for room_id, scores in base_scores.items()
    }
    final_scores_by_room: dict[str, dict[RoomType, float]] = {}

    for room in rooms:
        profile = profiles[room.room_id]
        scores = dict(base_scores[room.room_id])
        neighbor_types = [
            provisional_types[neighbor_id]
            for neighbor_id in profile.neighbors
            if neighbor_id in provisional_types
        ]
        if RoomType.LIVING_ROOM in neighbor_types:
            scores[RoomType.KITCHEN] += 1.3
            scores[RoomType.BEDROOM] += 0.5
        if RoomType.CORRIDOR in neighbor_types:
            scores[RoomType.KITCHEN] += 1.0
            scores[RoomType.BATHROOM] += 1.2
            scores[RoomType.LIVING_ROOM] += 0.4
        final_scores_by_room[room.room_id] = scores

    _apply_room_type_capacity_penalties(final_scores_by_room, hint_assignments)

    semantic_warnings: list[str] = []
    has_generic_fallback = False
    updated_rooms: list[RoomSpec] = []

    for index, room in enumerate(rooms, start=1):
        room_type, confidence, degraded = _resolve_room_semantic_choice(
            final_scores_by_room[room.room_id],
            hint_assignments.get(room.room_id, []),
        )
        if degraded:
            has_generic_fallback = True

        updated_rooms.append(
            RoomSpec(
                room_id=room.room_id,
                name=default_room_name(room_type, index),
                room_type=room_type,
                polygon=room.polygon,
                area_sqm=room.area_sqm,
                confidence=confidence,
            )
        )

    if has_generic_fallback:
        semantic_warnings.append("部分房间语义证据不足，已按通用空间处理。")
    return updated_rooms, semantic_warnings


def _apply_room_type_capacity_penalties(
    scores_by_room: dict[str, dict[RoomType, float]],
    hint_assignments: dict[str, list[tuple[RoomSemanticHint, float, bool]]],
) -> None:
    """没有文本锚点时，限制高稀缺类型的重复命中，避免整套户型被判成多个厨房。"""
    capacities = {
        RoomType.LIVING_ROOM: 1,
        RoomType.KITCHEN: 1,
        RoomType.CORRIDOR: 1,
        RoomType.BATHROOM: 2,
    }
    penalties = {
        RoomType.LIVING_ROOM: 1.6,
        RoomType.KITCHEN: 1.6,
        RoomType.CORRIDOR: 1.4,
        RoomType.BATHROOM: 1.2,
    }

    for room_type, capacity in capacities.items():
        text_backed_rooms = {
            room_id
            for room_id, assignments in hint_assignments.items()
            if any(hint.room_type == room_type for hint, _weight, _direct in assignments)
        }
        kept = 0
        ranked = sorted(
            scores_by_room.items(),
            key=lambda item: item[1][room_type],
            reverse=True,
        )
        for room_id, scores in ranked:
            if scores[room_type] < 2.2:
                continue
            if room_id in text_backed_rooms:
                kept += 1
                continue
            if kept < capacity:
                kept += 1
                continue
            scores[room_type] -= penalties[room_type]


def _build_room_semantic_profiles(
    rooms: list[RoomSpec],
    outer_walls: list[WallSegment],
    inner_walls: list[WallSegment],
    room_edge_map: dict[str, list[str]],
) -> dict[str, RoomSemanticProfile]:
    # 这里延迟导入，避免和 floorplan_builder 的装配入口形成模块级循环依赖。
    from .floorplan_builder import (
        _build_polygon_edge_usage,
        _polygon_centroid,
        _polygon_exterior_edge_count,
        _segment_length,
    )

    wall_by_id = {wall.wall_id: wall for wall in [*outer_walls, *inner_walls]}
    inner_wall_ids = {wall.wall_id for wall in inner_walls}
    wall_to_rooms: dict[str, set[str]] = defaultdict(set)
    total_area = max(sum(room.area_sqm for room in rooms), 0.01)
    profiles: dict[str, RoomSemanticProfile] = {}

    for room_id, wall_ids in room_edge_map.items():
        for wall_id in wall_ids:
            wall_to_rooms[wall_id].add(room_id)

    sorted_rooms = sorted(rooms, key=lambda room: room.area_sqm, reverse=True)
    edge_usage = _build_polygon_edge_usage(
        [[(point.x, point.z) for point in room.polygon] for room in rooms]
    )

    for area_rank, room in enumerate(sorted_rooms):
        polygon = [(point.x, point.z) for point in room.polygon]
        min_x = min(point[0] for point in polygon)
        max_x = max(point[0] for point in polygon)
        min_z = min(point[1] for point in polygon)
        max_z = max(point[1] for point in polygon)
        width = max_x - min_x
        depth = max_z - min_z
        perimeter = 0.0
        outer_length = 0.0
        neighbors: set[str] = set()
        exterior_edges = _polygon_exterior_edge_count(polygon, edge_usage)

        for wall_id in room_edge_map.get(room.room_id, []):
            wall = wall_by_id.get(wall_id)
            if wall is None:
                continue
            segment_length = _segment_length(wall)
            perimeter += segment_length
            if wall.kind == WallKind.OUTER:
                outer_length += segment_length
            elif wall_id in inner_wall_ids:
                neighbors.update(other_room for other_room in wall_to_rooms[wall_id] if other_room != room.room_id)

        compactness = room.area_sqm / max(width * depth, 0.01)
        outer_exposure_ratio = outer_length / max(perimeter, 0.01)
        outer_contact = outer_length >= max(0.4, perimeter * 0.12) or exterior_edges >= 1
        profiles[room.room_id] = RoomSemanticProfile(
            room_id=room.room_id,
            polygon=polygon,
            area=room.area_sqm,
            area_rank=area_rank,
            area_ratio=room.area_sqm / total_area,
            aspect=width / max(depth, 0.01),
            compactness=compactness,
            centroid=_polygon_centroid(polygon),
            diagonal=max(math.hypot(width, depth), 0.6),
            outer_exposure_ratio=outer_exposure_ratio,
            outer_contact=outer_contact,
            neighbors=tuple(sorted(neighbors)),
        )
    return profiles


def _assign_semantic_hints_to_rooms(
    profiles: dict[str, RoomSemanticProfile],
    semantic_hints: list[RoomSemanticHint] | tuple[RoomSemanticHint, ...],
) -> dict[str, list[tuple[RoomSemanticHint, float, bool]]]:
    assignments: dict[str, list[tuple[RoomSemanticHint, float, bool]]] = defaultdict(list)

    for hint in semantic_hints:
        direct_candidates = [
            profile
            for profile in profiles.values()
            if point_in_polygon_xy(hint.center, profile.polygon)
        ]
        if direct_candidates:
            target = min(
                direct_candidates,
                key=lambda profile: math.dist(hint.center, profile.centroid),
            )
            assignments[target.room_id].append((hint, 8.0, True))
            continue

        nearby_candidates = []
        for profile in profiles.values():
            distance = math.dist(hint.center, profile.centroid)
            if distance <= profile.diagonal * 0.35:
                nearby_candidates.append((distance, profile))
        if not nearby_candidates:
            continue
        _, target = min(nearby_candidates, key=lambda item: item[0])
        assignments[target.room_id].append((hint, 5.0, False))

    return dict(assignments)


def _base_room_semantic_scores(
    profile: RoomSemanticProfile,
    assigned_hints: list[tuple[RoomSemanticHint, float, bool]],
) -> dict[RoomType, float]:
    scores = {
        RoomType.LIVING_ROOM: 0.0,
        RoomType.BEDROOM: 0.0,
        RoomType.KITCHEN: 0.0,
        RoomType.BATHROOM: 0.0,
        RoomType.CORRIDOR: 0.0,
    }
    corridor_like = profile.aspect > 2.4 or profile.aspect < 0.42 or profile.compactness < 0.62
    adjacency_count = len(profile.neighbors)

    if profile.area_rank == 0:
        scores[RoomType.LIVING_ROOM] += 2.2
    if profile.area_ratio >= 0.26:
        scores[RoomType.LIVING_ROOM] += 1.8
    elif profile.area_ratio >= 0.18:
        scores[RoomType.LIVING_ROOM] += 0.8
    if profile.outer_exposure_ratio >= 0.35:
        scores[RoomType.LIVING_ROOM] += 1.4
    elif profile.outer_exposure_ratio >= 0.2:
        scores[RoomType.LIVING_ROOM] += 0.7
    if adjacency_count >= 2:
        scores[RoomType.LIVING_ROOM] += 1.2
    elif adjacency_count >= 1:
        scores[RoomType.LIVING_ROOM] += 0.4
    if corridor_like:
        scores[RoomType.LIVING_ROOM] -= 2.2

    if 0.10 <= profile.area_ratio <= 0.30:
        scores[RoomType.BEDROOM] += 1.4
    elif profile.area_ratio > 0.30:
        scores[RoomType.BEDROOM] += 0.4
    if profile.outer_contact:
        scores[RoomType.BEDROOM] += 1.2
    if profile.area_rank > 0:
        scores[RoomType.BEDROOM] += 0.6
    if adjacency_count <= 2:
        scores[RoomType.BEDROOM] += 0.4
    if corridor_like:
        scores[RoomType.BEDROOM] -= 1.0
    if profile.area_ratio < 0.07:
        scores[RoomType.BEDROOM] -= 0.8

    if 0.06 <= profile.area_ratio <= 0.22:
        scores[RoomType.KITCHEN] += 1.3
    if profile.outer_contact:
        scores[RoomType.KITCHEN] += 1.3
    if 0 < profile.area_rank <= 3:
        scores[RoomType.KITCHEN] += 0.6
    if profile.compactness >= 0.55:
        scores[RoomType.KITCHEN] += 0.4
    if profile.area_ratio > 0.35:
        scores[RoomType.KITCHEN] -= 0.6

    if profile.area_ratio <= 0.14:
        scores[RoomType.BATHROOM] += 1.8
    elif profile.area_ratio <= 0.2:
        scores[RoomType.BATHROOM] += 0.8
    if profile.outer_exposure_ratio <= 0.3:
        scores[RoomType.BATHROOM] += 1.0
    if profile.area_rank >= 2:
        scores[RoomType.BATHROOM] += 0.7
    if adjacency_count <= 2:
        scores[RoomType.BATHROOM] += 0.4
    if profile.area_ratio > 0.22:
        scores[RoomType.BATHROOM] -= 0.8

    if corridor_like:
        scores[RoomType.CORRIDOR] += 2.5
    if corridor_like and adjacency_count >= 2:
        scores[RoomType.CORRIDOR] += 1.8
    if profile.compactness < 0.6:
        scores[RoomType.CORRIDOR] += 1.5
    if adjacency_count >= 2:
        scores[RoomType.CORRIDOR] += 1.5
    elif adjacency_count >= 1:
        scores[RoomType.CORRIDOR] += 0.6
    if profile.area_ratio <= 0.18:
        scores[RoomType.CORRIDOR] += 0.6
    if profile.outer_exposure_ratio < 0.45:
        scores[RoomType.CORRIDOR] += 0.4

    for hint, weight, _direct in assigned_hints:
        scores[hint.room_type] += weight
    return scores


def _resolve_room_semantic_choice(
    scores: dict[RoomType, float],
    assigned_hints: list[tuple[RoomSemanticHint, float, bool]],
) -> tuple[RoomType, float, bool]:
    ranked = sorted(scores.items(), key=lambda item: item[1], reverse=True)
    best_type, best_score = ranked[0]
    second_score = ranked[1][1] if len(ranked) > 1 else 0.0
    direct_text_types = {
        hint.room_type
        for hint, _weight, direct in assigned_hints
        if direct
    }
    if direct_text_types:
        best_type = max(direct_text_types, key=lambda room_type: scores[room_type])
        best_score = scores[best_type]
        second_score = max((score for room_type, score in ranked if room_type != best_type), default=0.0)

    has_text_evidence = bool(assigned_hints)
    gap = best_score - second_score
    degraded = best_score < 3.0 or (gap < 0.5 and not has_text_evidence)
    if degraded:
        return RoomType.GENERIC, _semantic_confidence(best_score, gap, has_text_evidence, False, True), True

    return (
        best_type,
        _semantic_confidence(best_score, gap, has_text_evidence, bool(direct_text_types), False),
        False,
    )


def _semantic_confidence(
    best_score: float,
    gap: float,
    has_text_evidence: bool,
    has_direct_text: bool,
    degraded: bool,
) -> float:
    score_strength = min(max(best_score, 0.0) / 10.0, 1.0)
    gap_strength = min(max(gap, 0.0) / 4.0, 1.0)
    confidence = 0.55 + score_strength * 0.22 + gap_strength * 0.12
    if has_text_evidence:
        confidence += 0.04
    if has_direct_text:
        confidence += 0.04
    if degraded:
        confidence = min(confidence, 0.62)
    return round(min(max(confidence, 0.55), 0.92), 3)
