# 项目阶段 Todo 清单

作者：Bill Billion  
版本：v1.14  
日期：2026-03-31  
状态：当前阶段追踪清单

## 说明

本文档是项目阶段计划、完成度和优先级变化的专用记录。

每次出现以下情况，都必须同步更新本文档：

- 阶段目标变化
- 任务优先级变化
- 新增或删除任务
- 某项任务完成或回退
- 质量评估规则变化

只有在以下三个条件都满足时，才能把任务标记为已完成：

- 意图理解已经核对清楚，没有带着误解实现
- 代码设计合理，关键链路有明确异常处理和降级策略
- 相关测试已经通过；如果没有自动化测试，必须记录人工验证方式和原因
- 每完成一部分代码工作后，必须调用代码优化专家智能体做一轮质量评估
- 质量评估的结论必须同步写入项目记忆；如果有高优先级问题，先修正再标记完成

## 阶段 0：主线收敛与基线重建

- [x] 确定当前第一主线是“上传 CAD/平面图生成 3D 场景”
- [x] 建立当前唯一主设计稿 `docs/floorplan-to-3d-minimum-implementation.md`
- [x] 将多智能体相关文档标记为历史讨论稿，不作为当前实现依据
- [x] 统一作者为 `Bill Billion`
- [x] 当前实现版本更新到 `v0.1.6`

## 阶段 1：上传平面图生成 2D/3D 场景的最小闭环

- [x] 前端上传面板
- [x] 支持 `JPG`、`PNG`、`PDF`、`DXF`、`DWG` 文件选择
- [x] `DWG` 返回“请先转换为 DXF 或 PDF”的提示
- [x] 最小后端 API
- [x] 文件系统落盘 `job/floorplan/scene/glb`
- [x] 图片与 PDF 规则解析链
- [x] DXF 解析入口
- [x] `FloorplanSpec` 统一中间结构
- [x] `SceneSpec` 统一场景结构
- [x] 参数化建模器导出 `GLB`
- [x] 前端轮询任务状态
- [x] 任务完成后自动加载最新场景
- [x] 默认先展示 2D 户型图
- [x] 支持切换到 3D cutaway 预览
- [x] 房间、墙体、开口、基础家具的最小生成链路
- [x] 户型识别精度优化
- [x] 房间语义分类优化
- [x] 无文本真实样本语义一致性补强（两阶段语义判定）
- [x] 共享墙与墙体中心线归一
- [x] 门窗位置和尺寸精修
- [x] 3D 表达质量继续优化
- [x] 基于相机朝向的 cutaway 选择
- [x] 天花、门窗轻量构件和贴墙家具生成
- [x] 前端消费 `SceneSpec.camera` 并支持重置视角
- [x] 公共几何层与领域规则层收敛
- [x] 解析链拆成 facade + `parser_raster` / `parser_dxf` / `floorplan_builder` / `semantic_rules`
- [x] 场景链拆成 facade + `scene_layout` / `scene_mesh`
- [x] `App.vue` 拆成页面编排 + `UploadPanel` / `JobStatusCard` / `SceneSummaryCard`
- [x] facade 兼容层补齐旧签名 wrapper
- [x] 前端补齐连续上传的旧请求取消与串线防护
- [x] line-based DXF 房间恢复
- [x] 彩色装修平面图鲁棒性修复
- [x] 装修图样式路由、主平面 ROI 裁剪与多策略房间恢复
- [x] 公共几何层与 facade 兼容测试通过
- [x] 后端自动化测试通过
- [x] 前端构建通过

## 阶段 1 实验增强：可选 AI 语义与布置辅助

- [x] 上传接口支持可选 `llm_enabled`、`llm_base_url`、`llm_model`、`llm_api_key`
- [x] 前端增加默认关闭的 AI 辅助理解实验面板
- [x] 后端接入 OpenAI 兼容 chat completions adapter
- [x] 规则链先生成 `FloorplanSpec` 和草稿 `SceneSpec`，AI 只做后处理增强
- [x] AI 返回值限制为 `room_overrides`、`furniture_hints`、`extra_warnings`
- [x] 几何字段不可改写，越界字段会被 schema 护栏拒绝
- [x] AI 失败、超时、配置缺失或非法 JSON 时回退到规则结果
- [x] AI 失败原因可诊断化，warning 可区分超时、鉴权失败、接口不兼容、图片输入不支持和结构化输出错误
- [x] `llm_api_key` 不写入 `job.json`、`floorplan.json`、`scene.json`
- [x] AI 护栏与回退路径自动化测试通过

## 阶段 2：运行时主线与深度解释层并行推进

运行时主线：

- [ ] 从 `SceneSpec` 派生 `WorldState`
- [ ] 自动创建房间和基础设备位点
- [ ] `SessionManager` 最小版
- [ ] `ActionExecutor` 最小版
- [ ] 单域 `LightingAgent` 最小闭环
- [ ] 跑通“打开某个房间灯光”的端到端链路

可解释展示主线（并行，不阻塞运行时主线）：

- [ ] 新增 `GET /api/jobs/{job_id}/diagnostics` 只读诊断接口
- [ ] 新增 `GET /api/jobs/{job_id}/source-preview.png` 源图预览接口（`DXF` 明确自然降级）
- [ ] 默认隐藏的分析面板
- [ ] 2D 原图与结果叠加、房间高亮与证据联动
- [ ] 3D 房间级聚焦与高亮联动（不改几何主链）

## 阶段切换门槛（阶段 1 → 阶段 2）

- [ ] PR-2（无文本真实样本语义一致性补强）已合并
- [ ] 后端语义与全量测试持续通过
- [ ] 前端构建通过
- [ ] 四个公开主接口保持冻结且兼容
- [ ] 连续 72 小时无 P1 级回归

## 阶段 3：控制平面最小成型与跨域协同

- [ ] `Orchestrator`
- [ ] `AgentRegistry`
- [ ] `TaskBoard`
- [ ] `EventBus`
- [ ] `ProtocolManager`
- [ ] `PolicyArbiter`
- [ ] `ToolRegistry`
- [ ] `ContextManager`
- [ ] `HVAC` 接入
- [ ] Lighting + HVAC 跨域协同场景

## 阶段 4：安全、安防、能耗约束层

- [ ] `Security` Agent
- [ ] `Energy` 规则模块
- [ ] `ConstraintStore`
- [ ] `Incident` 协议
- [ ] 组合约束检查
- [ ] 安全优先级和权限仲裁
- [ ] 离家、夜间、安防联动模式

## 阶段 5：上下文智能、故障处理、回放与评测

- [ ] `UserContext` Agent
- [ ] `FaultDiagnosis` Agent
- [ ] LLM `Record/Replay Proxy`
- [ ] 确定性回放
- [ ] `MetricsCollector`
- [ ] 完整 `EventLog` 审计链
- [ ] trace 贯穿传播
- [ ] 批量评测与实验回放
- [ ] `WorldState` 写屏障和 ACL
- [ ] 组合安全分析

## 当前判断

当前项目仍处于阶段 1 收口阶段。阶段切换口径继续保持“主闭环稳定 + 无文本语义下限达标”，深度解释层保持阶段 2 并行项，不再阻塞阶段切换。

PR-2 的代码实现已经落地：无文本路径从“单房间局部决策”升级为“几何拓扑打分 + 户型级全局分配”。分配顺序固定为走廊、客厅、厨房、卫生间、卧室，`generic` 只在证据不足时保留。新增 `backend/tests/test_semantic_consistency.py`，覆盖无文本结构断言和真实装修图（禁用 OCR）回归。后端测试当前为 `66 passed, 2 skipped`，前端 `npm run build` 通过，公开接口未变更。

本轮代码优化专家复核结论：无 P0/P1/P2 高优先级问题。已确认没有新增的链路中断风险或接口兼容风险。后续建议保留三项优化：语义阈值常量进一步收敛、补内部语义决策 trace、为无文本语义回归增加漂移监控阈值。

“深度解释层”继续作为阶段 2 并行泳道，与运行时主线共享回归基线但互不阻塞发布节奏。

当前下一优先项调整为：提交并合并 PR-2，进入连续 72 小时无 P1 回归观察窗；窗口达标后立即切换阶段 2 并行开发。
