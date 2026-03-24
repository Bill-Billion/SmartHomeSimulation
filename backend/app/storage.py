from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any, Type, TypeVar

from pydantic import BaseModel

from .config import DATA_ROOT
from .models import FloorplanSpec, JobRecord, SceneSpec


ModelType = TypeVar("ModelType", bound=BaseModel)


def _model_to_json_dict(model: BaseModel) -> dict[str, Any]:
    if hasattr(model, "model_dump"):
        return model.model_dump(mode="json")
    return json.loads(model.json())


class FileStorage:
    """基于文件系统的最小存储层。

    第一阶段先追求链路完整和便于排查，不引入数据库。
    """

    def __init__(self, data_root: Path | None = None):
        self.data_root = data_root or DATA_ROOT
        self.jobs_root = self.data_root / "jobs"
        self.scenes_root = self.data_root / "scenes"
        self._lock = threading.Lock()
        self.ensure_dirs()

    def ensure_dirs(self) -> None:
        self.jobs_root.mkdir(parents=True, exist_ok=True)
        self.scenes_root.mkdir(parents=True, exist_ok=True)

    def job_dir(self, job_id: str) -> Path:
        return self.jobs_root / job_id

    def scene_dir(self, scene_id: str) -> Path:
        return self.scenes_root / scene_id

    def create_job(self, job: JobRecord) -> None:
        with self._lock:
            job_dir = self.job_dir(job.job_id)
            job_dir.mkdir(parents=True, exist_ok=True)
            self._write_json(job_dir / "job.json", job)

    def save_job(self, job: JobRecord) -> None:
        with self._lock:
            self._write_json(self.job_dir(job.job_id) / "job.json", job)

    def load_job(self, job_id: str) -> JobRecord:
        return self._read_json(self.job_dir(job_id) / "job.json", JobRecord)

    def save_upload(self, job_id: str, filename: str, content: bytes) -> Path:
        job_dir = self.job_dir(job_id)
        job_dir.mkdir(parents=True, exist_ok=True)
        target = job_dir / f"source{Path(filename).suffix.lower()}"
        target.write_bytes(content)
        return target

    def save_floorplan(self, job_id: str, spec: FloorplanSpec) -> Path:
        path = self.job_dir(job_id) / "floorplan.json"
        self._write_json(path, spec)
        return path

    def load_floorplan(self, job_id: str) -> FloorplanSpec:
        return self._read_json(self.job_dir(job_id) / "floorplan.json", FloorplanSpec)

    def save_scene(self, scene: SceneSpec) -> Path:
        scene_dir = self.scene_dir(scene.scene_id)
        scene_dir.mkdir(parents=True, exist_ok=True)
        path = scene_dir / "scene.json"
        self._write_json(path, scene)
        return path

    def load_scene(self, scene_id: str) -> SceneSpec:
        return self._read_json(self.scene_dir(scene_id) / "scene.json", SceneSpec)

    def save_model(self, scene_id: str, content: bytes) -> Path:
        scene_dir = self.scene_dir(scene_id)
        scene_dir.mkdir(parents=True, exist_ok=True)
        path = scene_dir / "model.glb"
        path.write_bytes(content)
        return path

    def model_path(self, scene_id: str) -> Path:
        return self.scene_dir(scene_id) / "model.glb"

    def _write_json(self, path: Path, model: BaseModel) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(_model_to_json_dict(model), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _read_json(self, path: Path, model_cls: Type[ModelType]) -> ModelType:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if hasattr(model_cls, "model_validate"):
            return model_cls.model_validate(payload)
        return model_cls.parse_obj(payload)

