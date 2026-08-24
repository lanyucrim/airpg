# AI-TRPG

一个由 AI 辅助驱动、但由确定性游戏内核维护世界事实的单人文字跑团项目。

## 当前开发模式

项目按同级领域模块开发，不存在固定的前后顺序。**当前焦点、已知问题、测试结果和下一项选择只看 [开发控制台](docs/development.md)。** 模块职责见[模块归类表](docs/module-classification.md)；历史路线和旧批次记录分别见[开发路线图](docs/development-roadmap.md)与[开发历史归档](docs/development-history.md)，它们不决定当前任务。

项目已有白鹭屋开局、权威移动/调查/询问、环境搜索、物品操作、赠礼/贿赂、时间推进、长期记忆、天气事实与受约束 AI 候选等基线能力。这些基线不表示对应模块已完整完成，具体边界以开发控制台为准。

阶段 4C 混合 NPC 决策与首个质量基线已完成：在结构化意图和安全多段落叙述之外，NPC 现在可以由 AI 综合人物经济处境、职业、动机、恐惧、行为原则、风险、关系和有来源的历史，选择接受、拒绝、还价、延迟或试探。Python 不用总分公式替 NPC 作决定，但仍严格校验物品归属、位置、人物底线、记忆来源和允许后果。版本化 11 项真实 DeepSeek 基线与 5 组成对比较全部通过。

当前版本包含 Python 权威内核、SQLite 事件日志、可重放状态投影、命令幂等、通用容器所有权校验、带来源的关系影响、原始消息、通用 Action Schema、事件来源链、回合调试接口、可校验且带版本指纹的剧本包，以及一个可操作的网页界面。正式灰港开局已经包含白鹭屋、核心 NPC、组织、隐藏世界事实、角色认知、七日期限、债务义务、条件化出口、可审计发现事件、可重放叙事边界和私有 NPC 决策档案。产品内容只保留《灰港：黑潮王座》；长期语义记忆与通缉/传闻等后果传播仍在后续阶段。

阶段 0 的结构检查可以在 PowerShell 中运行：

```powershell
.\scripts\validate-stage0.ps1
```

## 启动开发环境

首次安装后端：

```powershell
cd apps/server
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
```

分别打开两个终端：

```powershell
cd apps/server
.\.venv\Scripts\python.exe -m uvicorn trpg_server.core.main:app --reload --port 8000
```

```powershell
cd apps/web
pnpm install
pnpm dev
```

网页位于 `http://localhost:3000`，接口文档位于 `http://localhost:8000/docs`。

网页现在默认进入《灰港：黑潮王座》的白鹭屋开局。可以尝试：

1. `我去厨房。`
2. `我回到大厅。`
3. `我直接去地窖。`（当前没有直达出口，应被拒绝）
4. `我去王宫。`（不存在的地点不能被玩家一句话创造）
5. 从厨房进入地窖后输入 `我检查积水后的排水口。`（第五章暴雨触发前不会提前发现暗道）
6. `我查看铁钩帮最后通牒。`
7. `我询问玛莎：这笔债务到底是怎么来的？`
8. `我查看白鹭屋营业账本，核对债务数字。`
9. 点击“重置开局”，恢复到哈维送达七日通牒的时刻。

这些动作验证地点引用、直连出口、禁止瞬移、隐藏信息隔离、剧情条件和幂等事件提交。

## 核心文档

- [当前开发焦点与模块文档](docs/development.md)
- [模块归类表](docs/module-classification.md)
- [完整开发流程与路线图](docs/development-roadmap.md)
- [核心原则](docs/core-principles.md)
- [统一术语](docs/glossary.md)
- [单回合执行协议](docs/turn-protocol.md)
- [事件规范](docs/event-specification.md)
- [剧本包规范](docs/scenario-package-specification.md)
- [领域模型](docs/domain-model.md)
- [上下文与记忆规范](docs/context-memory-specification.md)
- [后果传播规范](docs/consequence-specification.md)
- [阶段 0 验收标准](docs/stage-0-acceptance.md)
- [阶段 0 验证报告](docs/stage-0-validation-report.md)
- [阶段 2 验证报告](docs/stage-2-validation-report.md)
- [阶段 3A 验证报告](docs/stage-3a-validation-report.md)
- [阶段 3B 验证报告](docs/stage-3b-validation-report.md)
- [阶段 3C 第一部分验证报告](docs/stage-3c-part1-validation-report.md)
- [阶段 3C 第二部分验证报告](docs/stage-3c-part2-validation-report.md)
- [阶段 3D 验证报告](docs/stage-3d-validation-report.md)
- [阶段 4A 第一部分验证报告](docs/stage-4a-part1-validation-report.md)
- [阶段 4A 第二部分验证报告](docs/stage-4a-part2-validation-report.md)
- [结构化意图解析规范](docs/intent-parser-specification.md)
- [DeepSeek 意图适配器说明](docs/deepseek-intent-adapter.md)
- [意图解析评估规范](docs/intent-evaluation-specification.md)
- [阶段 4A 评估基线报告](docs/stage-4a-evaluation-baseline-report.md)
- [安全 AI 叙述边界规范](docs/narration-boundary-specification.md)
- [AI 辅助 NPC 决策规范](docs/npc-decision-specification.md)
- [阶段 4B 第一部分验证报告](docs/stage-4b-part1-validation-report.md)
- [阶段 4B Narrative Plan 验证报告](docs/stage-4b-narrative-plan-validation-report.md)
- [阶段 4C 混合 NPC 决策验证报告](docs/stage-4c-hybrid-npc-decision-validation-report.md)
- [阶段 4C NPC 决策评估基线报告](docs/stage-4c-npc-decision-evaluation-baseline-report.md)
- [《灰港：黑潮王座》导入说明](docs/gray-harbor-import-notes.md)
- [架构自审与遗留问题](docs/architecture-self-review.md)
- [前端技术决策](docs/decisions/0001-frontend-stack.md)
- [后端架构决策](docs/decisions/0002-backend-architecture.md)
- [Narrative Plan 与剧本包 v5 决策](docs/decisions/0010-narrative-plan-and-scenario-v5.md)
- [有来源上下文约束下的 AI NPC 决策](docs/decisions/0011-hybrid-npc-decision.md)

## 当前架构结论

- 后端：Python，负责全部权威状态、规则、后果和 AI 调用协调。
- 前端：Next.js + TypeScript，负责玩家界面和开发者调试界面。
- 持久化：采用追加式事件日志，并维护可查询的当前状态投影。
- AI：只能解析、提议和叙述，不能绕过校验器直接修改世界。
