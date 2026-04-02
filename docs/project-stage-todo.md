# 项目阶段 Todo 清单

作者：Bill Billion  
版本：v1.23  
日期：2026-04-02  
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

## 阶段 2：运行时主线与深度解释层并行推进（进行中）

运行时主线：

- [x] 从 `SceneSpec` 派生 `WorldState`
- [x] 自动创建房间和基础设备位点
- [x] `SessionManager` 最小版
- [x] `ActionExecutor` 最小版
- [x] 单域 `LightingAgent` 最小闭环
- [x] 跑通“打开某个房间灯光”的端到端链路

可解释展示主线（并行，不阻塞运行时主线）：

- [x] 新增 `GET /api/jobs/{job_id}/diagnostics` 只读诊断接口
- [x] 新增 `GET /api/jobs/{job_id}/source-preview.png` 源图预览接口（`DXF` 明确自然降级）
- [x] 默认隐藏的分析面板
- [x] 2D 原图与结果叠加、房间高亮与证据联动
- [x] 3D 房间级聚焦与高亮联动（不改几何主链）

阶段 2 前端最小联动（PR-C）：

- [x] 新增仿真控制面板（创建会话、命令执行、状态刷新、事件流展示）
- [x] 2D 户型图按房间灯光状态做可视化反馈
- [x] 3D 视图按房间灯光状态高亮/弱化并支持房间聚焦

阶段 2 质量治理（PR-B）：

- [x] 引入后端 `ruff` lint 规则与统一配置
- [x] 增加统一质量命令入口（`lint:backend`、`test:backend`）
- [x] 新增最小 CI 流水线：后端 lint + tests、前端 build
- [x] 运行时回归补齐异常路径（会话创建失败、空命令、关闭会话、事件分页）
- [x] 事件流分页优化为增量读取，避免全量 O(n) 扫描阻塞
- [x] 统一 lint 目标入口，消除 npm 与 CI 双白名单漂移

## 阶段切换门槛（阶段 1 → 阶段 2）

历史记录（已达成并完成切换）：

- [x] PR-2（无文本真实样本语义一致性补强）已合并
- [x] 后端语义与全量测试持续通过
- [x] 前端构建通过
- [x] 四个公开主接口保持冻结且兼容
- [x] 连续 72 小时无 P1 级回归

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

当前已进入阶段 2。运行时最小闭环（`SceneSpec -> WorldState -> SessionManager -> ActionExecutor -> LightingAgent`）可用，四个仿真接口已联通，且“创建会话 -> 打开卧室灯 -> 状态更新 -> 事件可追踪”可重复执行。PR-C 最小前端联动已落地：同页可创建会话、发送灯光命令、查看设备状态和事件流，并在 2D/3D 中看到房间级灯光反馈。

PR-A（运行时收口）已完成关键修复：`test_orchestrator_parses_open_light_command` 修复为先持久化 `scene` 再创建会话；补齐会话关闭拒绝执行、事件分页和 API 异常路径回归；同时修复运行时重启后事件 `sequence` 连号问题。

PR-B（质量治理）已完成首轮落地：新增 `ruff` 配置、统一质量命令入口与最小 CI 流水线。后端回归当前为 `78 passed, 2 skipped`。由于当前执行环境缺少 `node/npm`，本地未执行前端构建，前端构建由 CI 负责兜底验证。

本轮代码优化专家复核结论：初审发现 3 个 P1（会话并发写入丢更新、`session.json` 非原子写、事件序号并发冲突）和 2 个 P2（事件分页线性读、本地 Python 版本漂移风险）。当前已完成 P1 修复：新增 per-session 执行锁，`session.json` 改为临时文件 + replace 原子写，事件序号分配下沉到存储锁内并补并发回归。二次复核结果为“无 P0/P1”，剩余 P2 为“事件分页仍是 O(n) 全量读取”和“lint 文件白名单在 npm/CI 双处维护，存在漏检漂移风险”，已登记为下一轮质量治理待办。

前端仿真联动实现后再次做代码优化专家复核：最初发现 2 个 P1（请求串线、父子状态不同步）已全部修复。最终复核结果为“无 P0/P1/P2”。

质量收口追加复核：事件游标语义已补上非法 `cursor` 的 400 降级与回归测试，最终复核结果为“无 P0/P1/P2”。

“深度解释层”继续作为阶段 2 并行泳道，不阻塞运行时主线发布。当前 PR-D 已落地：新增 diagnostics 与 source-preview 两个只读接口，前端新增默认折叠分析面板，支持 2D 源图叠加与房间证据联动，并复用 3D 房间聚焦能力。当前后端回归更新为 `83 passed, 2 skipped`。

本轮代码优化专家复核结论更新：PR-D 首轮发现的 3 个 P1 和 1 个 P2 已完成收口。修复项包括：`diagnostics` 预生成与懒重建一致性、坏 PDF 预览的降级处理、diagnostics 拉取失败不再反向污染主流程状态、分析面板与仿真控制面板房间选择单源同步、以及 diagnostics 几何 helper 对公共几何层复用。最终复核结果为“无 P0/P1/P2”。

复核验证补充：`backend/tests/test_diagnostics_api.py` 已扩到 5 条并通过，后端全量回归当前为 `83 passed, 2 skipped`。

最新一轮代码优化专家快速复核结论：发现 1 个 P1 和 2 个 P2。P1 是运行时主链在事件日志或会话持久化失败时缺少事务化收口与降级，`create_session` 会先落 `session.json` 再写 `session.created` 事件，`execute_command` 也会先写多条事件再持久化会话，已经通过定向复现确认会出现“接口报错但会话已创建”以及“事件显示动作已执行但持久化状态仍未更新”的半提交状态。P2 一是 `simulation_agents.py` 重复维护 OpenAI 兼容 endpoint 归一化和响应解析 helper，与 `llm_enhancer.py` 已出现语义漂移；二是前端当前没有自动化测试，`App.vue` 与 `SimulationControlPanel.vue` 同时承担接口编排、请求取消、状态同步和视图逻辑，后续回归更容易漏检。

本轮已完成上述 P1 收口：事件日志写入统一改为 best-effort 降级，事件写入失败只追加自然语言 warning，不再反向打断会话主流程；`action.executed` 事件改为会话状态落盘成功后再写，避免“事件先成功、状态未持久化”的半提交。并新增两条失败注入回归，分别覆盖“create_session 事件写失败降级”和“会话保存失败时不写 action.executed 事件”。

当前质量状态更新：`.venv/bin/python -m ruff check backend` 通过；`.venv/bin/python -m pytest backend/tests -q` 为 `85 passed, 2 skipped`；`npm run build` 通过（仍保留 TresJS `Timer` 与 chunk size warning）。剩余待办风险维持 2 个 P2：LLM adapter helper 复用收敛、前端最小自动化测试补齐。

针对本轮运行时收口补做代码优化专家二次复核：`simulation_runtime.py` 与新增失败注入测试结论为“无 P0/P1/P2”，确认事件日志降级策略与 `action.executed` 写入顺序修复有效。

运行时修复追加复核结论：本轮针对 `backend/app/simulation_runtime.py` 的 best-effort 收口与失败注入测试再次复核，结果为“无 P0/P1/P2”。已确认 `create_session` 的事件日志失败会降级为 warning，不再打断主流程；`action.executed` 已调整为会话状态持久化成功后再写入，避免半提交；新增两条失败注入测试有效覆盖“创建会话时事件日志失败降级”和“会话保存失败时不写 action.executed 事件”。补充人工验证也已确认：若 `action.executed` 自身写失败，命令仍成功，状态已持久化，并会写回 warning。

本轮继续执行阶段 2 质量收敛：已新增 `backend/app/llm_api_utils.py`，把 OpenAI 兼容 endpoint 归一化、响应文本提取、JSON 对象提取统一收敛，并替换 `llm_enhancer.py` 与 `simulation_agents.py` 内部重复 helper，消除两处实现漂移。新增 `backend/tests/test_llm_api_utils.py` 参数化回归锁定 URL 归一化与响应解析行为。当前回归更新为：`.venv/bin/python -m ruff check backend` 通过，`.venv/bin/python -m pytest backend/tests -q` 为 `101 passed`，`npm run build` 通过。本轮尝试调用代码优化专家子代理失败（当前会话无可用子代理），已先执行本地静态复核与全量回归，结论为无阻塞问题；下一轮补做专家复核。剩余待办风险收敛为 1 个 P2：前端最小自动化测试补齐。
