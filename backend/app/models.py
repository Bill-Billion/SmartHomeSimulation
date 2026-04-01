from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from .config import DEFAULT_WALL_HEIGHT_M, DEFAULT_WALL_THICKNESS_M


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class SourceType(str, Enum):
    JPG = "jpg"
    JPEG = "jpeg"
    PNG = "png"
    PDF = "pdf"
    DXF = "dxf"
    DWG = "dwg"


class JobStatus(str, Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


class SimulationSessionStatus(str, Enum):
    ACTIVE = "active"
    CLOSED = "closed"


class RoomType(str, Enum):
    BEDROOM = "bedroom"
    LIVING_ROOM = "living_room"
    KITCHEN = "kitchen"
    BATHROOM = "bathroom"
    CORRIDOR = "corridor"
    GENERIC = "generic"


class OpeningType(str, Enum):
    DOOR = "door"
    WINDOW = "window"


class WallKind(str, Enum):
    OUTER = "outer"
    INNER = "inner"


class DeviceType(str, Enum):
    LIGHT = "light"


class LightOperation(str, Enum):
    TURN_ON = "turn_on"
    TURN_OFF = "turn_off"


class Point2D(BaseModel):
    x: float
    z: float


class Point3D(BaseModel):
    x: float
    y: float
    z: float


class WallSegment(BaseModel):
    wall_id: str
    start: Point2D
    end: Point2D
    kind: WallKind


class OpeningSpec(BaseModel):
    opening_id: str
    wall_id: str
    kind: OpeningType
    center: Point2D
    width_m: float
    base_height_m: float
    top_height_m: float


class RoomSpec(BaseModel):
    room_id: str
    name: str
    room_type: RoomType
    polygon: List[Point2D]
    area_sqm: float
    confidence: float = 0.5


class FloorplanSpec(BaseModel):
    source_type: SourceType
    wall_height_m: float = DEFAULT_WALL_HEIGHT_M
    wall_thickness_m: float = DEFAULT_WALL_THICKNESS_M
    bounds_width_m: float
    bounds_depth_m: float
    scale_m_per_unit: float
    outer_walls: List[WallSegment] = Field(default_factory=list)
    inner_walls: List[WallSegment] = Field(default_factory=list)
    openings: List[OpeningSpec] = Field(default_factory=list)
    rooms: List[RoomSpec] = Field(default_factory=list)
    confidence: float = 0.5
    warnings: List[str] = Field(default_factory=list)


class SurfaceSpec(BaseModel):
    surface_id: str
    room_id: str
    polygon: List[Point2D]
    elevation_m: float
    thickness_m: float
    material: str


class FurnitureSpec(BaseModel):
    furniture_id: str
    room_id: str
    kind: str
    position: Point3D
    size: Point3D
    rotation_deg: float = 0.0


class CameraSpec(BaseModel):
    position: Point3D
    target: Point3D
    fov: float = 48.0


class SceneSpec(BaseModel):
    scene_id: str
    resource_version: str
    source_type: SourceType
    bounds_width_m: float
    bounds_depth_m: float
    wall_height_m: float
    wall_thickness_m: float
    rooms: List[RoomSpec]
    walls: List[WallSegment]
    openings: List[OpeningSpec]
    floors: List[SurfaceSpec]
    ceilings: List[SurfaceSpec]
    furnitures: List[FurnitureSpec]
    camera: CameraSpec
    warnings: List[str] = Field(default_factory=list)


class DeviceState(BaseModel):
    device_id: str
    room_id: str
    device_type: DeviceType = DeviceType.LIGHT
    name: str
    is_on: bool = False
    brightness: int = Field(default=0, ge=0, le=100)
    color_temp: int = Field(default=3500, ge=2700, le=6500)


class RoomState(BaseModel):
    room_id: str
    room_name: str
    room_type: RoomType
    primary_light_id: str
    device_ids: List[str] = Field(default_factory=list)


class WorldState(BaseModel):
    session_id: str
    scene_id: str
    rooms: List[RoomState] = Field(default_factory=list)
    devices: List[DeviceState] = Field(default_factory=list)
    updated_at: str


class SimulationSession(BaseModel):
    session_id: str
    scene_id: str
    status: SimulationSessionStatus = SimulationSessionStatus.ACTIVE
    world_state: WorldState
    created_at: str
    updated_at: str
    warnings: List[str] = Field(default_factory=list)


class AgentTask(BaseModel):
    task_id: str
    trace_id: str
    session_id: str
    command: str
    operation: LightOperation
    target_room_id: str
    target_device_id: str
    created_at: str


class ActionProposal(BaseModel):
    proposal_id: str
    trace_id: str
    task_id: str
    agent_id: str
    target_device_id: str
    operation: LightOperation
    brightness: int | None = Field(default=None, ge=0, le=100)
    color_temp: int | None = Field(default=None, ge=2700, le=6500)
    reason: str = ""
    llm_used: bool = False


class ExecutionRecord(BaseModel):
    execution_id: str
    trace_id: str
    session_id: str
    proposal_id: str
    success: bool
    message: str
    before: DeviceState | None = None
    after: DeviceState | None = None
    created_at: str


class SimulationEvent(BaseModel):
    event_id: str
    trace_id: str
    session_id: str
    sequence: int
    kind: str
    actor: str
    message: str
    payload: Dict[str, Any] = Field(default_factory=dict)
    created_at: str


class CreateSimulationSessionRequest(BaseModel):
    scene_id: str


class SimulationCommandRequest(BaseModel):
    command: str
    llm_enabled: bool = False
    llm_base_url: str | None = None
    llm_model: str | None = None
    llm_api_key: str | None = None


class SimulationEventsPage(BaseModel):
    events: List[SimulationEvent] = Field(default_factory=list)
    next_cursor: int = 0
    has_more: bool = False


class RoomCandidateScore(BaseModel):
    room_type: RoomType
    score: float
    reason: str


class RoomDiagnostics(BaseModel):
    room_id: str
    name: str
    chosen_type: RoomType
    confidence: float
    top_candidates: List[RoomCandidateScore] = Field(default_factory=list)
    evidence_flags: List[str] = Field(default_factory=list)
    fallback_flags: List[str] = Field(default_factory=list)


class ParseSummary(BaseModel):
    source_type: SourceType
    confidence: float
    room_count: int
    wall_count: int
    opening_count: int
    warnings_count: int


class AiDiagnostics(BaseModel):
    enabled: bool = False
    status: str = "disabled"
    model: str | None = None
    failure_reason: str | None = None
    warning_count: int = 0


class DiagnosticsRecord(BaseModel):
    job_id: str
    scene_id: str | None = None
    parse_summary: ParseSummary
    room_diagnostics: List[RoomDiagnostics] = Field(default_factory=list)
    ai_diagnostics: AiDiagnostics = Field(default_factory=AiDiagnostics)
    warnings_by_stage: Dict[str, List[str]] = Field(default_factory=dict)
    created_at: str


class JobRecord(BaseModel):
    model_config = ConfigDict(protected_namespaces=())

    job_id: str
    status: JobStatus
    message: str
    created_at: str
    updated_at: str
    source_filename: str
    source_type: Optional[SourceType] = None
    llm_enabled: bool = False
    llm_model: Optional[str] = None
    scene_id: Optional[str] = None
    confidence: Optional[float] = None
    warnings: List[str] = Field(default_factory=list)
    error: Optional[str] = None
    scene_url: Optional[str] = None
    model_url: Optional[str] = None

    @classmethod
    def new(
        cls,
        source_filename: str,
        source_type: Optional[SourceType],
        llm_enabled: bool = False,
        llm_model: Optional[str] = None,
    ) -> "JobRecord":
        now = utc_now_iso()
        return cls(
            job_id=f"job_{uuid4().hex[:12]}",
            status=JobStatus.PENDING,
            message="任务已创建，等待处理。",
            created_at=now,
            updated_at=now,
            source_filename=source_filename,
            source_type=source_type,
            llm_enabled=llm_enabled,
            llm_model=llm_model,
        )
