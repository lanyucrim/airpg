# 事件规范

## 1. 事件定义

事件描述一件已经发生的事情。事件只能追加，不能原地改写。当前状态由事件投影得到。

## 2. 标准事件信封

每个事件必须包含：

```json
{
  "eventId": "evt_01...",
  "campaignId": "cmp_01...",
  "turnId": "turn_01...",
  "sequence": 184,
  "eventType": "item.transferred",
  "schemaVersion": 1,
  "worldTime": 39720,
  "recordedAt": "2026-08-19T12:00:00Z",
  "actorId": "char_player",
  "causationId": "cmd_01...",
  "correlationId": "turn_01...",
  "payload": {},
  "metadata": {}
}
```

约束：

- `sequence` 在单个战役内严格递增；
- `worldTime` 是从战役纪元起单调递增的整数刻度，刻度单位由战役配置固定；`recordedAt` 是现实记录时间；
- `schemaVersion` 用于事件结构迁移；
- `payload` 只包含重建该事实所需的信息；
- AI 的自然语言思考不得写入权威事件。

## 3. 命名规范

事件类型使用“领域.已完成动作”形式：

```text
campaign.created
location.created
organization.created
container.created
character.moved
speech.spoken
question.asked
npc.answer_given
item.examined
item.transferred
commerce.offer_found
item.purchased
commerce.completed
gift.offered
gift.accepted
gift.rejected
bribe.offered
bribe.accepted
bribe.rejected
bribe.countered
bribe.delayed
bribe.tested
item.requested
request.accepted
request.refused
relationship.changed
relationship.initialized
knowledge.learned
world.fact_defined
clock.created
obligation.created
story.clue_revealed
clue.defined
claim.made
belief.adopted
crime.committed
wanted.issued
time.advanced
scene.started
scene.location_changed
scene.beat_advanced
inspection.defined
inquiry.defined
interaction.completed
```

事件必须使用过去完成含义，不能使用 `item.transfer` 这种像命令的名称。

## 4. 初始事件目录

| 事件类型 | 用途 | 必要信息 |
|---|---|---|
| `campaign.created` | 创建战役 | 战役标识、初始时间 |
| `location.created` | 从剧本包创建地点 | 地点标识、名称、连接 |
| `organization.created` | 从剧本包创建组织 | 组织标识、公开范围、成员与资源 |
| `container.created` | 从剧本包创建容器 | 容器标识、类型、所有者或地点 |
| `character.created` | 创建角色 | 角色标识、角色类型、初始位置；v2 可携带私有 NPC 决策档案 |
| `scene.started` | 启动或切换当前场景 | 场景、地点、阶段、在场角色、开场文字和节拍上限；v2 增加玩家安全叙事指引 |
| `character.moved` | 角色移动 | 来源地点、目标地点 |
| `speech.spoken` | 角色说话 | 说话者、听众、原文 |
| `question.asked` | 向 NPC 提出结构化话题 | 提问者、NPC、话题、原文 |
| `npc.answer_given` | NPC 基于自身知识作答 | 说话者、听众、话题、披露事实、关联问题 |
| `item.examined` | 检查真实存在且可接触的物品 | 角色、物品、容器、交互定义 |
| `item.created` | 创建物品实例 | 物品定义、初始容器、数量 |
| `item.transferred` | 物品转移 | 物品、来源容器、目标容器、数量 |
| `commerce.offer_found` | 记录已观察到的临时报价 | 报价、地点、库存、单价、来源机会 |
| `item.purchased` | 从报价实例化买方物品并扣减库存 | 报价、数量、目标容器、物品来源 |
| `commerce.completed` | 记录已完成交易并递减报价库存 | 交易、双方、数量、金额、来源 |
| `gift.offered` | 提出赠礼 | 赠送者、接收者、物品 |
| `gift.accepted` | 接受赠礼 | 接收者、物品、关联提议 |
| `gift.rejected` | 拒绝赠礼 | 接收者、物品、关联提议 |
| `bribe.offered` | 提出以真实物品换取通融 | 提供者、接收者、物品、请求风险 |
| `bribe.accepted` | NPC 收下贿赂物品 | 接收者、物品、决策因素和引用记忆；不表示请求已执行 |
| `bribe.rejected` | NPC 拒绝贿赂 | 接收者、物品、决策因素 |
| `bribe.countered` | NPC 要求调整条件 | 接收者、物品、固定条件代码 |
| `bribe.delayed` | NPC 暂缓决定 | 接收者、物品、固定条件代码 |
| `bribe.tested` | NPC 要求证明或先建立信任 | 接收者、物品、固定条件代码 |
| `item.requested` | 请求取得物品 | 请求者、持有人、物品、原始文本 |
| `request.accepted` | 物品持有人接受请求 | 关联请求、物品 |
| `request.refused` | 拒绝一项请求 | 关联请求、原因、可选替代方案 |
| `relationship.changed` | 关系变化 | 主体、对象、维度、变化量、来源事件 |
| `relationship.initialized` | 从剧本包建立初始关系 | 主体、对象、各维度初始值 |
| `knowledge.learned` | 角色得知事实 | 角色、事实、来源事件 |
| `world.fact_defined` | 登记客观命题 | 事实、真值、公开范围、标签 |
| `clock.created` | 建立期限或后台时钟 | 起止时间、状态、公开范围 |
| `obligation.created` | 建立债务、承诺或合同 | 双方、条款、状态、期限与证据 |
| `story.clue_revealed` | 向玩家公开线索 | 线索、说明、来源事件 |
| `clue.defined` | 从剧本包登记可用线索 | 线索、事实、标题和说明 |
| `claim.made` | 角色陈述一个待核验主张 | 主张类型、对象、是否存在历史证据 |
| `time.advanced` | 推进世界时间 | 起止时间、原因 |
| `scene.location_changed` | 当前场景随角色移动换地点 | 来源地点、目标地点、移动事件 |
| `inspection.defined` | 从剧本包登记物品检查 | 物品、访问策略、披露事实与线索 |
| `inquiry.defined` | 从剧本包登记 NPC 询问话题 | NPC、话题、知识边界与披露事实 |
| `interaction.completed` | 记录角色已经完成某项调查 | 角色、交互定义、来源事件 |

成功移动的事件时间必须单调一致。当前实现先追加到达时刻的 `time.advanced`，再在同一到达时刻追加 `character.moved`、`scene.location_changed` 和 `scene.beat_advanced`。失败移动不产生任何事件。

## 5. 赠礼示例

成功赠礼至少产生：

```text
gift.offered
gift.accepted
item.transferred
relationship.changed
time.advanced
```

其中 `relationship.changed` 必须引用导致变化的赠礼事件：

```json
{
  "eventType": "relationship.changed",
  "payload": {
    "subjectId": "martha_bell",
    "objectId": "protagonist",
    "dimension": "favor",
    "delta": 12,
    "sourceEventId": "evt_help_accepted"
  }
}
```

拒绝赠礼时不得生成物品转移，除非存在“扣押”等其他明确事件。

AI 辅助决定仍不能直接产生上述事件。模型只提出受限决定，Python 校验当前所有权、位置、接收容器、人物底线、事实与记忆来源后组装事件。`bribe.accepted` 与 `item.transferred` 只能证明 NPC 收下物品；交钥匙、放行、隐瞒通缉等后续请求必须另行判定并产生自己的确认事件。

## 6. 投影规则

事件写入后，由纯规则更新投影：

```text
item.transferred
→ 验证来源容器当前数量
→ 减少来源数量
→ 增加目标数量或更改唯一物品容器

relationship.changed
→ 找到有方向的角色关系
→ 更新指定维度
→ 保留来源事件索引
```

相同事件重复投影不能产生重复效果。投影器必须记录最后处理的战役序号。

## 7. 修正历史

禁止删除或修改已发布事件。需要纠错时追加明确修正事件，例如：

```text
event.voided
item.transfer_reversed
relationship.adjustment_corrected
```

修正事件必须引用被修正事件并记录原因。是否允许普通玩家触发修正，由存档和管理员权限另行规定。

## 8. 事件与叙述的边界

事件保存：

```text
玩家检查了白鹭屋地窖
当前条件下没有发现新的通路
```

叙述可以写成不同文风，但不能新增以下事实：

```text
玩家发现了尚未暴露的秘密通道
玩家已经穿过通道进入废弃面包房
玛莎坦白了她并不知道的债务真相
```

## 9. 消息来源

由玩家回合产生的确认事件必须通过 `event_sources` 或等价结构引用触发它的原始消息：

```text
message_id
event_id
source_kind = trigger_input
```

该引用表示“此输入触发了这次判定”，不表示玩家输入本身已经被当成事实。叙述输出不能作为同一回合确认事件的反向来源。

系统初始化事件可以来自战役模板，不强制伪造一条玩家消息；其 `causationId` 应明确标记为系统初始化命令。

## 10. 剧本包初始化

剧本包不能直接写入当前状态表。固定流程是：

```text
剧本包
→ Schema 与引用校验
→ 初始事件编译
→ 追加到事件日志
→ 重放生成当前状态
```

同一剧本内容、同一战役标识必须生成相同的初始事件标识。`campaign.created` 必须记录剧本标识、语义版本和完整内容指纹，使存档可以证明自己基于哪一份内容创建。

## 11. 访问条件与发现

版本 3 剧本包使用以下事件维护条件与角色级发现：

```text
story.condition_defined
story.condition_activated
investigation.performed
location.exit_discovered
knowledge.learned
story.clue_revealed
```

`location.exit_discovered` 必须包含 `characterId`、稳定的 `exitId`、`discoveryId` 和 `sourceEventId`。发现属于具体角色，NPC 知道路不代表玩家知道。未满足发现条件的搜索可以记录调查和耗时，但不得生成出口发现、知识或线索事件。

门锁判定读取权威物品容器。玩家输入或叙述声称持有钥匙不能满足通行条件。

## 12. 调查与 NPC 回答

物品检查的最小成功链是：

```text
item.examined
knowledge.learned（如有新事实）
story.clue_revealed（如有新线索）
interaction.completed
time.advanced
scene.beat_advanced
```

NPC 话题回答的最小成功链是：

```text
question.asked
npc.answer_given
knowledge.learned（仅限 NPC 确实知道并披露的事实）
interaction.completed
time.advanced
scene.beat_advanced
```

玩家说“我检查后发现某人伪造签名”只能作为 `claimedOutcome` 留在命令中。规则只提交剧本定义允许的结果；玩家原话、AI 解析和叙述都不能新增知识或线索。

## 13. `scene.started` 事件版本

`scene.started` 的 `schemaVersion: 1` 保存原有场景字段。版本 5 剧本包编译为 `schemaVersion: 2`，在原载荷上增加：

```json
{
  "narrativeGuidance": {
    "premise": "玩家安全的场景前提",
    "hardAnchors": ["不可随意改写的危机和因果"],
    "flexibleApproaches": ["允许玩家采用的策略"],
    "stopBefore": ["必须归还控制权的边界"]
  }
}
```

投影器必须继续接受版本 1，并把缺失指引投影为空值，以保证历史存档可重放；版本 2 才读取上述字段。任何未知事件版本必须明确拒绝，不能猜测或静默忽略。叙事指引是可重放的场景约束，不是已经发生的新剧情事实。

## 14. `character.created` 事件版本

`character.created` 的 `schemaVersion: 1` 保存原有角色字段。版本 6 剧本包编译为 `schemaVersion: 2`，NPC 可以额外携带私有 `decisionProfile`。投影器继续接受版本 1，并把缺失档案视为未配置；版本 2 才建立决策档案投影。未知版本必须明确拒绝。

决策档案的具体数值不进入普通玩家状态。运行中修改档案时不能静默覆盖投影；后续必须新增明确的版本化档案变更事件和迁移策略。
