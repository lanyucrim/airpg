# 阶段 2 验证报告

> 验证日期：2026-08-19  
> 阶段：通用回合与原始记录  
> 结果：通过

## 1. 本阶段目标

建立一条可以审计且适合未来长期记忆系统使用的完整链路：

```text
Raw Message
→ Structured Action
→ Resolution
→ Confirmed Event
→ Current State Projection
→ Narration
```

## 2. 已实现内容

- append-only 原始消息表；
- 玩家输入和叙述输出逐字保存；
- 消息权威范围标记；
- 通用 Action Schema；
- `claimed_outcome`、`authority` 和 `resolution_required`；
- Action 对来源消息的引用；
- Confirmed Event 对触发消息的引用；
- 回合版本前后变化记录；
- 回合详情和近期消息 API；
- 开发页面来源链；
- 角色内说谎与玩家叙述性主张分离；
- 顺序复合动作和部分结果；
- 旧数据库增量迁移；
- 实际查询所需的 SQLite 索引。

## 3. 论文设计如何影响实现

本阶段没有提前加入向量数据库，而是先实现论文共同强调的可追溯基础：

- [MemMachine](https://arxiv.org/abs/2604.04853)：保留完整原始片段，减少有损抽取成为唯一历史；
- [LongMemEval](https://arxiv.org/abs/2410.10813)：为后续 Indexing、Retrieval、Reading 分阶段评估保留来源数据；
- [LoCoMo](https://arxiv.org/abs/2402.17753)：保留时间、顺序和因果关系，支持未来时序与因果问题；
- [Memory-Driven Role-Playing](https://aclanthology.org/2026.findings-acl.1175/)：Action 中显式保留 Anchoring 和 Bounding 所需的来源与权威信息。

原始消息只对“谁说过什么”具有权威性。世界事实仍然只来自 Confirmed Event。

## 4. 自动测试

### 阶段 1 基线

- 后端：9 项测试通过；
- 覆盖率：94%；
- 前端静态检查：通过；
- 前端构建与渲染测试：通过；
- 阶段 0 文档验证：通过。

### 阶段 2 完成后

- 后端：21 项测试通过；
- 覆盖率：95%；
- 前端静态检查：通过；
- 前端生产构建：通过；
- HTTP 端到端来源链测试：通过；
- 阶段 0～2 文档验证：通过。

## 5. 关键验收结果

| 场景 | 结果 |
|---|---|
| 原始输入包含前后空格和标点 | 完整原样保存 |
| 玩家说“我已经拿到了钥匙” | 主张被否定，钥匙未进入背包 |
| 玩家对守卫说“我拿到了钥匙” | 记录为角色发言，不改写世界 |
| 已送出的酒再次赠送 | 行动拒绝，版本不变，但消息保留 |
| 相同幂等键重复提交 | 返回原结果，不重复写消息与事件 |
| 先说话、再赠送不存在的酒 | 说话事件保留，赠送失败 |
| 先等待、再索要钥匙 | 按顺序结算，时间与后续判定一致 |
| 查询回合详情 | 能看到消息、命令、事件、来源和版本变化 |
| 从事件重放状态 | 与提交后的当前状态一致 |

## 6. 未在本阶段实现

- 大模型意图解析；
- 向量检索和 Reranker；
- PostgreSQL / pgvector；
- Scene、Quest、Chapter 摘要；
- NPC 视角记忆；
- 完整通用世界规则。

这些内容将在后续阶段建立在本阶段的来源链之上，不会绕过 Confirmed Event。
