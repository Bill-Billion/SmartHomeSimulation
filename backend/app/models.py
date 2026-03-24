from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import List, Optional
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


class JobRecord(BaseModel):
    model_config = ConfigDict(protected_namespaces=())

    job_id: str
    status: JobStatus
    message: str
    created_at: str
    updated_at: str
    source_filename: str
    source_type: Optional[SourceType] = None
    scene_id: Optional[str] = None
    confidence: Optional[float] = None
    warnings: List[str] = Field(default_factory=list)
    error: Optional[str] = None
    scene_url: Optional[str] = None
    model_url: Optional[str] = None

    @classmethod
    def new(cls, source_filename: str, source_type: Optional[SourceType]) -> "JobRecord":
        now = utc_now_iso()
        return cls(
            job_id=f"job_{uuid4().hex[:12]}",
            status=JobStatus.PENDING,
            message="任务已创建，等待处理。",
            created_at=now,
            updated_at=now,
            source_filename=source_filename,
            source_type=source_type,
        )
