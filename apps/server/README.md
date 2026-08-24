# TRPG Python 服务

阶段 1～7 已完成基础纵向能力，7.5A 已完成 V4.2 内容编译，7.5B/7.5C/7.5G/7.5H 完成首个切片，7.5D 完成后端交易生命周期首个切片。当前服务实现一个可在本地规则与 DeepSeek 之间安全切换的权威游戏内核、可审计回合链和《灰港：黑潮王座》剧本包：

- SQLite 追加式事件日志；
- 从事件重建当前状态；
- 物品所有权与背包校验；
- 关系效果及来源事件；
- 世界时间；
- 命令幂等与状态版本；
- 玩家与叙述器原始消息；
- 通用 Action Schema；
- 玩家声称结果与决定权标记；
- message → command → event → projection → narration 来源链；
- 顺序复合行动与部分结果；
- 回合详情和近期消息调试接口。
- 多文件剧本包结构与引用完整性校验；
- 剧本版本、内容指纹与存档绑定；
- 剧本包编译为初始确认事件，禁止直接改写当前状态。
- 根据角色名称和剧本别名解析当前可见实体；
- 向任意在场角色请求其真实持有的物品；
- 把任意真实持有物交给明确接受该类礼物的角色；
- 未启用人物决策模型时保守拒绝未确认交易；启用后由 AI 综合人物上下文提案，规则仍校验权限与后果；
- 模糊物品或角色引用明确拒绝，不猜测目标；
- 条件化出口、权威门锁判定和秘密通道发现；
- 隐藏发现定义与 GM 条件不会进入玩家公开状态。
- 真实物品检查、NPC 话题询问与角色知识边界；
- 后端计算当前可用调查方向，前端不复制判定；
- 单回合主要节拍限制，避免复合输入一次推进多层剧情。
- 模型只接收玩家可见的解析上下文，并输出严格候选 Schema；
- 模型候选继续接受实体、权限和节拍校验，不能直接产生世界事实；
- 非法、超时、歧义或低置信度输出安全降级到本地解析器；
- 解析请求与原始输出保存在后端审计表，不进入世界投影，也不通过普通玩家接口返回。
- DeepSeek JSON 输出适配器、非思考/思考模式切换、固定超时和最多一次瞬时错误重试；
- 模型调用在 SQLite 写事务之外执行，提交前再次验证幂等键和状态版本；
- 审计记录提供方、模型、令牌用量与延迟，密钥不进入请求审计和网页。
- 版本化 32 项《灰港》意图评估集和准确率、安全、延迟、令牌发布门槛。
- 只接收确认结果和玩家可见变化的 AI 叙述器；
- Narrative Plan v1、Narration Proposal v3、逐字权威原子、秘密片段扫描、单节拍与玩家控制权校验；
- 2～5 段小说式叙述、计划内微动作、最终决策边界和同计划确定性降级；
- 剧本包 v5 场景叙事指引、`scene.started` v2 兼容重放和 GM 秘密预检；
- 独立叙述审计、确定性降级和事务外模型调用。
- 剧本包 v6 NPC 私有决策档案和物品经济价值；
- 结合人物、风险、关系与来源历史的 AI NPC 结构化决定；
- Python 对所有权、位置、底线、记忆来源和允许后果的二次校验；
- `npc_decision_attempts` 私有审计、保守降级、事务外调用和幂等提交；
- 贿赂接受只转移真实物品，不直接完成附带请求。
- 版本化 11 项《灰港》NPC 决策评估集、5 组成对上下文比较和真实 DeepSeek 发布门槛。
- 只由确认事件派生、可从事件流确定性重建的 `episodic_memories v2`；
- 记忆实体与玩家/NPC 作用域索引，当前状态请求不会由历史记忆猜测；
- 时间模式、精确实体匹配、角色视角 Bounding、字符预算和拒绝原因；
- 不暴露私密记忆正文的 Retrieval Trace，以及网页开发环境中的筛选轨迹；
- `NpcDecisionContext v2` 使用经过检索边界检查的历史互动，玩家和 AI 文本不能直接写记忆。
- 保留旧位置事实的 `updates` 链，以及关系变化指向真实互动的 `caused_by` 链；
- 按场景与地点片段生成的确定性摘要，保存事件序号、世界时间、来源事件、生成器版本和未解决目标；
- 新回合只追加记忆增量，旧记忆不会被每回合删除重写；手动重建仍能得到完全相同的业务字段；
- 18 项《灰港》长期记忆基线，覆盖事实、时间、更新、因果、拒答、当前路由、精确实体和 NPC 隔离。
- SQLite FTS5 中文双字候选、结构化/全文/混合模式、候选截断追踪和受约束一跳链接展开；240 条压力历史能召回结构化 Top-200 之外的相关旧记忆。
- V4.2 内容目录、Canon 层、地点机会和物品关键性边界；
- 注册式事件投影和无写锁回合流水线，旧投影分支仍作为历史兼容回放路径保留；
- 环境机会搜索、无结果行动、日/周/月世界结算和普通价格/库存候选；
- 物品堆叠数量、重量/体积容量、余额、临时报价、货币转移、购买实例化和交易来源链；
- 购买的 Python 权威接口和公开状态字段已存在，但网页交易按钮与完整商品目录尚未完成。

正式开发内容位于 `content/campaigns/gray-harbor/`，唯一来源是仓库根目录的《灰港：黑潮王座》原稿。修改内容后必须通过加载校验；已经创建的存档仍保留其创建时的剧本版本和内容指纹。

本地启动：

```powershell
$env:TRPG_INTENT_MODEL_ENABLED = "true"
$env:TRPG_NARRATOR_MODEL_ENABLED = "true"
$env:TRPG_NPC_DECISION_MODEL_ENABLED = "true"
$env:DEEPSEEK_API_KEY = "<只在本机设置>"
.\.venv\Scripts\python.exe -m uvicorn trpg_server.core.main:app --reload --port 8000
```

其余可选配置见 `.env.example`。默认不启用真实模型；仅设置密钥但未显式启用时不会产生调用费用。

运行版本化意图评估：

```powershell
.\.venv\Scripts\python.exe scripts\evaluate_deepseek_intents.py --compact
```

可以使用多个 `--case-id` 只比较失败用例，避免无意义地重复全部付费调用。

运行版本化 NPC 决策评估：

```powershell
.\.venv\Scripts\python.exe scripts\evaluate_deepseek_npc_decisions.py --compact
```

该脚本同样支持多个 `--case-id`，用于只复测失败的人物场景。

运行版本化长期记忆评估：

```powershell
.\.venv\Scripts\python.exe scripts\evaluate_long_term_memory.py
```

这套基线完全使用确定性记忆协议，不会调用模型或产生 API 费用。

接口文档位于 `http://localhost:8000/docs`。

主要调试接口：

```text
GET /api/v1/campaigns/{campaign_id}/messages
GET /api/v1/campaigns/{campaign_id}/turns/{turn_id}
GET /api/v1/system/intent-model
GET /api/v1/system/narrator-model
GET /api/v1/system/npc-decision-model
```
