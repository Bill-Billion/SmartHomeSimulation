from __future__ import annotations

from pathlib import Path

from fastapi import BackgroundTasks, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from .config import SUPPORTED_UPLOAD_EXTENSIONS
from .llm_enhancer import LlmRequestConfig, apply_scene_llm_enhancements
from .models import JobRecord, JobStatus, SourceType, utc_now_iso
from .parsers import UnsupportedFormatError, parse_floorplan
from .scene_builder import build_scene_spec, export_scene_glb
from .storage import FileStorage


def create_app(data_root: Path | None = None) -> FastAPI:
    storage = FileStorage(data_root)
    app = FastAPI(
        title="Smart Home Floorplan Generator",
        description="上传 CAD 或平面图并生成可预览 3D 场景。",
        version="0.1.6",
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.post("/api/floorplans:generate")
    async def generate_floorplan(
        background_tasks: BackgroundTasks,
        file: UploadFile = File(...),
        llm_enabled: bool = Form(False),
        llm_base_url: str | None = Form(None),
        llm_model: str | None = Form(None),
        llm_api_key: str | None = Form(None),
    ) -> dict[str, str]:
        suffix = Path(file.filename or "").suffix.lower()
        if suffix not in SUPPORTED_UPLOAD_EXTENSIONS:
            raise HTTPException(status_code=400, detail="仅支持 JPG、PNG、PDF、DXF、DWG 文件。")

        source_type = _safe_source_type(suffix)
        job = JobRecord.new(source_filename=file.filename or "upload", source_type=source_type)
        storage.create_job(job)
        content = await file.read()
        source_path = storage.save_upload(job.job_id, file.filename or "upload", content)
        background_tasks.add_task(
            _process_job,
            storage,
            job.job_id,
            source_path,
            LlmRequestConfig(
                enabled=llm_enabled,
                base_url=llm_base_url,
                model=llm_model,
                api_key=llm_api_key,
            ),
        )
        return {"job_id": job.job_id, "status": job.status.value}

    @app.get("/api/jobs/{job_id}")
    def get_job(job_id: str) -> JobRecord:
        try:
            return storage.load_job(job_id)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail="未找到对应任务。") from exc

    @app.get("/api/scenes/{scene_id}")
    def get_scene(scene_id: str):
        try:
            return storage.load_scene(scene_id)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail="未找到对应场景。") from exc

    @app.get("/api/scenes/{scene_id}/model.glb")
    def get_scene_model(scene_id: str):
        model_path = storage.model_path(scene_id)
        if not model_path.exists():
            raise HTTPException(status_code=404, detail="场景模型尚未生成。")
        return FileResponse(model_path, media_type="model/gltf-binary", filename=f"{scene_id}.glb")

    @app.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


def _safe_source_type(suffix: str) -> SourceType | None:
    try:
        return SourceType(suffix[1:])
    except ValueError:
        return None


def _process_job(
    storage: FileStorage,
    job_id: str,
    source_path: Path,
    llm_config: LlmRequestConfig | None = None,
) -> None:
    job = storage.load_job(job_id)
    job.status = JobStatus.PROCESSING
    job.message = "正在解析户型并生成 3D 场景。"
    job.updated_at = utc_now_iso()
    storage.save_job(job)

    try:
        floorplan = parse_floorplan(source_path)
        scene_id = f"scene_{job_id}"
        furniture_hints: dict[str, str] = {}
        if llm_config and llm_config.enabled:
            draft_scene = build_scene_spec(scene_id, floorplan)
            floorplan, furniture_hints = apply_scene_llm_enhancements(
                source_path,
                floorplan,
                draft_scene,
                llm_config,
            )

        storage.save_floorplan(job_id, floorplan)
        scene = build_scene_spec(scene_id, floorplan, furniture_hints=furniture_hints)
        glb_bytes = export_scene_glb(scene)
        storage.save_scene(scene)
        storage.save_model(scene_id, glb_bytes)

        job.status = JobStatus.COMPLETED
        job.message = "场景生成完成，可以开始预览。"
        job.updated_at = utc_now_iso()
        job.scene_id = scene_id
        job.confidence = floorplan.confidence
        job.warnings = scene.warnings
        job.scene_url = f"/api/scenes/{scene_id}"
        job.model_url = f"/api/scenes/{scene_id}/model.glb"
        storage.save_job(job)
    except UnsupportedFormatError as exc:
        job.status = JobStatus.FAILED
        job.message = "文件格式暂不支持。"
        job.error = str(exc)
        job.updated_at = utc_now_iso()
        storage.save_job(job)
    except Exception as exc:  # noqa: BLE001
        job.status = JobStatus.FAILED
        job.message = "生成失败，请换一张更清晰的平面图后重试。"
        job.error = str(exc)
        job.updated_at = utc_now_iso()
        storage.save_job(job)


app = create_app()
