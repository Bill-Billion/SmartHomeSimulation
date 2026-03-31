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
    has_text_evidence = any(hint_assignments.values())
    no_text_assignments = (
        _resolve_no_text_global_assignments(profiles, final_scores_by_room)
        if not has_text_evidence
        else {}
    )

    semantic_warnings: list[str] = []
    has_generic_fallback = False
    updated_rooms: list[RoomSpec] = []

    for index, room in enumerate(rooms, start=1):
        if no_text_assignments:
            room_type = no_text_assignments[room.room_id]
            confidence, degraded = _resolve_no_text_confidence(
                room_type,
                final_scores_by_room[room.room_id],
            )
        else:
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
    corridor_like = _is_corridor_like(profile)
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


def _resolve_no_text_global_assignments(
    profiles: dict[str, RoomSemanticProfile],
    scores_by_room: dict[str, dict[RoomType, float]],
) -> dict[str, RoomType]:
    """无文本模式下做户型级角色分配，避免每个房间各自打分后集体退化。"""
    assignments: dict[str, RoomType] = {room_id: RoomType.GENERIC for room_id in profiles}
    remaining = set(profiles.keys())
    centrality = _layout_centrality_scores(profiles)

    # 先锁定走廊，避免后续把细长连通空间误分给厨房或卧室。
    corridor_id = _pick_best_room(
        remaining,
        lambda room_id: _no_text_corridor_rank(
            profiles[room_id],
            scores_by_room[room_id],
            centrality[room_id],
        ),
        threshold=3.2,
    )
    if corridor_id is not None:
        assignments[corridor_id] = RoomType.CORRIDOR
        remaining.remove(corridor_id)

    # 客厅按中心性、面积和连接度优先，不允许细长空间抢占。
    living_id = _pick_best_room(
        remaining,
        lambda room_id: _no_text_living_rank(
            profiles[room_id],
            scores_by_room[room_id],
            centrality[room_id],
        ),
        threshold=2.9,
    )
    if living_id is None and remaining:
        living_id = max(
            remaining,
            key=lambda room_id: scores_by_room[room_id][RoomType.LIVING_ROOM],
        )
    if living_id is not None:
        assignments[living_id] = RoomType.LIVING_ROOM
        remaining.remove(living_id)

    # 厨房只分配一个，优先靠外墙且与客厅/走廊相邻的中小空间。
    kitchen_id = _pick_best_room(
        remaining,
        lambda room_id: _no_text_kitchen_rank(
            profiles[room_id],
            scores_by_room[room_id],
            assignments,
        ),
        threshold=2.5,
    )
    if kitchen_id is not None:
        assignments[kitchen_id] = RoomType.KITCHEN
        remaining.remove(kitchen_id)

    # 卫生间最多两个；小面积、低外墙暴露且靠近连接空间的候选优先。
    bathroom_capacity = 2 if len(profiles) >= 5 else 1
    bathroom_candidates = sorted(
        remaining,
        key=lambda room_id: _no_text_bathroom_rank(
            profiles[room_id],
            scores_by_room[room_id],
            assignments,
            centrality[room_id],
        ),
        reverse=True,
    )
    assigned_bathrooms = 0
    for room_id in bathroom_candidates:
        if assigned_bathrooms >= bathroom_capacity:
            break
        rank = _no_text_bathroom_rank(
            profiles[room_id],
            scores_by_room[room_id],
            assignments,
            centrality[room_id],
        )
        if rank < 2.4:
            continue
        assignments[room_id] = RoomType.BATHROOM
        remaining.remove(room_id)
        assigned_bathrooms += 1

    # 剩余房间优先回收为卧室，只有证据确实不足时才保留 generic。
    for room_id in sorted(remaining):
        profile = profiles[room_id]
        score = scores_by_room[room_id]
        bedroom_rank = _no_text_bedroom_rank(profile, score)
        if bedroom_rank >= 2.1 or (
            profile.area_ratio >= 0.08
            and not _is_corridor_like(profile)
            and len(profile.neighbors) <= 3
        ):
            assignments[room_id] = RoomType.BEDROOM
        else:
            assignments[room_id] = RoomType.GENERIC
    return assignments


def _pick_best_room(
    room_ids: set[str],
    rank_fn,
    *,
    threshold: float,
) -> str | None:
    if not room_ids:
        return None
    ranked = sorted(((room_id, rank_fn(room_id)) for room_id in room_ids), key=lambda item: item[1], reverse=True)
    room_id, score = ranked[0]
    if score < threshold:
        return None
    return room_id


def _layout_centrality_scores(profiles: dict[str, RoomSemanticProfile]) -> dict[str, float]:
    total_area = max(sum(profile.area for profile in profiles.values()), 0.01)
    center_x = sum(profile.centroid[0] * profile.area for profile in profiles.values()) / total_area
    center_z = sum(profile.centroid[1] * profile.area for profile in profiles.values()) / total_area
    distances = {
        room_id: math.dist(profile.centroid, (center_x, center_z))
        for room_id, profile in profiles.items()
    }
    max_distance = max(max(distances.values(), default=0.0), 0.01)
    return {
        room_id: 1.0 - min(distance / max_distance, 1.0)
        for room_id, distance in distances.items()
    }


def _no_text_corridor_rank(
    profile: RoomSemanticProfile,
    score: dict[RoomType, float],
    centrality: float,
) -> float:
    if profile.area_ratio > 0.36:
        return -999.0
    adjacency = len(profile.neighbors)
    corridor_like = _is_corridor_like(profile)
    if not corridor_like and adjacency < 3:
        return -999.0
    rank = score[RoomType.CORRIDOR]
    rank += 0.55 * min(adjacency, 4)
    rank += 1.2 if corridor_like else -0.6
    rank += (1.0 - centrality) * 0.4
    rank -= max(profile.area_ratio - 0.18, 0.0) * 6.0
    return rank


def _no_text_living_rank(
    profile: RoomSemanticProfile,
    score: dict[RoomType, float],
    centrality: float,
) -> float:
    rank = score[RoomType.LIVING_ROOM]
    rank += centrality * 1.8
    rank += min(len(profile.neighbors), 4) * 0.45
    rank += profile.area_ratio * 3.0
    if _is_corridor_like(profile):
        rank -= 2.0
    if profile.area_ratio < 0.12:
        rank -= 1.2
    return rank


def _no_text_kitchen_rank(
    profile: RoomSemanticProfile,
    score: dict[RoomType, float],
    assignments: dict[str, RoomType],
) -> float:
    rank = score[RoomType.KITCHEN]
    if profile.outer_contact:
        rank += 0.8
    if 0.06 <= profile.area_ratio <= 0.23:
        rank += 0.7
    if profile.area_ratio > 0.28:
        rank -= 1.4
    if _is_corridor_like(profile):
        rank -= 0.8

    neighbor_types = {assignments.get(neighbor_id) for neighbor_id in profile.neighbors}
    if RoomType.LIVING_ROOM in neighbor_types:
        rank += 1.1
    if RoomType.CORRIDOR in neighbor_types:
        rank += 0.7
    return rank


def _no_text_bathroom_rank(
    profile: RoomSemanticProfile,
    score: dict[RoomType, float],
    assignments: dict[str, RoomType],
    centrality: float,
) -> float:
    rank = score[RoomType.BATHROOM]
    if profile.area_ratio <= 0.16:
        rank += 0.9
    if profile.outer_exposure_ratio <= 0.3:
        rank += 0.8
    if profile.area_ratio > 0.24:
        rank -= 1.4
    if profile.area_ratio < 0.05:
        rank -= 0.6
    rank += (1.0 - centrality) * 0.4

    neighbor_types = {assignments.get(neighbor_id) for neighbor_id in profile.neighbors}
    if RoomType.CORRIDOR in neighbor_types:
        rank += 1.0
    elif RoomType.LIVING_ROOM in neighbor_types:
        rank += 0.5
    return rank


def _no_text_bedroom_rank(
    profile: RoomSemanticProfile,
    score: dict[RoomType, float],
) -> float:
    rank = score[RoomType.BEDROOM]
    if profile.outer_contact:
        rank += 0.5
    if 0.08 <= profile.area_ratio <= 0.3:
        rank += 0.5
    if len(profile.neighbors) <= 2:
        rank += 0.4
    if _is_corridor_like(profile):
        rank -= 0.9
    return rank


def _resolve_no_text_confidence(
    room_type: RoomType,
    scores: dict[RoomType, float],
) -> tuple[float, bool]:
    ranked = sorted(scores.items(), key=lambda item: item[1], reverse=True)
    if room_type == RoomType.GENERIC:
        chosen_score = ranked[0][1] if ranked else 0.0
        second_score = ranked[1][1] if len(ranked) > 1 else 0.0
    else:
        chosen_score = scores[room_type]
        second_score = max((score for kind, score in ranked if kind != room_type), default=0.0)
    gap = chosen_score - second_score
    degraded = room_type == RoomType.GENERIC or chosen_score < 2.1
    return _semantic_confidence(chosen_score, gap, False, False, degraded), degraded


def _is_corridor_like(profile: RoomSemanticProfile) -> bool:
    return profile.aspect > 2.4 or profile.aspect < 0.42 or profile.compactness < 0.62


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
