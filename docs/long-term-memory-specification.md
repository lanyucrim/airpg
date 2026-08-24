# 长期记忆 MVP 详细规范

文档状态：v2.1  
实现阶段：5A～5C——有来源情节记忆、时间/因果链接、场景片段摘要、SQLite 全文候选与受约束一跳展开

> 文档定位：历史实现规范与当前能力基线，不是当前开发任务清单；当前开发模块和任务以 `docs/development.md` 为准。

## 1. 本阶段解决什么

首个纵向切片回答四个问题：

1. 哪些过去事件可以成为长期记忆？
2. 当前做决定的角色是否有权知道它？
3. 查询问的是过去还是现在，最早还是最近一次？
4. 系统为什么选中或排除了某条记忆？

本阶段不把“能做语义搜索”误当成“拥有可靠记忆”。向量、摘要、知识图谱和模型重排都排在事实锚定与权限边界之后。

## 2. 权威关系

```text
Raw Message ──只证明说过──┐
AI Narration ─只证明写过──┤
                          ├─不能直接生成事实记忆
Model Proposal ─候选──────┘

Confirmed Event（权威历史）
       ├── replay ──> Current Projection（权威当前状态）
       └── project ─> Episodic Memory（可重建历史索引）
```

发生冲突时：当前问题以投影为准，历史问题以来源事件为准，记忆摘要只用于快速选择和上下文表达。

## 3. 数据模型 v1

### `episodic_memories` v2

| 字段 | 约束 | 含义 |
|---|---|---|
| `memory_id` | 主键、确定性 | `mem_` + 来源事件 id；重复重建不会产生新记忆 |
| `campaign_id` | 外键 | 所属战役 |
| `source_event_id` | 唯一外键 | 唯一事实锚点 |
| `schema_version` | 当前为 1 | 投影语义版本 |
| `memory_type` | `interaction` / `relationship` | 首期记忆类型 |
| `event_type` | 原事件类型 | 精确筛选 |
| `summary` | 非权威 | 由纯 Python 模板生成，不由 AI 自由补写 |
| `importance` | 0～100 | 规则派生，只影响排序和预算 |
| `world_time` | 非负整数 | 统一世界时间 |
| `location_id` | 可空 | 事件发生地点（可确认时） |
| `status` | 首期 `active` | 为后续纠正/失效保留语义 |
| `update_key` | 可空 | 同一可更新状态槽；首期位置使用 `character_location:<id>` |

### `memory_entities`

一条记忆可以关联 actor、target、item 等多个实体。查询双方交往时要求 actor 与 target 都命中，避免只因共享一个 NPC 就混入别人的历史。

### `memory_scopes`

首期作用域为 `player:<id>`、`npc:<id>`。互动结果可分别投影给参与者；NPC 内部关系变化只投影给该 NPC。后续增加 `faction`、`scene` 和 `public` 时必须定义传播来源，不能仅凭事件发生就默认全世界知道。

### `retrieval_traces`

保存查询用途、视角、结构化条件、候选 id、排除原因、最终 id 和预算。普通调试 API 只返回这些 id 与原因码，不返回 NPC 私有摘要和模型私有上下文。

### `memory_links` v1

方向固定为“新记忆 → 旧记忆/原因记忆”：

- `updates`：新位置接续同一角色的旧位置；
- `caused_by`：关系变化指向事件载荷明确引用的赠礼或其他互动。

链接必须以新记忆的来源事件作为 `source_event_id`。旧位置仍保持 `active`，因为它对历史问题仍然有效；当前位置继续查询投影。

### `scene_memory_summaries` v1

按 `scene_id + location segment` 保存确定性摘要，包含事件序号和世界时间范围、生成器/版本、滚动或封存状态、已解决/未解决事项、来源事件和来源记忆。普通回合接口只返回摘要元数据，不返回可能包含 NPC 私密内容的正文。

## 4. 首期事件允许列表

| 事件 | 是否入记忆 | 类型 | 默认作用域 |
|---|---:|---|---|
| `gift.accepted` | 是 | interaction | actor 与 target |
| `gift.rejected/countered/delayed/tested` | 是 | interaction | actor 与 target |
| `bribe.accepted/rejected/countered/delayed/tested` | 是 | interaction | actor 与 target |
| `relationship.changed` | 是 | relationship | subject NPC |
| `character.moved` | 是 | state_change | 移动角色；并写位置更新槽 |
| `gift.offered` / `bribe.offered` | 否 | — | 结果事件已经表达完整交互，避免重复 |
| `item.transferred` | 否 | — | 当前归属由投影查询，接受事件已提供交互记忆 |
| 玩家消息 / 叙述 | 否 | — | 不是世界事实 |
| 启动定义事件 | 否 | — | 属于剧本初始状态，不是角色经历 |

允许列表必须版本化。增加新事件时同时补充成功、失败、幂等、作用域和重建测试。

## 5. 确定性摘要规则

摘要只使用事件载荷与事件发生时投影中已存在的名称：

- 接受：`哈维·科尔确实收下了艾拉·帕克交出的普通小刀。`
- 拒绝：`哈维·科尔没有接受艾拉·帕克交出的普通小刀。`
- 关系：`哈维·科尔因一次已确认事件改变了对艾拉·帕克的信任。`

摘要不能增加“因此愿意给钥匙”等未发生结果。找不到名称时使用实体 id，不能请求 AI 猜测。

## 6. 查询协议

`MemoryQuery v1` 包含：

- `campaign_id`；
- `purpose`，首期为 `npc_decision` 或 `debug`；
- `perspective_kind` 与 `perspective_id`；
- `information_need = historical`；
- 必须同时命中的 `entity_ids`；
- 可选 `event_types`；
- `time_mode = any / earliest / latest / before / after / between`；
- 可选时间边界；
- `limit` 与字符预算。
- 5C 新增可选 `search_text`、`retrieval_mode = structured / fts / hybrid`、`candidate_limit` 和 `expand_links`；这些只控制候选生成，不改变最终边界校验。

如果 `information_need = current`，检索器必须返回 `current_state_required`，由调用方改查投影；它不能从最新一条历史记忆推断当前值。

## 7. 检索流水线

```text
1. Structured Candidate
   campaign + entity overlap + event type + time window
2. FTS Candidate (optional)
   summary n-gram match, merged with structured candidates
3. Bounding
   exact entity match + perspective scope + active status + source exists
4. Ranking
   explicit time mode > importance > world time > deterministic id
5. Link Expansion (optional, max one hop)
   explicit `updates` / `caused_by` links only
6. Budget
   limit + character budget，超限项记录 budget_exceeded
7. Context Assembly
   只传最终记忆及 source_event_id
```

常见排除原因：`entity_mismatch`、`scope_mismatch`、`time_mismatch`、`inactive`、`source_missing`、`budget_exceeded`。

## 8. NPC 决策接入

`offer_item` 在调用 NPC 模型前，以目标 NPC 视角检索双方过去的赠礼、贿赂和关系变化。检索结果与当前物品归属、当前位置、人物档案和当前关系一起进入 `NpcDecisionContext`。

- 检索发生在数据库写锁之外；
- 模型只能引用返回的 `memory_id`；
- Python 再验证引用属于本次上下文；
- 决策及事件提交前重查幂等键和状态版本；
- 本轮生成的新记忆与事件、来源消息和命令在同一事务内提交；
- 重复请求直接返回旧响应，不重复建立记忆或检索轨迹。

## 9. 重建与迁移

记忆读模型可以在任意时刻按事件顺序全量重建：

1. 清空指定战役的记忆读模型；
2. 从空投影开始按序重放事件；
3. 对允许事件使用 v1 投影器；
4. 写入确定性 id、实体和作用域；
5. 比较重建前后的来源事件集合和行数；
6. 运行事件重放，确认当前状态完全不变。

升级记忆语义时创建 v2 投影器后全量重建，不在查询时猜测旧行含义。

## 10. 验收矩阵

- 确认赠礼会生成有来源、双方可见的记忆；
- 被拒绝、还价、延迟和试探也会留下真实互动历史；
- 缺少物品等前置条件失败不会生成事实记忆；
- 玩家虚构“我以前送过”不会生成记忆；
- 另一个 NPC 无法读取双方私下互动；
- 最早/最近/时间边界返回确定性结果；
- 当前状态请求被路由而不是由历史猜测；
- 重建两次结果相同；
- 幂等重试不增加记忆和轨迹；
- 每条结果都能追溯事件，再追溯玩家来源消息；
- 数据库查询计划使用实体、作用域和时间索引；
- 后端全量测试、覆盖率、前端检查与构建全部通过。

## 11. 阶段 5B 已完成内容

- `episodic_memories` 升级至 v2，并提供从 v1 增量迁移与全量重建；
- 位置新旧事实的 `updates` 链；
- 关系变化到真实互动的 `caused_by` 链；
- 场景地点片段摘要、来源序号、生成器版本和未解决目标；
- 正常回合只追加记忆增量，不全量删除重写；
- 18 项《灰港》确定性评估集和独立评分脚本。

## 12. 后续切片

5C 已加入可重建的 SQLite FTS5 候选表、候选截断指标和最多一跳的显式链接展开。FTS 只生成候选，不能覆盖实体、时间、作用域、当前状态或来源检查。下一步继续扩充至少 50 项语料和 200+ 事件压力场景，比较结构化、FTS、混合检索的 Recall、Precision、过时事实率、越权率、拒答率与候选成本。只有全文仍无法满足的语义召回场景达到明确数量后，才比较 embedding/重排；启用标准仍包括零越权、零当前状态误路由、零无来源事实。阶段 6 再实现角色知识、错误信念、目击和传播，不在阶段 5 里偷跑“所有人自动知道”。
