# smart_home

Author: Bill Billion  
Version: v0.1.1

## Description

This prototype lets users upload a floor plan image or DXF file, then generates a browsable scene, shows a structural 2D floorplan first, and provides a cutaway 3D preview in the current Vue + TresJS frontend.

## Current Scope

- Upload `JPG`, `PNG`, `PDF`, and `DXF`
- Poll job progress and show clear errors
- Generate walls, room partitions, openings, and simple built-in furniture
- Preview the generated layout in both 2D plan mode and cutaway 3D mode

## Structure

- `src/`: upload UI and 3D preview
- `backend/`: FastAPI service, parsers, scene builder, tests
- `docs/floorplan-to-3d-minimum-implementation.md`: current source of truth

## Run Locally

### Backend

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -r backend/requirements.txt
python3 -m uvicorn backend.app.main:app --reload --app-dir .
```

### Frontend

Node.js and npm are required locally.

```bash
npm install
npm run dev
```

## API

- `POST /api/floorplans:generate`
- `GET /api/jobs/{job_id}`
- `GET /api/scenes/{scene_id}`
- `GET /api/scenes/{scene_id}/model.glb`

## Notes

Native `DWG` parsing is intentionally out of scope in phase one. The backend returns a clear conversion hint instead of a parser exception.
