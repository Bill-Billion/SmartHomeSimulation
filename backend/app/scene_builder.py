from __future__ import annotations

from .scene_layout import _build_camera, _collect_cutaway_wall_ids as _collect_cutaway_wall_ids_impl, build_scene_spec
from .scene_mesh import export_scene_glb


def _collect_cutaway_wall_ids(walls_or_scene, camera=None):
    if camera is None:
        if hasattr(walls_or_scene, "walls") and hasattr(walls_or_scene, "camera"):
            return _collect_cutaway_wall_ids_impl(walls_or_scene.walls, walls_or_scene.camera)

        walls = list(walls_or_scene)
        if not walls:
            return set()
        xs = [point.x for wall in walls for point in (wall.start, wall.end)]
        zs = [point.z for wall in walls for point in (wall.start, wall.end)]
        fallback_camera = _build_camera(
            max(max(xs) - min(xs), 0.1),
            max(max(zs) - min(zs), 0.1),
            2.8,
        )
        return _collect_cutaway_wall_ids_impl(walls, fallback_camera)

    return _collect_cutaway_wall_ids_impl(walls_or_scene, camera)


__all__ = [
    "build_scene_spec",
    "export_scene_glb",
    "_collect_cutaway_wall_ids",
]
