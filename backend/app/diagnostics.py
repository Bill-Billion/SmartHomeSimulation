from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

from .geometry import iter_polygon_edges, point_segment_distance
from .models import (
    AiDiagnostics,
    DiagnosticsRecord,
    FloorplanSpec,
    JobRecord,
    ParseSummary,
    Point2D,
    RoomCandidateScore,
    RoomDiagnostics,
    RoomType,
    SceneSpec,
    SourceType,
    utc_now_iso,
)


def build_diagnostics_record(
    *,
    job: JobRecord,
    floorplan: FloorplanSpec,
    scene: SceneSpec,
    llm_enabled: bool = False,
    llm_model: str | None = None,
) -> DiagnosticsRecord:
    parse_summary = ParseSummary(
        source_type=floorplan.source_type,
        confidence=round(float(floorplan.confidence), 3),
        room_count=len(floorplan.rooms),
        wall_count=len(floorplan.outer_walls) + len(floorplan.inner_walls),
        opening_count=len(floorplan.openings),
        warnings_count=len(scene.warnings),
    )

    ai_warnings = [warning for warning in scene.warnings if "AI" in warning]
    ai_diagnostics = AiDiagnostics(
        enabled=llm_enabled,
        status=_ai_status(llm_enabled, ai_warnings),
        model=llm_model if llm_enabled else None,
        failure_reason=ai_warnings[0] if ai_warnings else None,
        warning_count=len(ai_warnings),
    )

    room_diagnostics = [
        _build_room_diagnostics(room, floorplan)
        for room in floorplan.rooms
    ]

    warnings_by_stage = {
        "parse": floorplan.warnings,
        "scene": scene.warnings,
        "ai": ai_warnings,
    }

    return DiagnosticsRecord(
        job_id=job.job_id,
        scene_id=job.scene_id,
        parse_summary=parse_summary,
        room_diagnostics=room_diagnostics,
        ai_diagnostics=ai_diagnostics,
        warnings_by_stage=warnings_by_stage,
        created_at=utc_now_iso(),
    )


def build_source_preview_png(source_path: Path, source_type: SourceType | None) -> bytes:
    if source_type == SourceType.PDF or source_path.suffix.lower() == ".pdf":
        try:
            import fitz

            document = fitz.open(source_path)
            page = document.load_page(0)
            pix = page.get_pixmap(matrix=fitz.Matrix(1.5, 1.5), alpha=False)
            image_bytes = pix.tobytes("png")
            document.close()
            return image_bytes
        except Exception:  # noqa: BLE001
            return _build_unavailable_preview(
                "PDF source preview failed.",
                "Use generated 2D/3D result for diagnostics.",
            )

    if source_type in {SourceType.PNG, SourceType.JPG, SourceType.JPEG}:
        image = cv2.imread(str(source_path))
        if image is None:
            return _build_unavailable_preview(
                "Image source preview failed.",
                "Use generated 2D/3D result for diagnostics.",
            )
        return _encode_png(_resize_if_needed(image))

    if source_type in {SourceType.DXF, SourceType.DWG}:
        return _build_dxf_fallback_preview()

    image = cv2.imread(str(source_path))
    if image is None:
        return _build_unavailable_preview(
            "Source preview is not available.",
            "Use generated 2D/3D result for diagnostics.",
        )
    return _encode_png(_resize_if_needed(image))


def _build_room_diagnostics(room, floorplan: FloorplanSpec) -> RoomDiagnostics:
    confidence = round(float(room.confidence), 3)
    chosen_type = room.room_type
    fallback_flags: list[str] = []
    evidence_flags: list[str] = []

    if confidence >= 0.8:
        evidence_flags.append("high_confidence")
    if room.room_type == RoomType.GENERIC:
        fallback_flags.append("generic_fallback")

    opening_count = 0
    for opening in floorplan.openings:
        if _point_near_polygon(opening.center.x, opening.center.z, room.polygon):
            opening_count += 1
    if opening_count > 0:
        evidence_flags.append("opening_nearby")

    candidates = [
        RoomCandidateScore(
            room_type=chosen_type,
            score=confidence,
            reason="规则链输出",
        )
    ]
    if chosen_type != RoomType.GENERIC:
        candidates.append(
            RoomCandidateScore(
                room_type=RoomType.GENERIC,
                score=round(max(0.35, confidence - 0.24), 3),
                reason="降级候选",
            )
        )

    return RoomDiagnostics(
        room_id=room.room_id,
        name=room.name,
        chosen_type=chosen_type,
        confidence=confidence,
        top_candidates=candidates,
        evidence_flags=evidence_flags,
        fallback_flags=fallback_flags,
    )


def _point_near_polygon(x: float, z: float, polygon) -> bool:
    for start, end in iter_polygon_edges(polygon):
        if point_segment_distance(Point2D(x=x, z=z), start, end) <= 0.18:
            return True
    return False


def _ai_status(llm_enabled: bool, ai_warnings: list[str]) -> str:
    if not llm_enabled:
        return "disabled"
    if ai_warnings:
        return "fallback"
    return "applied_or_rule_compatible"


def _resize_if_needed(image: np.ndarray) -> np.ndarray:
    height, width = image.shape[:2]
    max_side = max(height, width)
    if max_side <= 1500:
        return image
    scale = 1500.0 / float(max_side)
    return cv2.resize(image, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)


def _encode_png(image: np.ndarray) -> bytes:
    ok, encoded = cv2.imencode(".png", image)
    if not ok:
        raise ValueError("预览图编码失败。")
    return encoded.tobytes()


def _build_dxf_fallback_preview() -> bytes:
    canvas = np.ones((720, 1080, 3), dtype=np.uint8) * 247
    cv2.rectangle(canvas, (40, 40), (1040, 680), (188, 188, 188), 2)
    cv2.putText(
        canvas,
        "DXF source preview is not available yet.",
        (80, 220),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.92,
        (52, 62, 58),
        2,
        cv2.LINE_AA,
    )
    cv2.putText(
        canvas,
        "The generated 2D/3D result can still be analyzed.",
        (80, 280),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.86,
        (76, 90, 86),
        2,
        cv2.LINE_AA,
    )
    return _encode_png(canvas)


def _build_unavailable_preview(title: str, subtitle: str) -> bytes:
    canvas = np.ones((720, 1080, 3), dtype=np.uint8) * 247
    cv2.rectangle(canvas, (40, 40), (1040, 680), (188, 188, 188), 2)
    cv2.putText(
        canvas,
        title,
        (80, 220),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.9,
        (52, 62, 58),
        2,
        cv2.LINE_AA,
    )
    cv2.putText(
        canvas,
        subtitle,
        (80, 280),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.78,
        (76, 90, 86),
        2,
        cv2.LINE_AA,
    )
    return _encode_png(canvas)
