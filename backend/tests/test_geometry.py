from __future__ import annotations

from backend.app import parsers, scene_builder
from backend.app.geometry import (
    edge_signature,
    point_in_polygon,
    point_segment_distance,
    polygon_bounds,
    room_center,
    segment_overlap,
)
from backend.app.models import CameraSpec, Point2D, Point3D, RoomType, WallKind, WallSegment


def test_point_segment_distance_projects_to_segment() -> None:
    point = Point2D(x=1.0, z=1.0)
    start = Point2D(x=0.0, z=0.0)
    end = Point2D(x=2.0, z=0.0)

    assert round(point_segment_distance(point, start, end), 3) == 1.0


def test_segment_overlap_returns_shared_length() -> None:
    assert segment_overlap(0.0, 3.5, 2.0, 5.0) == 1.5
    assert segment_overlap(0.0, 1.0, 1.2, 2.0) == 0.0


def test_point_in_polygon_treats_boundary_as_inside() -> None:
    polygon = [
        Point2D(x=-1.0, z=-1.0),
        Point2D(x=1.0, z=-1.0),
        Point2D(x=1.0, z=1.0),
        Point2D(x=-1.0, z=1.0),
    ]

    assert point_in_polygon(Point2D(x=0.0, z=0.0), polygon)
    assert point_in_polygon(Point2D(x=1.0, z=0.0), polygon)


def test_room_center_and_polygon_bounds_are_stable() -> None:
    polygon = [
        Point2D(x=-2.0, z=-1.0),
        Point2D(x=2.0, z=-1.0),
        Point2D(x=2.0, z=1.0),
        Point2D(x=-2.0, z=1.0),
    ]

    center = room_center(polygon)
    assert center == Point2D(x=0.0, z=0.0)
    assert polygon_bounds(polygon) == (-2.0, 2.0, -1.0, 1.0)


def test_edge_signature_is_order_independent() -> None:
    start = Point2D(x=-0.1234, z=2.5678)
    end = Point2D(x=4.3219, z=-0.1114)

    assert edge_signature(start, end) == edge_signature(end, start)


def test_parsers_facade_keeps_legacy_helper_exports() -> None:
    axis_edge = parsers.AxisEdge(
        edge_id="edge_001",
        room_id="room_01",
        orientation="horizontal",
        axis=0.0,
        start=0.0,
        end=2.0,
    )

    assert axis_edge.room_id == "room_01"
    assert parsers._normalize_room_semantic_text("WC") == RoomType.BATHROOM
    assert callable(parsers._extract_raster_semantic_hints)


def test_parsers_facade_accepts_legacy_fragment_kind_signature() -> None:
    edge = parsers.AxisEdge(
        edge_id="edge_002",
        room_id="room_02",
        orientation="vertical",
        axis=-2.0,
        start=-1.5,
        end=1.5,
    )

    kind = parsers._classify_fragment_kind(edge, (-2.0, 2.0, -2.0, 2.0), 0.05)

    assert kind == WallKind.OUTER


def test_scene_builder_facade_keeps_cutaway_helper() -> None:
    walls = [
        WallSegment(
            wall_id="wall_1",
            start=Point2D(x=-2.0, z=-2.0),
            end=Point2D(x=2.0, z=-2.0),
            kind=WallKind.OUTER,
        ),
        WallSegment(
            wall_id="wall_2",
            start=Point2D(x=2.0, z=-2.0),
            end=Point2D(x=2.0, z=2.0),
            kind=WallKind.OUTER,
        ),
        WallSegment(
            wall_id="wall_3",
            start=Point2D(x=2.0, z=2.0),
            end=Point2D(x=-2.0, z=2.0),
            kind=WallKind.OUTER,
        ),
        WallSegment(
            wall_id="wall_4",
            start=Point2D(x=-2.0, z=2.0),
            end=Point2D(x=-2.0, z=-2.0),
            kind=WallKind.OUTER,
        ),
    ]
    camera = CameraSpec(
        position=Point3D(x=4.0, y=6.0, z=5.0),
        target=Point3D(x=0.0, y=0.8, z=0.0),
        fov=36.0,
    )

    cutaway = scene_builder._collect_cutaway_wall_ids(walls, camera)

    assert isinstance(cutaway, set)
    assert cutaway


def test_scene_builder_facade_accepts_legacy_single_argument_signature() -> None:
    walls = [
        WallSegment(
            wall_id="wall_1",
            start=Point2D(x=-2.0, z=-2.0),
            end=Point2D(x=2.0, z=-2.0),
            kind=WallKind.OUTER,
        ),
        WallSegment(
            wall_id="wall_2",
            start=Point2D(x=2.0, z=-2.0),
            end=Point2D(x=2.0, z=2.0),
            kind=WallKind.OUTER,
        ),
        WallSegment(
            wall_id="wall_3",
            start=Point2D(x=2.0, z=2.0),
            end=Point2D(x=-2.0, z=2.0),
            kind=WallKind.OUTER,
        ),
        WallSegment(
            wall_id="wall_4",
            start=Point2D(x=-2.0, z=2.0),
            end=Point2D(x=-2.0, z=-2.0),
            kind=WallKind.OUTER,
        ),
    ]

    cutaway = scene_builder._collect_cutaway_wall_ids(walls)

    assert isinstance(cutaway, set)
    assert cutaway
