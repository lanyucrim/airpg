# 当前 AI 应用清单

更新日期：2026-08-26
用途：只记录已经存在的 AI 调用入口、输入边界、降级方式和状态权限。当前开发范围与最新验证结果见 [开发控制台](development.md)。

## 总结

AI 在本项目中分成两类：

1. **回合运行时候选**：意图解析、玩家叙述、NPC 赠礼/贿赂决策、日常机会候选、天气候选和物品交互物理候选。它们由回合或时间推进触发，必须经过结构校验与领域校验。
2. **显式 AI-物品资料工具**：日常物品价格/重量参考、未定义日常物品定义、严格配方评估和初始耐久候选。它们只在明确调用时运行；可缓存工具写入可审阅的图册缓存，初始耐久只返回候选。它们都不创建物品实例、不确认玩家获得，也不直接提交事件。耐久行为损耗和维修通过运行时 `item_wear` 适配器提出候选，仍由程序完成最终结算。

两类 AI 都不能直接写数据库、投影或确认事件。Python 后端是唯一的状态和规则权威。

## 回合运行时 AI

| 能力 | 入口 | 触发与输入边界 | 模型输出 | 程序最终负责 | 默认降级 |
| --- | --- | --- | --- | --- | --- |
| 玩家意图解析 | `ai/player/intent.py` | 本地解析无法得到可靠行动时；只发送玩家可见地点、人物、物品、出口和交互定义 | 最多 4 个结构化行动候选 | 可见性、目标、歧义、置信度、复合行动与命令转换 | 本地解析/澄清 |
| 玩家可见叙述 | `ai/player/narration.py` | 已确认 `Resolution` 与安全 `NarrativePlan` | 叙述提案，只能安排既有原子 | 事实原子、顺序、秘密过滤、玩家控制权、失败语义 | 确定性叙述 |
| NPC 社会决策 | `characters/decision.py` | 目前仅 `offer_item`；只发送该 NPC 有权读取的带来源上下文 | `accept/reject/counteroffer/delay/test` 候选 | 同地、物品/容器、底线、知识、关系、风险与后果 | 受约束本地拒绝/接受规则 |
| 日常机会候选 | `behavior/routine_rules.py` | `search_location` 或 `environment_action`；只使用已有地点机会与可见上下文 | 一个普通日常候选 | 来源、地点、影响级别、物品关键性和候选事件物化 | 无候选的确定性结果 |
| 天气候选 | `world/weather.py` 与 `ai/platform/weather_adapter.py` | 开局或跨入尚未确定的世界日期；只给日期、季节、硬约束和上一日天气 | 日期、天气类型、最低/最高温 | 季节范围、剧本硬约束、温差、天气/温度相容性、一次性日期事件 | 确定性天气候选 |
| 物品交互物理候选 | `ai/platform/item_interaction.py` 与 `behavior/item_interactions.py` | 玩家提出物品↔物品/家具/地点动作；只发送当前权威实例的可观察摘要和目标摘要 | `possible/impossible/clarify`、工具匹配、难度档、所需能力和物理依据 | 实例来源、所有权、位置、身体/手部、d20、程序 DC、目标 resolver、事件事务 | 模型关闭时安全拒绝；已有配方缓存可继续走程序校验 |
| 物品行为磨损候选 | `ai/platform/item_wear.py` 与 `behavior/item_interactions.py` | 已确认发生撬、切、撞、撕、刮等接触行为；发送当前物品和目标的可观察摘要 | 磨损等级、粗略损耗比例、相关能力、难度档和物理依据 | 程序夹逼比例、复用主行动 d20、计算最终损耗并提交 `item.wear_applied` | 模型失败时对已登记触发器使用保守程序档位；未知动作不自动损耗 |
| 维修方式与维修工具磨损候选 | `ai/platform/item_wear.py` 与 `behavior/commands/maintenance.py` | 已存在的维修目标、真实材料、真实工具和维修地点 | `repairLevel`、材料类别、相关能力、难度档；工具磨损另取受限磨损候选 | 程序确认实例、身体、d20、恢复上限、材料消耗和工具 `item.wear_applied` | 模型失败时需玩家明确维修等级；工具损耗使用同一维修检定，不重复掷骰 |

运行时模型开关均以环境配置为准；未配置时通常关闭。当前可查询的只读状态接口包括：

```text
GET /api/v1/system/intent-model
GET /api/v1/system/narrator-model
GET /api/v1/system/npc-decision-model
GET /api/v1/system/routine-model
GET /api/v1/system/weather-model
GET /api/v1/system/item-interaction-model
```

天气是已接入的世界事实生成链，但模型仍只提出候选；确认后才形成 `world.weather_determined` 事件。天气不会自行改变穿着、身体状态、地点拓扑或剧情。

## 显式 AI-物品资料工具

这些能力位于 `items/ai_items/`，因为它们处理的是物品资料与候选，而不是面向玩家的通用回合 AI。它们复用统一的 DeepSeek 配置；现有资料脚本要求显式 `--allow-ai`，初始耐久则只有调用方明确传入已启用适配器时才会请求模型。

| 工具 | 入口 | 可写入的资料 | 不得做的事 |
| --- | --- | --- | --- |
| 日常价格与重量参考 | `scripts/cache_daily_item_reference.py`、`items/ai_items/references.py` | `daily-item-references.json/md` 中按名称/别名/单位缓存的美元估价、重量、置信度和审计资料 | 模型不能给克朗定价、创建实例、修改所有权或确认来源；克朗由苹果比例程序换算 |
| 未定义日常物品定义 | `scripts/generate_daily_item_definition.py`、`items/ai_items/generation.py` | `daily-item-definitions.json/md` 中经 15 字段契约校验的普通日常定义、别名和参考关联 | 不生成剧情物品、货币、实例、位置、获得来源、通用 `usages`、耐久或权威效果 |
| 严格配方评估 | `scripts/assess_item_recipe.py`、`items/ai_items/recipes.py` | `generated-recipes.json/md` 中绑定时代资料/物品契约指纹的配方缓存 | 不改变输入材料、不能使用剧情物品/货币/文书、不得突破时代或质量守恒、不能直接扣料或创建产物实例 |
| 初始耐久候选 | `items/ai_items/durability.py`、`items/ai_items/deepseek_adapter.py` | 不写资料；返回类别、状态、相对最大值、剩余比例、置信度和依据 | 不返回权威 `current/max`，不改写锁定类别，不创建实例/事件；不处理腐坏、受潮或环境自动演化 |
| 物品来源确认 | `items/provenance.py`（程序契约，非模型裁决） | 交互编排器提供已确认的获取来源和定义状态 | `item.source_confirmed` 事件候选 | 程序要求 `location_take/furniture_take/npc_transfer/purchase/theft/recipe` 等来源，生成日用品必须先确认再创建实例 | 无来源则拒绝进入交互 |

日常物品定义和配方产物会复用正式图册及已有缓存；缓存命中不调用模型。配方产物缺失时，最多再触发一次日常物品定义生成。即使配方获接受，`items/recipes.py` 也只构造“消耗输入 + 创建产物”的候选事件计划，调用方仍需原子提交。初始耐久以崭新小刀 `100.0` 为基准，程序按类别上限和状态比例换算最终 float；一次性消耗品及锁定的非耐久类别零调用。

## IT-06 运行时 AI 边界

物品行为损耗和维修的首版运行时链已经接入。入口为 `ai/platform/item_wear.py`，维修命令为 `behavior/commands/maintenance.py`，事件投影为 `items/wear_events.py`。

该适配器只接收当前真实物品的可观察描述、已确认的行为/维修上下文和必要目标摘要，返回 `wearBand` 或维修等级、粗略比例/恢复上限、相关能力、难度档和物理依据。它不得返回最终损耗或恢复值、骰点、DC、能力修正、事件、消耗、销毁或成功结论。程序负责候选校验、`d20 + 能力修正`、数值上限、材料/来源、维修工具损耗和 `item.wear_applied` / `item.repair_attempted` / `item.repaired` 事件。

## AI 输入与输出的共同限制

- 模型输入必须按玩家可见性、NPC 私有认知或专用资料工具的最小需要裁剪。
- 模型返回必须通过 JSON/Schema、置信度和领域规则校验；超时、HTTP 错误、无效 JSON 或越权字段不污染状态或资料缓存。
- 重复提交回合不能重复调用模型；资料工具的缓存命中也必须零调用。
- AI 不能创造地点、NPC、关键物品、隐藏事实、剧情推进、关系变化、法律状态或玩家行动。
- 人物能力与语言风格是带来源资料，不是模型自动写入的 Canon 事实。
- 记忆检索不是 AI 事实源；它只从确认事件派生，并受作用域、时间和预算边界限制。

## 当前物理归属缺口

目标结构仍是 `ai/platform` 管底层传输，`ai/player` 管面向玩家的 AI 安全层。当前以下适配器仍与其领域编排代码共处：

- `ai/player/narration.py` 中的叙述 DeepSeek 适配器；
- `behavior/routine_rules.py` 中的日常 DeepSeek 适配器；
- `characters/decision.py` 中的 NPC 决策 DeepSeek 适配器。

它们的现有权限已经受候选协议约束；未来物理迁移到 `ai/platform` 时不得改变事件语义或扩大模型权限。AI-物品适配器保留在 `items/ai_items/` 是已确认的资料领域归属，不属于该迁移缺口。

## 验证

真实 API 验证必须控制调用量：每种能力使用最小代表性请求，记录模型/开关、成功或降级、Token 与错误原因。当前批次的真实调用结果及程序验证结果统一写入 [开发控制台](development.md) 的“验证与修复记录”，不回写历史阶段报告。
