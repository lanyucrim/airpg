# 阶段 6 设计与开发流程：NPC 认知与后果传播

状态：已完成（基础链路与专项回归）

## 权威分层

世界事实由 `crime.committed`、物品、位置等确认事件表达；角色认知由带来源的 `npc.cognition_changed` 表达；信念、怀疑和否认不能改写 `world_facts`。同一命题可以在不同角色中同时存在 `known`、`believed`、`suspected` 或 `denied`，并保留认知历史、获取方式、可信度和有效期。

## 开发流程

1. 先增加 `CognitionState`、`EffectState`、`WantedState`、认知历史和法律记录投影，并为旧事件保留 schema 兼容。
2. 以来源前驱表校验法律链：`crime.committed → witness.observed → information.reported/withheld → evidence.registered → suspect.identified/described → wanted.issued`。顺序错误、跨时间倒流和无来源事件都拒绝投影。
3. 通过 `notice.scheduled → notice.received` 实现延迟传播；只有送达事件才能创建目标 NPC 的法律认知。没有目击、报告或通知时，其他 NPC 不会自动知道。
4. 支持 `witness / told / document / faction_report / rumor / inference / system` 来源，允许错误传闻、隐瞒、置信度和过期。态度变化必须引用目标 NPC 自己的认知事件。
5. 将持续影响统一保存来源、作用对象、范围、创建时间、到期时间和状态；声望使用分群 `groupId`，法律状态使用 `jurisdictionId`。
6. 在等待、休息、移动和长时间缺席时结算通知、认知和效果到期；不自动生成目击者或犯罪事实。

## 验收结果

- 认知来源、延迟通缉、完整法律链、隐瞒、错误传闻、相反信念、态度来源和过期均有专项测试。
- NPC 决策只装配自身认知与通知；一个 NPC 收到的秘密不会出现在另一个 NPC 的上下文。
- 通缉不会从签发瞬间扩散到全世界；世界事实和错误信念保持分离。
