# 项目阶段 Todo 清单

作者：Bill Billion  
版本：v1.12  
日期：2026-03-27  
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

## 阶段 2：把生成场景接入智能家居最小闭环

- [ ] 从 `SceneSpec` 派生 `WorldState`
- [ ] 自动创建房间和基础设备位点
- [ ] `SessionManager` 最小版
- [ ] `ActionExecutor` 最小版
- [ ] 单域 `LightingAgent` 最小闭环
- [ ] 跑通“打开某个房间灯光”的端到端链路

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

当前项目仍处于阶段 1 后半段，但主链已经从“只会处理线稿图”推进到“三条解析路径并存”的状态。`parsers.py` 继续只保留 facade，实际实现拆到 `parser_raster.py`、`parser_raster_decorated.py`、`parser_dxf.py`、`floorplan_builder.py`、`semantic_rules.py`；`scene_builder.py` 也保持 facade 形态，布局与 mesh 导出拆到 `scene_layout.py` 和 `scene_mesh.py`。前端的 `App.vue` 只保留上传编排、轮询和视图切换，上传表单、任务状态、场景摘要已经独立成组件。

当前自动化回归已经扩到 63 条。这一轮除了保留原有的 `DXF` 专项回归，还新增了彩色装修图真实样本回归和 AI 诊断回归。`houseplan1` 与 `homeplanq` 这类装修图现在会先做样式识别，再走主平面 ROI 裁剪和多策略房间恢复；两张样本都已经进入测试夹具，当前指标分别稳定在 `9` 房间 / `16` 条内墙和 `6` 房间 / `4` 条内墙，不再退化成单房间壳体。`.venv/bin/python -m pytest backend/tests -q` 全绿，前端 `npm run build` 继续通过。现有已知非阻塞 warning 仍然只有 TresJS 的 `Timer` 导出警告和打包体积提示。

这轮代码优化专家先后做了结构复核。第一轮指出 `parsers.py` 仍然把 OCR warning、几何 warning 和重复 tuple 几何 helper 混在一起，导致可选 OCR 依赖会错误拉低总置信度；当前实现已经把 OCR warning 和几何置信度拆开，并把 parser 逻辑按职责模块化。第二轮复核指出 facade 兼容和前端连续上传存在 correctness 风险：`parsers.py` 和 `scene_builder.py` 现在已经补齐旧签名 wrapper，`App.vue` 也补上了旧轮询与旧场景请求的取消，避免连续上传时状态和模型串线。

这轮把优先级临时切到了“彩色装修平面图鲁棒性修复与 AI 失败可诊断化”，现在这两个缺口都已经补上。`parser_raster.py` 会先做样式路由，命中装修图时再转给 `parser_raster_decorated.py`；后者会先裁掉边缘标注区域，再并行评估原始 ROI 和纹理压平 ROI 的结构墙候选，并用新的房间质量评分抑制“大矩形塌缩”和“单房间占比过高”的退化结果。`llm_enhancer.py` 也不再把所有失败吞成“AI 辅助未生效”，而是会区分配置不完整、连接失败、超时、鉴权失败、接口路径不兼容、图片输入不支持、返回内容非 JSON 和 schema 校验失败，并把自然语言原因写回 `warnings`。

当前阶段剩余风险主要有四类：复杂深色或强透视装修图仍可能依赖保守回退；斜墙、弧墙和非正交构型仍不支持；无文本真实样本的全局语义一致性仍可能不够自然；AI 实验增强当前只覆盖 OpenAI 兼容接口，且默认只做语义和家具建议，不参与几何主链。

当前下一优先项重新回到“无文本真实样本的全局语义一致性补强”。原因也更直接了：彩色装修图鲁棒性和 AI 失败诊断已经补上，接下来最影响用户感知的问题又回到“没有 OCR / DXF 文本时，规则链能不能更自然地给出厨房、卫生间和走廊语义”。斜墙、弧墙和更完整的 CAD 图元支持继续后置，不在下一轮主线上抢优先级。
