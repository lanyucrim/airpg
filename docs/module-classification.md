# 模块归类与依赖边界

状态：稳定架构参考  
更新日期：2026-08-24  
当前开发焦点、模块健康度与待办只见 [开发控制台](development.md)。

本文件只回答“代码属于哪里、可以依赖什么”。它不记录本轮焦点、阶段进度或测试数字；这些变化频繁的信息只维护在 `development.md`。

## 领域归属

| 领域 | 负责内容 | 当前正式实现入口 |
| --- | --- | --- |
| `core` | 事件、命令、投影聚合、回放、幂等、事务、存储、API 与应用启动 | `core/events.py`、`commands.py`、`state.py`、`projection.py`、`turn_pipeline.py`、`service.py`、存储与 `main.py` |
| `characters` | 人物身份、身体、装备绑定、档案、关系、认知、背包归属与 NPC 决策规则 | `characters/events.py`、`body.py`、`equipment.py`、`inventory.py`、`traits.py`、`decision.py` |
| `items` | 定义/实例、容器、数量、容量、所有权、生命周期、装备规格、交易、AI-物品资料工具 | `items/models.py`、`catalog.py`、`inventory.py`、`commerce.py`、`functions.py`、`ai_items/` |
| `locations` | 运行时地点移动、出口许可、环境机会和天气移动修正 | `locations/movement.py`、`environment.py`、`weather_travel.py` |
| `map` | 灰港图册、街道拓扑、路径、占用派生和公开地图读模型 | `map/atlas.py`、`traversal.py`、`routing.py`、`occupancy.py`、`public.py` |
| `behavior` | 玩家文本本地解析、行动路由、复合行动协调、日常规则与按领域分组命令 | `behavior/intent_router.py`、`router.py`、`routine_rules.py`、`commands/` |
| `story` | 剧本包、V4.2 编译、开局、场景、调查、线索和剧情事件 | `story/scenario.py`、`v4_compiler.py`、`bootstrap.py`、`investigation.py`、事件处理器 |
| `world` | 时间、天气、法律、经济、组织计划、世界结算和自治候选 | `world/weather.py`、`events.py`、`simulation.py`、`consequences.py`、`director.py` |
| `ai/platform` | 模型配置、供应商 HTTP、超时/重试、调用指标与底层适配器 | `ai/platform/deepseek.py`、`weather_adapter.py`、`contracts.py` |
| `ai/player` | 玩家意图候选、安全叙述、可见上下文与玩家可见降级 | `ai/player/intent.py`、`narration.py` |
| `memory` | 确认事件派生的长期记忆、检索、摘要与记忆评估 | `memory/memory.py`、`memory/evaluation.py` |
| `evaluation` | 意图、NPC 等跨领域评估工具 | `evaluation/intent.py`、`evaluation/npc_decision.py` |
| 网页 | 玩家展示、输入和开发调试；不复制权威规则 | `apps/web/` |

## 允许的协作方式

- 所有领域都可依赖 `core` 的协议、确认事件和只读投影接口。
- `behavior` 可以协调人物、物品、地点、剧情、AI 和记忆，但只能提交经过领域校验的命令/候选事件，不能直接改投影。
- `characters` 对物品只读取必要摘要或使用物品命令；不直接修改物品实例。
- `locations` 只读取剧情提供的地点机会来源；不创建交易或物品。
- `items` 可读取剧情提供的故事绑定策略；不读取 NPC 私有认知来决定社交结果。
- `map` 提供静态图册和派生读模型；运行时位置、可达性与物品位置仍以事件投影和地点规则为准。
- `world` 可以提出世界变化候选，但状态变化必须经核心确认事件提交。
- `ai/platform` 只能返回结构化候选或失败；不能访问存储写入、投影写入或事件提交。AI-物品适配器留在 `items/ai_items/`，因为其产物是物品资料工具而非通用回合 AI。
- `memory` 只从确认事件派生，不作为当前权威事实来源。

尤其禁止：

- 人物领域直接改物品状态；
- 地点领域直接创建物品、货币或交易；
- AI 直接写数据库、投影或确认事件；
- 剧情领域直接替 NPC 作社会决策；
- 前端复制最终移动、物品归属、关系、通缉、骰子或天气计算规则。

## 已接受的过渡边界

这些不是“已完成迁移”的声明，而是当前明确存在、需要在独立重构批次处理的边界：

1. `Projection` 继续放在 `core/state.py`；若干人物和地点状态数据类也仍集中在那里。新领域规则不得继续添加到核心，状态类型迁出需单独验证回放。
2. `behavior/commands/` 已按领域分组，但部分处理仍分发到 `behavior/router.py` 的私有实现。新命令应进入对应分组，不再扩大总路由器。
3. NPC 决策、日常候选和叙述的部分 DeepSeek 传输实现仍分别位于 `characters/decision.py`、`behavior/routine_rules.py` 与 `ai/player/narration.py`。它们已经只返回候选，但物理迁入 `ai/platform` 尚未完成。
4. 历史事件回放所需的兼容逻辑属于核心投影协议；不能为此恢复没有调用方的旧业务文件或顶层兼容入口。

## 变更规则

- 新代码先确定领域归属；跨领域访问优先使用只读端口、命令或确认事件。
- 若改变事件结构、权威边界或上述依赖方向，先更新 ADR 或相关规范，并在 [开发控制台](development.md) 记录迁移影响。
- 删除无调用方、已被正式实现替代的代码；删除前检查代码、脚本和文档引用。
- 默认运行本模块冒烟测试；核心协议、存储、回放、公共 API 或用户明确要求时运行全量回归。
