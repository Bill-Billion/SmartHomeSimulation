from __future__ import annotations

import base64
import json
import logging
from pathlib import Path
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .domain_rules import default_room_name, merge_warnings
from .geometry import iter_polygon_edges, point_segment_distance, room_center, segment_overlap
from .llm_api_utils import extract_json_object, extract_message_content, normalize_chat_endpoint
from .models import FloorplanSpec, OpeningSpec, Point2D, RoomSpec, RoomType, SceneSpec, WallKind

logger = logging.getLogger(__name__)


class LlmRequestConfig(BaseModel):
    enabled: bool = False
    base_url: str | None = None
    model: str | None = None
    api_key: str | None = None

    def ready(self) -> bool:
        return bool(self.enabled and self.base_url and self.model and self.api_key)


class RoomOverride(BaseModel):
    model_config = ConfigDict(extra="forbid")

    room_id: str
    room_type: RoomType
    name: str | None = None
    confidence: float | None = None
    reason: str | None = None


class FurnitureHint(BaseModel):
    model_config = ConfigDict(extra="forbid")

    room_id: str
    wall_preference: str = Field(pattern="^(north|south|east|west|longest|center)$")
    confidence: float = 0.68
    reason: str | None = None


class LlmEnhancementResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    room_overrides: list[RoomOverride] = Field(default_factory=list)
    furniture_hints: list[FurnitureHint] = Field(default_factory=list)
    extra_warnings: list[str] = Field(default_factory=list)


class LlmEnhancementFailure(RuntimeError):
    """把 AI 失败压成可诊断的类别，便于 warning 和服务端日志同时复用。"""

    def __init__(self, *, category: str, user_warning: str, log_message: str):
        super().__init__(user_warning)
        self.category = category
        self.user_warning = user_warning
        self.log_message = log_message


def apply_scene_llm_enhancements(
    source_path: Path,
    floorplan: FloorplanSpec,
    draft_scene: SceneSpec,
    config: LlmRequestConfig,
) -> tuple[FloorplanSpec, dict[str, str]]:
    if not config.enabled:
        return floorplan, {}

    if not config.ready():
        updated_floorplan = floorplan.model_copy(
            update={
                "warnings": merge_warnings(
                    floorplan.warnings,
                    ["AI 辅助配置不完整，已按规则结果继续生成。"],
                )
            }
        )
        return updated_floorplan, {}

    try:
        evidence = _build_evidence_payload(source_path, floorplan, draft_scene)
        result = _request_llm_enhancement(config, evidence)
    except LlmEnhancementFailure as exc:
        logger.warning("LLM enhancement failed [%s]: %s", exc.category, exc.log_message)
        updated_floorplan = floorplan.model_copy(
            update={
                "warnings": merge_warnings(
                    floorplan.warnings,
                    [exc.user_warning],
                )
            }
        )
        return updated_floorplan, {}
    except Exception:  # noqa: BLE001
        logger.exception("LLM enhancement crashed with an unexpected error")
        updated_floorplan = floorplan.model_copy(
            update={
                "warnings": merge_warnings(
                    floorplan.warnings,
                    ["AI 辅助处理异常，已按规则结果继续生成。"],
                )
            }
        )
        return updated_floorplan, {}

    updated_rooms, room_warnings = _apply_room_overrides(floorplan.rooms, result.room_overrides)
    furniture_hints, furniture_warnings = _collect_furniture_hints(result.furniture_hints, floorplan.rooms)
    ai_warnings = _sanitize_ai_warnings(result.extra_warnings)

    updated_floorplan = floorplan.model_copy(
        update={
            "rooms": updated_rooms,
            "warnings": merge_warnings(
                floorplan.warnings,
                [*room_warnings, *furniture_warnings, *ai_warnings],
            ),
        }
    )
    return updated_floorplan, furniture_hints


def _request_llm_enhancement(config: LlmRequestConfig, evidence: dict[str, Any]) -> LlmEnhancementResult:
    endpoint = normalize_chat_endpoint(config.base_url or "")
    prompt = _build_llm_prompt(evidence)
    message_content: Any = prompt
    thumbnail = evidence.get("thumbnail_data_url")
    if thumbnail:
        message_content = [
            {"type": "text", "text": prompt},
            {"type": "image_url", "image_url": {"url": thumbnail}},
        ]

    payload = {
        "model": config.model,
        "temperature": 0.1,
        "max_tokens": 900,
        "messages": [
            {
                "role": "system",
                "content": (
                    "你是户型语义与家具布置增强器。只能输出一个 JSON 对象。"
                    "你只能返回 room_overrides、furniture_hints、extra_warnings 三个字段。"
                    "不能修改墙体、开口、房间轮廓、场景尺寸。"
                    "当证据不足时返回空数组，不要编造。"
                ),
            },
            {
                "role": "user",
                "content": message_content,
            },
        ],
    }

    try:
        with httpx.Client(timeout=18.0) as client:
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
        raise LlmEnhancementFailure(
            category="timeout",
            user_warning="AI 服务请求超时，已按规则结果继续生成。",
            log_message=f"endpoint={endpoint}",
        ) from exc
    except httpx.ConnectError as exc:
        raise LlmEnhancementFailure(
            category="connect_error",
            user_warning="AI 服务连接失败，已按规则结果继续生成。",
            log_message=f"endpoint={endpoint}",
        ) from exc
    except httpx.HTTPStatusError as exc:
        raise _map_http_status_failure(exc.response, endpoint) from exc
    except httpx.HTTPError as exc:
        raise LlmEnhancementFailure(
            category="request_error",
            user_warning="AI 服务调用失败，已按规则结果继续生成。",
            log_message=f"endpoint={endpoint} error={exc.__class__.__name__}",
        ) from exc

    try:
        response_payload = response.json()
    except ValueError as exc:
        raise LlmEnhancementFailure(
            category="response_not_json",
            user_warning="AI 服务返回的响应不是合法 JSON，已按规则结果继续生成。",
            log_message=f"endpoint={endpoint} response={_response_excerpt(response)}",
        ) from exc

    try:
        content = extract_message_content(response_payload)
    except ValueError as exc:
        raise LlmEnhancementFailure(
            category="missing_content",
            user_warning="AI 返回内容不完整，已按规则结果继续生成。",
            log_message=f"endpoint={endpoint} payload_keys={sorted(response_payload.keys())}",
        ) from exc

    try:
        data = extract_json_object(content)
    except (ValueError, json.JSONDecodeError) as exc:
        raise LlmEnhancementFailure(
            category="content_not_json",
            user_warning="AI 返回的内容不是 JSON 格式，已按规则结果继续生成。",
            log_message=f"endpoint={endpoint} content={content[:180]!r}",
        ) from exc

    try:
        return LlmEnhancementResult.model_validate(data)
    except ValidationError as exc:
        raise LlmEnhancementFailure(
            category="schema_validation",
            user_warning="AI 返回结构不符合约束，已按规则结果继续生成。",
            log_message=f"endpoint={endpoint} errors={exc.errors()}",
        ) from exc


def _build_llm_prompt(evidence: dict[str, Any]) -> str:
    return (
        "请基于下面的户型证据包，只做理解层增强。\n"
        "任务 1：如果某个房间现在是 generic，但你有足够证据，可以给出 room_overrides。\n"
        "任务 2：如果家具沿某面墙摆放会更自然，可以给出 furniture_hints。\n"
        "任务 3：如果结果仍有明显不确定性，用 extra_warnings 给出自然语言提示。\n"
        "严格返回 JSON，不要加 Markdown。\n"
        "JSON 结构示例："
        '{"room_overrides":[{"room_id":"room_1","room_type":"kitchen","name":"厨房 1","confidence":0.84,"reason":"..." }],'
        '"furniture_hints":[{"room_id":"room_1","wall_preference":"east","confidence":0.76,"reason":"..." }],'
        '"extra_warnings":["..."]}\n'
        f"证据包如下：\n{json.dumps(evidence, ensure_ascii=False, indent=2)}"
    )


def _build_evidence_payload(
    source_path: Path,
    floorplan: FloorplanSpec,
    draft_scene: SceneSpec,
) -> dict[str, Any]:
    room_adjacency = _compute_room_adjacency(floorplan.rooms)
    outer_exposure = _compute_outer_exposure(floorplan.rooms, draft_scene)
    room_openings = _count_room_openings(floorplan.rooms, floorplan.openings)

    rooms_payload = []
    for room in floorplan.rooms:
        center = room_center(room.polygon)
        rooms_payload.append(
            {
                "room_id": room.room_id,
                "name": room.name,
                "room_type": room.room_type.value,
                "confidence": room.confidence,
                "area_sqm": round(room.area_sqm, 3),
                "center": {"x": round(center.x, 3), "z": round(center.z, 3)},
                "polygon": [{"x": round(point.x, 3), "z": round(point.z, 3)} for point in room.polygon],
                "neighbors": room_adjacency.get(room.room_id, []),
                "outer_exposure_edges": outer_exposure.get(room.room_id, 0),
                "opening_count": room_openings.get(room.room_id, 0),
            }
        )

    return {
        "source_type": floorplan.source_type.value,
        "bounds_width_m": floorplan.bounds_width_m,
        "bounds_depth_m": floorplan.bounds_depth_m,
        "warnings": floorplan.warnings,
        "rooms": rooms_payload,
        "scene": {
            "camera": draft_scene.camera.model_dump(mode="json"),
            "furnitures": [
                {
                    "room_id": furniture.room_id,
                    "kind": furniture.kind,
                    "position": furniture.position.model_dump(mode="json"),
                    "rotation_deg": furniture.rotation_deg,
                }
                for furniture in draft_scene.furnitures
            ],
        },
        "thumbnail_data_url": _build_thumbnail_data_url(source_path),
    }


def _build_thumbnail_data_url(source_path: Path) -> str | None:
    suffix = source_path.suffix.lower()
    if suffix not in {".png", ".jpg", ".jpeg", ".pdf"}:
        return None

    if suffix == ".pdf":
        import fitz

        doc = fitz.open(source_path)
        page = doc.load_page(0)
        pix = page.get_pixmap(matrix=fitz.Matrix(1.2, 1.2), alpha=False)
        image_bytes = pix.tobytes("png")
        doc.close()
        return f"data:image/png;base64,{base64.b64encode(image_bytes).decode('ascii')}"

    import cv2

    image = cv2.imread(str(source_path))
    if image is None:
        return None
    height, width = image.shape[:2]
    max_side = max(height, width)
    if max_side > 960:
        scale = 960 / max_side
        image = cv2.resize(image, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    ok, encoded = cv2.imencode(".png", image)
    if not ok:
        return None
    return f"data:image/png;base64,{base64.b64encode(encoded.tobytes()).decode('ascii')}"


def _compute_room_adjacency(rooms: list[RoomSpec]) -> dict[str, list[str]]:
    adjacency = {room.room_id: [] for room in rooms}
    for index, room in enumerate(rooms):
        for other in rooms[index + 1 :]:
            if _rooms_are_adjacent(room, other):
                adjacency[room.room_id].append(other.room_id)
                adjacency[other.room_id].append(room.room_id)
    return adjacency


def _rooms_are_adjacent(room: RoomSpec, other: RoomSpec) -> bool:
    for start, end in iter_polygon_edges(room.polygon):
        for other_start, other_end in iter_polygon_edges(other.polygon):
            if _shared_edge_length(start, end, other_start, other_end) >= 0.42:
                return True
    return False


def _shared_edge_length(start: Point2D, end: Point2D, other_start: Point2D, other_end: Point2D) -> float:
    if abs(start.x - end.x) < 1e-6 and abs(other_start.x - other_end.x) < 1e-6 and abs(start.x - other_start.x) < 0.08:
        return segment_overlap(start.z, end.z, other_start.z, other_end.z)
    if abs(start.z - end.z) < 1e-6 and abs(other_start.z - other_end.z) < 1e-6 and abs(start.z - other_start.z) < 0.08:
        return segment_overlap(start.x, end.x, other_start.x, other_end.x)
    return 0.0


def _compute_outer_exposure(rooms: list[RoomSpec], scene: SceneSpec) -> dict[str, int]:
    exposures = {room.room_id: 0 for room in rooms}
    outer_walls = [wall for wall in scene.walls if wall.kind == WallKind.OUTER]
    for room in rooms:
        for start, end in iter_polygon_edges(room.polygon):
            midpoint = Point2D(x=(start.x + end.x) / 2, z=(start.z + end.z) / 2)
            if any(point_segment_distance(midpoint, wall.start, wall.end) <= 0.12 for wall in outer_walls):
                exposures[room.room_id] += 1
    return exposures


def _count_room_openings(rooms: list[RoomSpec], openings: list[OpeningSpec]) -> dict[str, int]:
    counts = {room.room_id: 0 for room in rooms}
    for opening in openings:
        for room in rooms:
            if _point_touches_polygon(opening.center, room.polygon):
                counts[room.room_id] += 1
    return counts


def _point_touches_polygon(point: Point2D, polygon: list[Point2D]) -> bool:
    return any(
        point_segment_distance(point, start, end) <= 0.18
        for start, end in iter_polygon_edges(polygon)
    )


def _apply_room_overrides(
    rooms: list[RoomSpec],
    overrides: list[RoomOverride],
) -> tuple[list[RoomSpec], list[str]]:
    warnings: list[str] = []
    overrides_by_id = {override.room_id: override for override in overrides}
    updated_rooms: list[RoomSpec] = []

    for index, room in enumerate(rooms, start=1):
        override = overrides_by_id.get(room.room_id)
        if override is None:
            updated_rooms.append(room)
            continue

        if room.room_type != RoomType.GENERIC and room.confidence >= 0.86 and override.room_type != room.room_type:
            warnings.append(f"{room.name} 已有较强规则证据，已忽略 AI 的类型改写。")
            updated_rooms.append(room)
            continue

        confidence = room.confidence
        if override.confidence is not None:
            confidence = round(min(max(override.confidence, 0.55), 0.92), 3)
        name = override.name.strip() if override.name else default_room_name(override.room_type, index)
        updated_rooms.append(
            room.model_copy(
                update={
                    "room_type": override.room_type,
                    "name": name,
                    "confidence": confidence,
                }
            )
        )
    return updated_rooms, warnings


def _collect_furniture_hints(
    hints: list[FurnitureHint],
    rooms: list[RoomSpec],
) -> tuple[dict[str, str], list[str]]:
    room_ids = {room.room_id for room in rooms}
    accepted: dict[str, str] = {}
    warnings: list[str] = []
    for hint in hints:
        if hint.room_id not in room_ids:
            warnings.append("AI 返回了未识别房间的家具建议，已忽略。")
            continue
        if hint.confidence < 0.45:
            warnings.append("部分 AI 家具建议置信度不足，已忽略。")
            continue
        accepted[hint.room_id] = hint.wall_preference
    return accepted, warnings


def _sanitize_ai_warnings(warnings: list[str]) -> list[str]:
    sanitized = []
    for warning in warnings:
        text = str(warning).strip()
        if not text:
            continue
        if len(text) > 96:
            text = text[:93].rstrip() + "..."
        sanitized.append(f"AI 辅助提示：{text}")
    return sanitized


def _map_http_status_failure(response: httpx.Response, endpoint: str) -> LlmEnhancementFailure:
    status_code = response.status_code
    excerpt = _response_excerpt(response)
    if status_code in {401, 403}:
        return LlmEnhancementFailure(
            category="auth_failed",
            user_warning="AI 服务鉴权失败，已按规则结果继续生成。",
            log_message=f"endpoint={endpoint} status={status_code} response={excerpt}",
        )
    if status_code == 404:
        return LlmEnhancementFailure(
            category="endpoint_not_found",
            user_warning="AI 服务地址、接口路径或模型名称不兼容，已按规则结果继续生成。",
            log_message=f"endpoint={endpoint} status=404 response={excerpt}",
        )
    if status_code in {400, 415, 422} and _looks_like_image_capability_error(excerpt):
        return LlmEnhancementFailure(
            category="image_not_supported",
            user_warning="当前 AI 服务不支持图片输入，已按规则结果继续生成。",
            log_message=f"endpoint={endpoint} status={status_code} response={excerpt}",
        )
    if status_code in {400, 415, 422}:
        return LlmEnhancementFailure(
            category="request_rejected",
            user_warning="AI 服务拒绝了当前增强请求，已按规则结果继续生成。",
            log_message=f"endpoint={endpoint} status={status_code} response={excerpt}",
        )
    return LlmEnhancementFailure(
        category="http_error",
        user_warning=f"AI 服务返回异常状态 {status_code}，已按规则结果继续生成。",
        log_message=f"endpoint={endpoint} status={status_code} response={excerpt}",
    )


def _response_excerpt(response: httpx.Response) -> str:
    text = response.text.strip().replace("\n", " ")
    if len(text) > 220:
        text = text[:217].rstrip() + "..."
    return text


def _looks_like_image_capability_error(text: str) -> bool:
    lowered = text.lower()
    markers = (
        "image_url",
        "image input",
        "does not support images",
        "vision",
        "multimodal",
        "unsupported image",
        "only text",
        "text-only",
        "input_image",
        "unsupported content type",
    )
    return any(marker in lowered for marker in markers)

