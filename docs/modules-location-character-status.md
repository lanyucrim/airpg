# 人物与地图/地点模块参考快照

状态：只读参考，不是当前任务清单  
更新日期：2026-08-24  
当前焦点、问题编号和验证结果见 [开发控制台](development.md)。

本文件保留人物与地图/地点的稳定使用入口，避免重复维护“当前完成度”。此前带有旧人数、旧状态和人物焦点的批次叙述已移入 [开发历史归档](development-history.md)。

## 当前基线

| 模块 | 当前可依赖的能力 | 仍不代表已完成的部分 |
| --- | --- | --- |
| 地图与地点 | 灰港图册、区域元数据、街道拓扑、地点结构、公开/隐藏出口、相邻移动、路线查询、公开读模型、占用派生和天气跨地点移动加时。 | 地点能力、NPC 位置演化、可见物品过滤、运营/权限、交通、环境状态和图册静态校验。 |
| 人物 | 142 个运行时人物的档案/位置、独立背包归属、关系与认知基础事件、能力/语言风格上下文、身体槽位、装备和外伤。 | 内伤、每日身心状态、完整关系图册映射、NPC 自主轨迹、能力判定、语言生成和更多 NPC 决策。 |

这些能力的精确健康度和活跃问题以 `LO-*`、`CH-*`、`CX-*` 为准，不在本文件重复记录。

## 地图与地点稳定事实

- 七个大区是地图 `regions` 元数据，不是可进入或可停留的运行时地点。
- 街道是无内部结构的通行层。玩家从建筑/结构出门进入所属街道，再经街道位置与连接到达其他建筑；同街道不等于一分钟直达。
- 普通地点内部结构默认公开、可通过相邻出口进入；只有剧本明确标记的隐藏地点或秘密出口继续使用 `discoveryId` 和发现状态。
- 当前位置由权威投影保存。公开地图可显示“区域 · 地点 · 结构”，但房间不作为全城地图的独立城市节点。
- 图册是静态输入；它不能直接写位置、占用、物品、出口发现或任何运行时状态。
- 天气只对跨顶层地点的移动时间产生程序计算的加时；同一地点内部结构移动不受影响。

主要入口：

```text
trpg_server.map.atlas.load_map_atlas()
trpg_server.map.routing.find_map_route()
trpg_server.map.public.public_map()
trpg_server.map.occupancy.build_location_contents()
trpg_server.locations.movement.evaluate_movement()
trpg_server.map.capabilities.build_location_capability_context()
```

玩家端地点移动只使用后端确认的相邻出口；全城图册 UI 已移除。地图只读 API 包括：

```text
GET /api/v1/campaigns/{campaignId}/map
GET /api/v1/campaigns/{campaignId}/map/atlas
GET /api/v1/campaigns/{campaignId}/map/route
```

## 人物稳定事实

- 每个运行时人物都有唯一的 `inventory` 容器；人物模块只维护背包归属，物品定义、数量、容量、转移、消费和交易仍归物品领域。
- 能力与语言风格是 `character.created` 可读取的带来源上下文，不授予行动权限，也不自动出现在玩家公开状态。
- 装备状态只引用物品实例 ID；`worn` 与 `held` 分层。人物领域校验身体功能和槽位，物品领域声明装备规格。
- 外伤通过独立确认事件记录部位、严重度、活动状态和功能影响；内伤不在现有字段、事件或规则中。
- NPC 的模型社会决策目前只覆盖 `offer_item`。模型只能提出候选，人物规则与核心事件决定最终结果。
- 图册关系、住处和日常轨迹是资料与后续接口，不能被当成完整的运行时事实。

主要入口：

```text
trpg_server.characters.events
trpg_server.characters.inventory.character_inventory_container()
trpg_server.characters.inventory.character_inventory_item_ids()
trpg_server.characters.traits.build_character_traits()
trpg_server.characters.decision.build_npc_decision_context()
```

## 边界提醒

- 人物和地点都不能直接修改物品实例。
- 地点能力与 NPC 轨迹目前只能提出候选，不能绕过领域校验或核心事件。
- 当前人物/地点状态类型的一部分仍集中在 `core/state.py`；这是一项独立公共协议迁移，不是普通模块功能可以顺带改动的内容。
- 更详细的历史人数、图册统计、旧接口和验收记录仅用于追溯，见 [开发历史归档](development-history.md)。
