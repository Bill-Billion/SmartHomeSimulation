from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any, Type, TypeVar
from uuid import uuid4

from pydantic import BaseModel

from .config import DATA_ROOT
from .models import (
    DiagnosticsRecord,
    FloorplanSpec,
    JobRecord,
    SceneSpec,
    SimulationEvent,
    SimulationSession,
)

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
        self.simulations_root = self.data_root / "simulations"
        self._lock = threading.Lock()
        self._simulation_lock_guard = threading.Lock()
        self._simulation_locks: dict[str, threading.Lock] = {}
        self._event_sequence_cache: dict[str, int] = {}
        self.ensure_dirs()

    def ensure_dirs(self) -> None:
        self.jobs_root.mkdir(parents=True, exist_ok=True)
        self.scenes_root.mkdir(parents=True, exist_ok=True)
        self.simulations_root.mkdir(parents=True, exist_ok=True)

    def job_dir(self, job_id: str) -> Path:
        return self.jobs_root / job_id

    def scene_dir(self, scene_id: str) -> Path:
        return self.scenes_root / scene_id

    def simulation_dir(self, session_id: str) -> Path:
        return self.simulations_root / session_id

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

    def save_diagnostics(self, job_id: str, record: DiagnosticsRecord) -> Path:
        path = self.job_dir(job_id) / "diagnostics.json"
        self._write_json(path, record)
        return path

    def load_diagnostics(self, job_id: str) -> DiagnosticsRecord:
        return self._read_json(self.job_dir(job_id) / "diagnostics.json", DiagnosticsRecord)

    def source_path(self, job_id: str) -> Path:
        candidates = sorted(self.job_dir(job_id).glob("source.*"))
        if not candidates:
            raise FileNotFoundError("未找到源文件。")
        return candidates[0]

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

    def save_simulation_session(self, session: SimulationSession) -> Path:
        with self._simulation_lock(session.session_id):
            session_dir = self.simulation_dir(session.session_id)
            session_dir.mkdir(parents=True, exist_ok=True)
            path = session_dir / "session.json"
            self._write_json(path, session)
            return path

    def load_simulation_session(self, session_id: str) -> SimulationSession:
        with self._simulation_lock(session_id):
            return self._read_json(self.simulation_dir(session_id) / "session.json", SimulationSession)

    def append_simulation_event(self, event: SimulationEvent) -> SimulationEvent:
        with self._simulation_lock(event.session_id):
            session_dir = self.simulation_dir(event.session_id)
            session_dir.mkdir(parents=True, exist_ok=True)
            path = session_dir / "events.jsonl"
            sequence = self._event_sequence_cache.get(event.session_id)
            if sequence is None:
                sequence = self._count_non_empty_lines(path)
            if hasattr(event, "model_copy"):
                stored_event = event.model_copy(update={"sequence": sequence})
            else:
                stored_event = event.copy(update={"sequence": sequence})  # pragma: no cover
            with path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(_model_to_json_dict(stored_event), ensure_ascii=False))
                handle.write("\n")
            self._event_sequence_cache[event.session_id] = sequence + 1
            return stored_event

    def load_simulation_events(
        self,
        session_id: str,
        *,
        cursor: int = 0,
        limit: int = 50,
    ) -> tuple[list[SimulationEvent], int, bool]:
        with self._simulation_lock(session_id):
            path = self.simulation_dir(session_id) / "events.jsonl"
            if not path.exists():
                return [], max(cursor, 0), False

            file_size = path.stat().st_size
            safe_cursor = max(cursor, 0)
            if safe_cursor >= file_size:
                return [], file_size, False
            if not self._is_line_boundary(path, safe_cursor):
                raise ValueError("事件游标无效，请使用上一页返回的 next_cursor。")

            read_limit = max(limit, 1)
            events: list[SimulationEvent] = []
            next_cursor = safe_cursor
            with path.open("r", encoding="utf-8") as handle:
                handle.seek(safe_cursor)
                while len(events) < read_limit:
                    raw_line = handle.readline()
                    if not raw_line:
                        break
                    next_cursor = handle.tell()
                    line = raw_line.strip()
                    if not line:
                        continue
                    payload = json.loads(line)
                    if hasattr(SimulationEvent, "model_validate"):
                        events.append(SimulationEvent.model_validate(payload))
                    else:
                        events.append(SimulationEvent.parse_obj(payload))

                has_more = False
                while True:
                    raw_line = handle.readline()
                    if not raw_line:
                        break
                    if raw_line.strip():
                        has_more = True
                        break

            return events, next_cursor, has_more

    def count_simulation_events(self, session_id: str) -> int:
        with self._simulation_lock(session_id):
            path = self.simulation_dir(session_id) / "events.jsonl"
            return self._count_non_empty_lines(path)

    def _write_json(self, path: Path, model: BaseModel) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        serialized = json.dumps(_model_to_json_dict(model), ensure_ascii=False, indent=2)
        temp_path = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
        temp_path.write_text(serialized, encoding="utf-8")
        temp_path.replace(path)

    def _read_json(self, path: Path, model_cls: Type[ModelType]) -> ModelType:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if hasattr(model_cls, "model_validate"):
            return model_cls.model_validate(payload)
        return model_cls.parse_obj(payload)

    def _count_non_empty_lines(self, path: Path) -> int:
        if not path.exists():
            return 0
        with path.open("r", encoding="utf-8") as handle:
            return sum(1 for line in handle if line.strip())

    def _is_line_boundary(self, path: Path, cursor: int) -> bool:
        if cursor == 0:
            return True
        with path.open("rb") as handle:
            handle.seek(cursor - 1)
            return handle.read(1) == b"\n"

    def _simulation_lock(self, session_id: str) -> threading.Lock:
        with self._simulation_lock_guard:
            lock = self._simulation_locks.get(session_id)
            if lock is None:
                lock = threading.Lock()
                self._simulation_locks[session_id] = lock
            return lock
