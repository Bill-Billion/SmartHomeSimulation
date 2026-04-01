from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.app.main import create_app
from backend.app.models import (
    CameraSpec,
    CreateSimulationSessionRequest,
    Point2D,
    Point3D,
    RoomSpec,
    RoomType,
    SceneSpec,
    SimulationCommandRequest,
    SimulationSessionStatus,
    SourceType,
    SurfaceSpec,
)
from backend.app.simulation_agents import AgentRegistry, Orchestrator
from backend.app.simulation_runtime import SimulationRuntime
from backend.app.simulation_world import LightweightWorldEngine
from backend.app.storage import FileStorage


def _build_demo_scene(scene_id: str = "scene_demo") -> SceneSpec:
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
    rooms = [
        RoomSpec(
            room_id="room_01",
            name="卧室 1",
            room_type=RoomType.BEDROOM,
            polygon=room_1_polygon,
            area_sqm=24.0,
            confidence=0.81,
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
    floors = [
        SurfaceSpec(
            surface_id="floor_room_01",
            room_id="room_01",
            polygon=room_1_polygon,
            elevation_m=0.0,
            thickness_m=0.02,
            material="floor_bedroom",
        ),
        SurfaceSpec(
            surface_id="floor_room_02",
            room_id="room_02",
            polygon=room_2_polygon,
            elevation_m=0.0,
            thickness_m=0.02,
            material="floor_living_room",
        ),
    ]
    ceilings = [
        SurfaceSpec(
            surface_id="ceiling_room_01",
            room_id="room_01",
            polygon=room_1_polygon,
            elevation_m=2.76,
            thickness_m=0.04,
            material="ceiling_default",
        ),
        SurfaceSpec(
            surface_id="ceiling_room_02",
            room_id="room_02",
            polygon=room_2_polygon,
            elevation_m=2.76,
            thickness_m=0.04,
            material="ceiling_default",
        ),
    ]
    return SceneSpec(
        scene_id=scene_id,
        resource_version="v0.1.6",
        source_type=SourceType.PNG,
        bounds_width_m=8.0,
        bounds_depth_m=6.0,
        wall_height_m=2.8,
        wall_thickness_m=0.2,
        rooms=rooms,
        walls=[],
        openings=[],
        floors=floors,
        ceilings=ceilings,
        furnitures=[],
        camera=CameraSpec(
            position=Point3D(x=6.2, y=6.0, z=7.0),
            target=Point3D(x=0.0, y=0.7, z=0.0),
            fov=42.0,
        ),
        warnings=[],
    )


def test_lightweight_world_engine_builds_one_main_light_per_room() -> None:
    scene = _build_demo_scene("scene_world")
    engine = LightweightWorldEngine()

    world = engine.build_initial_state(session_id="sim_test", scene=scene)

    assert len(world.rooms) == len(scene.rooms)
    assert len(world.devices) == len(scene.rooms)
    assert all(device.device_id.startswith("light_") for device in world.devices)
    assert all(not device.is_on for device in world.devices)
    assert all(device.brightness == 0 for device in world.devices)


def test_orchestrator_parses_open_light_command() -> None:
    scene = _build_demo_scene("scene_orchestrator")
    storage = FileStorage(Path("/tmp/smart_home_test_orchestrator"))
    storage.save_scene(scene)
    engine = LightweightWorldEngine()
    session = SimulationRuntime(storage, engine).create_session(
        request=CreateSimulationSessionRequest(scene_id=scene.scene_id)
    )
    registry = AgentRegistry()
    orchestrator = Orchestrator(registry)
    task = orchestrator.create_task(
        session=session,
        command="打开卧室 1 的灯",
        trace_id="trace_demo",
    )

    assert task.operation.value == "turn_on"
    assert task.target_room_id == "room_01"
    assert task.target_device_id == "light_room_01_main"


def test_runtime_command_updates_world_state_and_events(tmp_path: Path) -> None:
    storage = FileStorage(tmp_path / "runtime")
    scene = _build_demo_scene("scene_runtime")
    storage.save_scene(scene)
    runtime = SimulationRuntime(storage)

    session = runtime.create_session(request=CreateSimulationSessionRequest(scene_id=scene.scene_id))
    updated_session, execution = runtime.execute_command(
        session_id=session.session_id,
        request=SimulationCommandRequest(command="打开卧室 1 的灯"),
    )

    assert execution.success
    target = next(device for device in updated_session.world_state.devices if device.room_id == "room_01")
    assert target.is_on
    assert target.brightness > 0

    page = runtime.read_events(session_id=session.session_id, cursor=0, limit=20)
    kinds = [event.kind for event in page.events]
    assert "command.received" in kinds
    assert "proposal.created" in kinds
    assert "action.executed" in kinds


def test_runtime_create_session_event_log_failure_does_not_break_main_flow(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    storage = FileStorage(tmp_path / "runtime_create_session_event_failure")
    scene = _build_demo_scene("scene_runtime_create_session_event_failure")
    storage.save_scene(scene)
    runtime = SimulationRuntime(storage)

    def _raise_on_append(**_: object) -> None:
        raise OSError("event log unavailable")

    monkeypatch.setattr(runtime.events, "append", _raise_on_append)
    session = runtime.create_session(request=CreateSimulationSessionRequest(scene_id=scene.scene_id))
    persisted = runtime.get_session(session_id=session.session_id)

    assert any("事件日志写入失败" in warning for warning in persisted.warnings)


def test_runtime_does_not_write_action_executed_event_when_session_save_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    storage = FileStorage(tmp_path / "runtime_save_failure")
    scene = _build_demo_scene("scene_runtime_save_failure")
    storage.save_scene(scene)
    runtime = SimulationRuntime(storage)
    session = runtime.create_session(request=CreateSimulationSessionRequest(scene_id=scene.scene_id))

    original_save = runtime.sessions.save
    save_called = {"count": 0}

    def _raise_on_first_save(*, session: object) -> None:
        if save_called["count"] == 0:
            save_called["count"] += 1
            raise OSError("session write failed")
        original_save(session=session)

    monkeypatch.setattr(runtime.sessions, "save", _raise_on_first_save)
    with pytest.raises(OSError):
        runtime.execute_command(
            session_id=session.session_id,
            request=SimulationCommandRequest(command="打开卧室 1 的灯"),
        )

    events = runtime.read_events(session_id=session.session_id, cursor=0, limit=200).events
    assert all(event.kind != "action.executed" for event in events)


def test_runtime_llm_toggle_without_config_falls_back_to_rule_with_warning(tmp_path: Path) -> None:
    storage = FileStorage(tmp_path / "runtime_llm")
    scene = _build_demo_scene("scene_runtime_llm")
    storage.save_scene(scene)
    runtime = SimulationRuntime(storage)

    session = runtime.create_session(request=CreateSimulationSessionRequest(scene_id=scene.scene_id))
    updated_session, execution = runtime.execute_command(
        session_id=session.session_id,
        request=SimulationCommandRequest(
            command="打开客厅 1 的灯",
            llm_enabled=True,
        ),
    )

    assert execution.success
    assert any("AI 微调配置不完整" in warning for warning in updated_session.warnings)
    target = next(device for device in updated_session.world_state.devices if device.room_id == "room_02")
    assert target.is_on


def test_runtime_rejects_command_when_session_closed(tmp_path: Path) -> None:
    storage = FileStorage(tmp_path / "runtime_closed")
    scene = _build_demo_scene("scene_runtime_closed")
    storage.save_scene(scene)
    runtime = SimulationRuntime(storage)
    session = runtime.create_session(request=CreateSimulationSessionRequest(scene_id=scene.scene_id))
    session.status = SimulationSessionStatus.CLOSED
    runtime.sessions.save(session=session)

    with pytest.raises(ValueError) as exc_info:
        runtime.execute_command(
            session_id=session.session_id,
            request=SimulationCommandRequest(command="打开卧室 1 的灯"),
        )
    assert "会话已关闭" in str(exc_info.value)


def test_runtime_events_pagination(tmp_path: Path) -> None:
    storage = FileStorage(tmp_path / "runtime_events")
    scene = _build_demo_scene("scene_runtime_events")
    storage.save_scene(scene)
    runtime = SimulationRuntime(storage)
    session = runtime.create_session(request=CreateSimulationSessionRequest(scene_id=scene.scene_id))

    runtime.execute_command(
        session_id=session.session_id,
        request=SimulationCommandRequest(command="打开卧室 1 的灯"),
    )
    runtime.execute_command(
        session_id=session.session_id,
        request=SimulationCommandRequest(command="关闭卧室 1 的灯"),
    )

    first = runtime.read_events(session_id=session.session_id, cursor=0, limit=3)
    second = runtime.read_events(session_id=session.session_id, cursor=first.next_cursor, limit=3)

    assert len(first.events) == 3
    assert first.has_more
    assert second.events
    assert second.next_cursor > first.next_cursor


def test_runtime_event_sequence_keeps_increasing_after_restart(tmp_path: Path) -> None:
    storage = FileStorage(tmp_path / "runtime_event_sequence")
    scene = _build_demo_scene("scene_runtime_event_sequence")
    storage.save_scene(scene)

    runtime_1 = SimulationRuntime(storage)
    session = runtime_1.create_session(request=CreateSimulationSessionRequest(scene_id=scene.scene_id))
    runtime_1.execute_command(
        session_id=session.session_id,
        request=SimulationCommandRequest(command="打开卧室 1 的灯"),
    )

    runtime_2 = SimulationRuntime(storage)
    runtime_2.execute_command(
        session_id=session.session_id,
        request=SimulationCommandRequest(command="关闭卧室 1 的灯"),
    )

    events = runtime_2.read_events(session_id=session.session_id, cursor=0, limit=200).events
    sequences = [event.sequence for event in events]
    assert sequences == list(range(len(sequences)))


def test_runtime_concurrent_commands_keep_sequence_unique(tmp_path: Path) -> None:
    storage = FileStorage(tmp_path / "runtime_concurrent_commands")
    scene = _build_demo_scene("scene_runtime_concurrent_commands")
    storage.save_scene(scene)
    runtime = SimulationRuntime(storage)
    session = runtime.create_session(request=CreateSimulationSessionRequest(scene_id=scene.scene_id))

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(
                runtime.execute_command,
                session_id=session.session_id,
                request=SimulationCommandRequest(command="打开卧室 1 的灯"),
            ),
            executor.submit(
                runtime.execute_command,
                session_id=session.session_id,
                request=SimulationCommandRequest(command="关闭卧室 1 的灯"),
            ),
        ]
        for future in futures:
            future.result()

    events = runtime.read_events(session_id=session.session_id, cursor=0, limit=200).events
    sequences = [event.sequence for event in events]
    assert sequences == list(range(len(sequences)))

    executed = [event for event in events if event.kind == "action.executed"]
    assert len(executed) == 2


def test_simulation_api_end_to_end(tmp_path: Path) -> None:
    runtime_root = tmp_path / "runtime_api"
    app = create_app(runtime_root)
    client = TestClient(app)
    scene = _build_demo_scene("scene_api")
    storage = FileStorage(runtime_root)
    storage.save_scene(scene)

    create_response = client.post(
        "/api/simulations:sessions",
        json={"scene_id": scene.scene_id},
    )
    assert create_response.status_code == 200
    session_id = create_response.json()["session_id"]

    command_response = client.post(
        f"/api/simulations/sessions/{session_id}/commands",
        json={"command": "打开卧室 1 的灯"},
    )
    assert command_response.status_code == 200
    payload = command_response.json()
    assert payload["execution"]["success"] is True
    assert payload["session"]["world_state"]["devices"][0]["is_on"] is True

    events_response = client.get(f"/api/simulations/sessions/{session_id}/events")
    assert events_response.status_code == 200
    events_payload = events_response.json()
    assert events_payload["events"]


def test_simulation_api_create_session_returns_404_when_scene_missing(tmp_path: Path) -> None:
    app = create_app(tmp_path / "runtime_api_missing_scene")
    client = TestClient(app)

    response = client.post(
        "/api/simulations:sessions",
        json={"scene_id": "scene_not_found"},
    )
    assert response.status_code == 404


def test_simulation_api_empty_command_returns_400(tmp_path: Path) -> None:
    runtime_root = tmp_path / "runtime_api_empty_command"
    app = create_app(runtime_root)
    client = TestClient(app)
    scene = _build_demo_scene("scene_api_empty_command")
    storage = FileStorage(runtime_root)
    storage.save_scene(scene)
    create_response = client.post(
        "/api/simulations:sessions",
        json={"scene_id": scene.scene_id},
    )
    session_id = create_response.json()["session_id"]

    response = client.post(
        f"/api/simulations/sessions/{session_id}/commands",
        json={"command": "  "},
    )
    assert response.status_code == 400
    assert "命令不能为空" in response.json()["detail"]


def test_simulation_api_invalid_events_cursor_returns_400(tmp_path: Path) -> None:
    runtime_root = tmp_path / "runtime_api_invalid_cursor"
    app = create_app(runtime_root)
    client = TestClient(app)
    scene = _build_demo_scene("scene_api_invalid_cursor")
    storage = FileStorage(runtime_root)
    storage.save_scene(scene)

    create_response = client.post(
        "/api/simulations:sessions",
        json={"scene_id": scene.scene_id},
    )
    session_id = create_response.json()["session_id"]

    command_response = client.post(
        f"/api/simulations/sessions/{session_id}/commands",
        json={"command": "打开卧室 1 的灯"},
    )
    assert command_response.status_code == 200

    response = client.get(f"/api/simulations/sessions/{session_id}/events?cursor=1&limit=10")
    assert response.status_code == 400
    assert "游标无效" in response.json()["detail"]
