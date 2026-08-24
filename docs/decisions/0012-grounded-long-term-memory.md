# ADR 0012：由确认事件派生、受角色视角约束的长期记忆

状态：已接受  
日期：2026-08-20

## 背景

长战役需要在数百回合后仍能回答“谁在什么时候做过什么”，也要让赠礼、欺骗、承诺、犯罪和通缉在未来继续影响人物。但聊天原文中可能包含玩家虚构的过去，叙述模型也可能写出并未结算的结果；语义相似度还能召回已经过时、属于别人的秘密或只是措辞相近的内容。

现有事件日志和当前状态投影已经分别承担“发生过什么”和“现在是什么”的权威职责。长期记忆不能成为第三套事实来源，只能是可重建的查询与上下文装配层。

## 研究依据

- Generative Agents（Park 等，2023）说明经历流、反思和动态检索可以支撑持续行为；本项目采用分层与动态检索思想，但不让模型生成的反思覆盖事件事实。
- LongMemEval（Wu 等，2024）把长期记忆能力拆成信息提取、跨会话关联、时间推理、知识更新和拒答；这五类成为本项目的评估主轴。
- RMM（Tan 等，ACL 2025）表明多粒度记忆与检索后反思有效；本项目先实现确定性候选和可审计 Bounding，取得评估数据后再加入模型重排。
- A-MEM（Xu 等，2025）通过动态属性和链接组织记忆；本项目借鉴“记忆节点与关系可演化”，但链接必须带来源，不能由相似度自动升级为事实。
- LoCoMo（Maharana 等，2024）显示长对话中的时间与因果关联是独立难点；因此检索协议把时间模式和因果来源作为结构化字段，而不是只拼进查询文本。
- THEANINE（Lee 等，NAACL 2025）强调保留旧信息并以时间与因果连接更新；因此旧事实不会因新事实出现而物理删除，系统区分“过去曾成立”和“当前仍成立”。
- Memory-Driven Role-Playing（Wu 等，ACL Findings 2026）提出 Anchoring、Selecting、Bounding、Enacting；本项目把它转化为事件锚定、候选选择、视角/时间/状态边界检查和受控上下文使用。
- MemMachine（Yang 等，2026）和 ReverieMem（Ge 等，2026）分别强调保持完整经历的可追溯核心、角色第一人称与可见性隔离；本项目首期保存事件级记忆及角色作用域，不把压缩摘要作为唯一证据。

以上是工程适配，不等于论文直接证明了本项目的具体数据库结构。

论文原始来源：

- [Generative Agents: Interactive Simulacra of Human Behavior](https://arxiv.org/abs/2304.03442)
- [LongMemEval: Benchmarking Chat Assistants on Long-Term Interactive Memory](https://arxiv.org/abs/2410.10813)
- [RMM: Reinforced Memory Management for Long-Term Conversation](https://aclanthology.org/2025.acl-long.413/)
- [A-MEM: Agentic Memory for LLM Agents](https://arxiv.org/abs/2502.12110)
- [LoCoMo: Evaluating Very Long-Term Conversational Memory of LLM Agents](https://arxiv.org/abs/2402.17753)
- [THEANINE: Revisiting Memory Management in Long-term Conversations with Timeline-augmented Response Generation](https://aclanthology.org/2025.naacl-long.435/)
- [Memory-Driven Role-Playing](https://aclanthology.org/2026.findings-acl.1175/)
- [MemMachine](https://arxiv.org/abs/2604.04853)
- [ReverieMem](https://arxiv.org/abs/2606.25632)

## 决策

1. `events` 继续是历史事实的唯一权威，当前问题继续查询事件投影；长期记忆表是可删除、可重建的读模型。
2. 首期只把允许列表内的已提交事件投影为情节记忆。玩家消息、AI 叙述、模型候选和被拒绝命令不能生成事实记忆。
3. 每条记忆使用确定性 `memory_id`，必须关联唯一 `source_event_id`，并保存事件类型、世界时间、重要度、实体和角色作用域。
4. 检索固定经过四步：结构化候选、角色作用域检查、时间/有效性 Bounding、预算裁剪。每一步产生不含私密正文的 Retrieval Trace。
5. NPC 只能读取 `npc:<自身 id>` 作用域及允许的公开记忆。玩家视角不能读取 NPC 私有关系变化或隐藏动机。
6. “现在”类问题直接路由到当前状态，不允许用历史记忆猜测；“以前、最早、最近一次”才进入情节记忆查询。
7. 旧记忆不因更新而删除。未来需要表达纠正、反转或失效时，增加带来源的记忆链接与有效区间；在该结构实现前，不进行自动语义覆盖。
8. 向量和模型重排只能扩充或排序已经锚定到来源事件的候选。任何候选在进入 AI 上下文前仍要重做作用域、时间和当前有效性检查。
9. 记忆 schema 从 `schema_version = 1` 开始。结构或语义改变时必须升级版本并提供全量重建策略；不得静默解释旧数据。
10. SQLite 用于当前纵向切片和自动测试。只有在检索评估证明需要时才迁移 PostgreSQL/pgvector，迁移不得改变事件数量或重放结果。

## 影响

正面影响：

- “前天送礼”能以来源事件进入今天的 NPC 判断；
- 玩家一句“我送过酒”不会污染记忆；
- 可以区分某人过去的位置与现在的位置；
- 能说明某条记忆为何入选、为何被挡下；
- 未来的向量检索和摘要算法可以替换，而不会改变世界事实。

代价与限制：

- 需要维护事件到记忆的版本化投影器和重建测试；
- 首期只覆盖与人物互动直接相关的事件，不等于完整的长期记忆系统；
- 角色错误信念、传闻传播、记忆纠正和多跳图关系留到后续切片；
- 在没有长期评估结果前，不提前引入复杂向量基础设施。

## 被否决的方案

- 把完整聊天记录持续塞给模型：成本无界，也无法区分声称与事实。
- 只使用向量库 Top-K：相似不等于真实、当前、可见或仍有效。
- 用摘要替代原始事件：压缩错误会失去可审计证据。
- 新事实覆盖并删除旧事实：会破坏“过去相信什么、何时改变”的时间推理。
- 让 AI 直接写记忆表：绕过确认事件和领域校验，产生第二套事实来源。
