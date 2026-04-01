# smart_home

作者：Bill Billion  
版本：v0.1.6

## 介绍

这是一个面向智能家居场景的 3D 户型生成原型。当前主线是：用户上传 `JPG`、`PNG`、`PDF` 或 `DXF` 文件后，后端先走规则解析产出 `FloorplanSpec` 和 `SceneSpec`，再生成带剖切视角的 `GLB` 场景，前端先展示可对照的 2D 户型，再切到可浏览的 3D 预览。

这一版把两个最影响观感的问题一起补强了。栅格主链现在会先判断图片更像线稿还是彩色装修图；命中装修图时，会走 `parser_raster_decorated.py`，先做主平面 ROI 裁剪，再并行评估原始 ROI 和纹理压平 ROI 的房间候选，最后按质量评分选出更可信的多房间结构。AI 实验增强这边也不再把所有失败吞成一句 warning，而是能区分配置缺失、连接失败、超时、鉴权失败、接口路径不兼容、图片输入不支持、返回内容非 JSON 和 schema 不合法这些场景。

阶段 2 当前已落地最小仿真运行时。后端支持从 `SceneSpec` 创建仿真会话，按自然语言命令驱动单域照明智能体执行动作，并回写 `WorldState` 和事件流，形成可回放的最小闭环。

## 当前能力

- 支持上传平面图或 DXF
- 支持任务轮询、错误提示和自然语言 warning
- 支持生成房间、墙体、门窗开口、地面、天花和基础家具
- 支持保留 L 形和多拐点房间的正交轮廓，并在复杂深色图纸上自动回退到保守分割策略
- 支持对彩色装修平面图做样式路由、主平面裁剪和多策略房间恢复，减少家具、纹理和尺寸线把户型压成单房间壳体
- 支持用 OCR 和 DXF 文本线索增强厨房、卫生间、走廊等房间语义分类
- 支持从 line-based `DXF`、开放 polyline 和闭合多段线恢复真实房间轮廓，并在失败时返回自然语言降级 warning
- 支持按相机方向做 cutaway 预览，并优先沿可用墙面摆放家具
- 支持可选 AI 辅助理解；AI 只补语义和家具建议，不改墙体几何，也不会把密钥写入产物文件
- 支持把 AI 失败原因明确回传成自然语言 warning，便于判断是超时、鉴权、接口不兼容还是返回格式错误
- 支持最小仿真运行时：会话创建、灯光命令执行、状态快照查询和事件流分页读取
- 支持同页仿真控制面板：创建会话、下发灯光命令、查看设备状态和事件流，并同步到 2D/3D 视图反馈
- 支持默认折叠的分析面板：查看 diagnostics、源图预览、房间证据和 AI 失败原因

## 项目结构

- `src/`：前端页面编排、上传组件、任务状态卡片、2D 预览和 3D 场景浏览
- `backend/`：FastAPI 服务、parser facade、raster/dxf 子模块、语义规则层、场景布局层、mesh 导出层、AI 增强 adapter、测试
- `docs/floorplan-to-3d-minimum-implementation.md`：当前唯一主设计稿
- `docs/project-stage-todo.md`：阶段计划、完成度和后续优先级

## 本地运行

### 启动后端

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -r backend/requirements.txt
python3 -m uvicorn backend.app.main:app --reload --app-dir .
```

本机还需要安装 `tesseract`，并确保 `eng` 和 `chi_sim` 语言包可用，否则房间文字识别会自动降级回几何规则。

### 启动前端

需要本地已安装 Node.js 和 npm。

```bash
npm install
npm run dev
```

前端开发环境默认通过 Vite 代理访问 `http://127.0.0.1:8000`。

## 接口概览

主生成链：

- `POST /api/floorplans:generate`
  - multipart 必填字段：`file`
  - multipart 可选字段：`llm_enabled`、`llm_base_url`、`llm_model`、`llm_api_key`
- `GET /api/jobs/{job_id}`
- `GET /api/scenes/{scene_id}`
- `GET /api/scenes/{scene_id}/model.glb`

仿真链：

- `POST /api/simulations:sessions`
- `GET /api/simulations/sessions/{session_id}`
- `POST /api/simulations/sessions/{session_id}/commands`
- `GET /api/simulations/sessions/{session_id}/events`

解释层只读接口：

- `GET /api/jobs/{job_id}/diagnostics`
- `GET /api/jobs/{job_id}/source-preview.png`

## 质量门禁

后端质量命令：

```bash
npm run lint:backend
npm run test:backend
```

CI 已启用最小门禁：后端 `ruff + pytest`，前端 `npm run build`。

## 说明

当前阶段不支持原生 `DWG` 解析。上传 `DWG` 时，系统会提示先转换为 `DXF` 或 `PDF`。

当前 AI 实验增强只支持 OpenAI 兼容接口。不开启时，系统行为与规则版一致。开启后如果模型超时、鉴权失败、接口路径不兼容、图片输入不支持、返回非法 JSON 或证据不足，也会自动回退到规则结果，并在 `warnings` 里给出更具体的原因。
