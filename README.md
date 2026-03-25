# smart_home

作者：Bill Billion  
版本：v0.1.1

## 介绍

这是一个面向智能家居场景的 3D 户型生成原型。当前阶段的核心能力是：用户上传 `JPG`、`PNG`、`PDF` 或 `DXF` 文件后，后端通过规则管线生成结构化场景和 `GLB` 模型，前端先展示可对照的户型图，再切到带剖切的 3D 场景预览。

## 当前能力

- 支持上传平面图或 DXF
- 支持任务轮询和错误提示
- 支持生成房间、墙体、门窗开口、基础家具
- 支持默认展示 2D 户型结构，并可切换到 3D 剖切预览

## 项目结构

- `src/`：前端上传和 3D 预览
- `backend/`：FastAPI 服务、解析器、建模器、测试
- `docs/floorplan-to-3d-minimum-implementation.md`：当前唯一主设计稿

## 本地运行

### 启动后端

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -r backend/requirements.txt
python3 -m uvicorn backend.app.main:app --reload --app-dir .
```

### 启动前端

需要本地已安装 Node.js 和 npm。

```bash
npm install
npm run dev
```

前端开发环境默认通过 Vite 代理访问 `http://127.0.0.1:8000`。

## 接口概览

- `POST /api/floorplans:generate`
- `GET /api/jobs/{job_id}`
- `GET /api/scenes/{scene_id}`
- `GET /api/scenes/{scene_id}/model.glb`

## 说明

当前阶段不支持原生 `DWG` 解析。上传 `DWG` 时，系统会提示先转换为 `DXF` 或 `PDF`。
