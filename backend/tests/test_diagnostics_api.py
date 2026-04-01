from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
from fastapi.testclient import TestClient

from backend.app.main import create_app
from backend.app.models import (
    CameraSpec,
    FloorplanSpec,
    JobRecord,
    JobStatus,
    OpeningSpec,
    OpeningType,
    Point2D,
    Point3D,
    RoomSpec,
    RoomType,
    SceneSpec,
    SourceType,
    SurfaceSpec,
    WallKind,
    WallSegment,
)
from backend.app.storage import FileStorage


def _build_demo_floorplan(source_type: SourceType = SourceType.PNG) -> FloorplanSpec:
    room_1_polygon = [
        Point2D(x=-4.0, z=-3.0),
        Point2D(x=0.0, z=-3.0),
        Point2D(x=0.0, z=3.0),
        Point2D(x=-4.0, z=3.0),
    ]
    room_2_polygon = [
        Point2D(x=0.0, z=-3.0),
        Point2D(x=4.0, z=-3.0),
        Point2D(x=4.0, z=3.0),
        Point2D(x=0.0, z=3.0),
    ]

    outer_walls = [
        WallSegment(wall_id="w_outer_1", start=Point2D(x=-4.0, z=-3.0), end=Point2D(x=4.0, z=-3.0), kind=WallKind.OUTER),
        WallSegment(wall_id="w_outer_2", start=Point2D(x=4.0, z=-3.0), end=Point2D(x=4.0, z=3.0), kind=WallKind.OUTER),
        WallSegment(wall_id="w_outer_3", start=Point2D(x=4.0, z=3.0), end=Point2D(x=-4.0, z=3.0), kind=WallKind.OUTER),
        WallSegment(wall_id="w_outer_4", start=Point2D(x=-4.0, z=3.0), end=Point2D(x=-4.0, z=-3.0), kind=WallKind.OUTER),
    ]
    inner_walls = [
        WallSegment(wall_id="w_inner_1", start=Point2D(x=0.0, z=-3.0), end=Point2D(x=0.0, z=3.0), kind=WallKind.INNER),
    ]
    openings = [
        OpeningSpec(
            opening_id="door_01",
            wall_id="w_inner_1",
            kind=OpeningType.DOOR,
            center=Point2D(x=0.0, z=0.0),
            width_m=0.92,
            base_height_m=0.0,
            top_height_m=2.1,
        )
    ]

    rooms = [
        RoomSpec(
            room_id="room_01",
            name="卧室 1",
            room_type=RoomType.BEDROOM,
            polygon=room_1_polygon,
            area_sqm=24.0,
            confidence=0.82,
        ),
        RoomSpec(
            room_id="room_02",
            name="客厅 1",
            room_type=RoomType.LIVING_ROOM,
            polygon=room_2_polygon,
            area_sqm=24.0,
            confidence=0.79,
        ),
    ]

    return FloorplanSpec(
        source_type=source_type,
        bounds_width_m=8.0,
        bounds_depth_m=6.0,
        scale_m_per_unit=1.0,
        outer_walls=outer_walls,
        inner_walls=inner_walls,
        openings=openings,
        rooms=rooms,
        confidence=0.81,
        warnings=["解析已启用保守参数。"],
    )


def _build_demo_scene(scene_id: str, source_type: SourceType) -> SceneSpec:
    floorplan = _build_demo_floorplan(source_type)
    rooms = floorplan.rooms
    floors = [
        SurfaceSpec(
            surface_id="floor_room_01",
            room_id="room_01",
            polygon=rooms[0].polygon,
            elevation_m=0.0,
            thickness_m=0.02,
            material="floor_bedroom",
        ),
        SurfaceSpec(
            surface_id="floor_room_02",
            room_id="room_02",
            polygon=rooms[1].polygon,
            elevation_m=0.0,
            thickness_m=0.02,
            material="floor_living_room",
        ),
    ]
    ceilings = [
        SurfaceSpec(
            surface_id="ceiling_room_01",
            room_id="room_01",
            polygon=rooms[0].polygon,
            elevation_m=2.76,
            thickness_m=0.04,
            material="ceiling_default",
        ),
        SurfaceSpec(
            surface_id="ceiling_room_02",
            room_id="room_02",
            polygon=rooms[1].polygon,
            elevation_m=2.76,
            thickness_m=0.04,
            material="ceiling_default",
        ),
    ]
    return SceneSpec(
        scene_id=scene_id,
        resource_version="v0.1.6",
        source_type=source_type,
        bounds_width_m=8.0,
        bounds_depth_m=6.0,
        wall_height_m=2.8,
        wall_thickness_m=0.2,
        rooms=rooms,
        walls=[*floorplan.outer_walls, *floorplan.inner_walls],
        openings=floorplan.openings,
        floors=floors,
        ceilings=ceilings,
        furnitures=[],
        camera=CameraSpec(
            position=Point3D(x=6.2, y=6.0, z=7.0),
            target=Point3D(x=0.0, y=0.7, z=0.0),
            fov=42.0,
        ),
        warnings=["AI 服务请求超时，已按规则结果继续生成。"],
    )


def _create_completed_job(storage: FileStorage, *, source_type: SourceType, suffix: str):
    job = JobRecord.new(source_filename=f"demo{suffix}", source_type=source_type)
    storage.create_job(job)
    scene_id = f"scene_{job.job_id}"
    floorplan = _build_demo_floorplan(source_type)
    scene = _build_demo_scene(scene_id, source_type)
    storage.save_floorplan(job.job_id, floorplan)
    storage.save_scene(scene)

    if source_type in {SourceType.PNG, SourceType.JPG, SourceType.JPEG}:
        image = np.ones((720, 1080, 3), dtype=np.uint8) * 240
        ok, encoded = cv2.imencode(".png", image)
        assert ok
        storage.save_upload(job.job_id, f"source{suffix}", encoded.tobytes())
    else:
        storage.save_upload(job.job_id, f"source{suffix}", b"0")

    job.status = JobStatus.COMPLETED
    job.scene_id = scene_id
    job.scene_url = f"/api/scenes/{scene_id}"
    job.model_url = f"/api/scenes/{scene_id}/model.glb"
    storage.save_job(job)
    return job


def test_diagnostics_api_builds_record_for_completed_png_job(tmp_path: Path) -> None:
    root = tmp_path / "diagnostics_png"
    app = create_app(root)
    client = TestClient(app)
    storage = FileStorage(root)
    job = _create_completed_job(storage, source_type=SourceType.PNG, suffix=".png")

    response = client.get(f"/api/jobs/{job.job_id}/diagnostics")
    assert response.status_code == 200
    payload = response.json()
    assert payload["job_id"] == job.job_id
    assert payload["parse_summary"]["room_count"] == 2
    assert len(payload["room_diagnostics"]) == 2
    assert "warnings_by_stage" in payload

    preview = client.get(f"/api/jobs/{job.job_id}/source-preview.png")
    assert preview.status_code == 200
    assert preview.headers["content-type"].startswith("image/png")
    assert len(preview.content) > 256


def test_source_preview_returns_fallback_for_dxf(tmp_path: Path) -> None:
    root = tmp_path / "diagnostics_dxf"
    app = create_app(root)
    client = TestClient(app)
    storage = FileStorage(root)
    job = _create_completed_job(storage, source_type=SourceType.DXF, suffix=".dxf")

    preview = client.get(f"/api/jobs/{job.job_id}/source-preview.png")
    assert preview.status_code == 200
    assert preview.headers["content-type"].startswith("image/png")
    assert len(preview.content) > 256


def test_source_preview_returns_fallback_for_broken_pdf(tmp_path: Path) -> None:
    root = tmp_path / "diagnostics_pdf_broken"
    app = create_app(root)
    client = TestClient(app)
    storage = FileStorage(root)
    job = _create_completed_job(storage, source_type=SourceType.PDF, suffix=".pdf")

    preview = client.get(f"/api/jobs/{job.job_id}/source-preview.png")
    assert preview.status_code == 200
    assert preview.headers["content-type"].startswith("image/png")
    assert len(preview.content) > 256


def test_diagnostics_lazy_rebuild_keeps_scene_and_ai_flags(tmp_path: Path) -> None:
    root = tmp_path / "diagnostics_rebuild"
    app = create_app(root)
    client = TestClient(app)
    storage = FileStorage(root)
    job = _create_completed_job(storage, source_type=SourceType.PNG, suffix=".png")
    job.llm_enabled = True
    job.llm_model = "test-model"
    storage.save_job(job)

    first_response = client.get(f"/api/jobs/{job.job_id}/diagnostics")
    assert first_response.status_code == 200
    first_payload = first_response.json()
    assert first_payload["scene_id"] is not None
    assert first_payload["ai_diagnostics"]["enabled"] is True

    diagnostics_path = storage.job_dir(job.job_id) / "diagnostics.json"
    diagnostics_path.unlink(missing_ok=True)

    second_response = client.get(f"/api/jobs/{job.job_id}/diagnostics")
    assert second_response.status_code == 200
    second_payload = second_response.json()
    assert second_payload["scene_id"] == first_payload["scene_id"]
    assert second_payload["ai_diagnostics"]["enabled"] is True
    assert second_payload["ai_diagnostics"]["status"] == first_payload["ai_diagnostics"]["status"]


def test_diagnostics_api_rejects_unfinished_job(tmp_path: Path) -> None:
    root = tmp_path / "diagnostics_pending"
    app = create_app(root)
    client = TestClient(app)
    storage = FileStorage(root)
    job = JobRecord.new(source_filename="pending.png", source_type=SourceType.PNG)
    storage.create_job(job)

    response = client.get(f"/api/jobs/{job.job_id}/diagnostics")
    assert response.status_code == 409
