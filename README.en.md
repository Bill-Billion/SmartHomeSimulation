# smart_home

Author: Bill Billion  
Version: v0.1.6

## Description

This prototype turns uploaded floor plan images or DXF files into a browsable 2D and 3D scene. The current rule-based pipeline parses geometry into `FloorplanSpec`, builds `SceneSpec`, exports a cutaway `GLB`, and lets the Vue + TresJS frontend switch between the plan view and the generated 3D preview.

This release improves the two weakest spots in the current user flow. The raster parser now routes colorful decorated plans into `parser_raster_decorated.py`, crops the main plan ROI, evaluates both raw and texture-flattened variants, and scores the recovered room sets so furniture, floor textures, and dimension lines are less likely to collapse the result into one giant shell. The optional AI enhancement path also returns diagnosable warnings instead of a single generic fallback message, so timeouts, auth failures, incompatible endpoints, image-input rejection, invalid JSON, and schema violations are easier to understand.

## Current Scope

- Upload `JPG`, `PNG`, `PDF`, and `DXF`
- Poll job progress and show clear warnings or errors
- Generate walls, room partitions, openings, floors, ceilings, and built-in furniture
- Preserve orthogonal L-shaped and multi-corner room outlines, with a conservative fallback path for dense or dark floorplan images
- Recover multi-room structure from colorful decorated plans with plan-ROI routing and multi-strategy room selection
- Recover room polygons from line-based DXF, open polylines, and closed polylines, with natural-language warnings when the parser has to fall back
- Improve kitchen, bathroom, and corridor labels with OCR and DXF text hints
- Preview the generated layout in both 2D plan mode and cutaway 3D mode
- Optionally use an OpenAI-compatible model for semantic and furniture-placement refinement without changing wall geometry
- Surface more specific AI fallback warnings when the remote service times out, rejects auth, rejects image input, or returns invalid structured output

## Structure

- `src/`: page orchestration, upload UI, job status, scene summary, 2D preview, and 3D viewer
- `backend/`: FastAPI service, parser facade, raster/dxf submodules, semantic rules, scene layout, mesh export, AI enhancement adapter, and tests
- `docs/floorplan-to-3d-minimum-implementation.md`: current source of truth
- `docs/project-stage-todo.md`: stage plan and progress tracker

## Run Locally

### Backend

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -r backend/requirements.txt
python3 -m uvicorn backend.app.main:app --reload --app-dir .
```

The backend still expects a local `tesseract` binary with `eng` and `chi_sim` language packs. If they are missing, semantic OCR falls back to geometry-only classification.

### Frontend

Node.js and npm are required locally.

```bash
npm install
npm run dev
```

## API

- `POST /api/floorplans:generate`
  - required multipart field: `file`
  - optional multipart fields: `llm_enabled`, `llm_base_url`, `llm_model`, `llm_api_key`
- `GET /api/jobs/{job_id}`
- `GET /api/scenes/{scene_id}`
- `GET /api/scenes/{scene_id}/model.glb`

## Notes

Native `DWG` parsing is still out of scope in phase one. The backend returns a clear conversion hint instead of a parser exception.

The optional AI enhancement currently targets OpenAI-compatible chat-completions APIs only. API keys are used in-memory for the current request and are not persisted into `job.json`, `floorplan.json`, or `scene.json`. When the remote model fails, the backend now returns a more specific natural-language warning instead of a single generic fallback message.
