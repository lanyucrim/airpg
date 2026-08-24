# 剧本包规范

## 1. 目的

剧本内容必须可以替换，而不要求重写 Python 规则。剧本包负责描述“战役开始时世界里有什么”，通用引擎负责判断“玩家能做什么、行动是否成功、会产生什么后果”。

剧本包不是完整预写剧情。它可以只有一个初始场景、少量人物和线索；后续可由人工或 AI 生成草稿，但草稿必须通过本规范的校验才能成为世界事实。

## 2. 权威流程

```text
剧本包文件
→ 结构校验
→ ID 与引用完整性校验
→ 计算内容指纹
→ 编译初始 Confirmed Events
→ Event Store
→ 状态投影
```

禁止使用以下捷径：

```text
剧本包 → 直接更新当前状态
```

这样可以保证初始世界与游玩后的世界使用同一套事件重放、审计和迁移机制。

## 3. 当前目录结构

```text
content/campaigns/gray-harbor/
├── manifest.json
├── locations.json
├── characters.json
├── containers.json
├── items.json
├── relationships.json
├── clues.json
├── scenes.json
├── organizations.json
├── facts.json
├── clocks.json
├── obligations.json
├── conditions.json
├── discoveries.json
├── interactions.json
├── affordances.json
├── generation-policies.json
└── v4.2-catalog.json
```

阶段 3A 使用 JSON，暂不引入 YAML、自定义脚本语言和图形编辑器。V4.2 由编译器从唯一 Markdown 原稿生成 `v4.2-catalog.json`，运行时只加载经过校验的结构化目录。

## 4. 清单字段

`manifest.json` 至少包含：

- `schemaVersion`：剧本包结构版本；加载器兼容 `1`～`8`，正式灰港 V4.2 内容使用 `8`；
- `scenarioId`：稳定的剧本标识；
- `version`：语义版本，例如 `0.1.0`；
- `name`：玩家可见名称；
- `initialWorldTime` 与 `timeUnit`：初始世界时间；
- `playerCharacterId`：玩家角色引用；
- `initialSceneId`：初始场景引用。

版本 2 还要求 `initialCalendar`，用于把统一世界分钟显示为剧本历法。版本 2 的四个新增文件分别承载组织、客观事实、时钟和义务。版本 3 新增剧情条件与发现定义，并要求每条出口具有稳定 ID。版本 4 新增物品检查与 NPC 话题询问定义。版本 5 要求每个场景提供玩家安全的 `narrativeGuidance`。版本 6 要求每个 NPC 提供私有 `decisionProfile`，物品可以提供 `economicValuePence` 与 `tags`。版本 7 预留地点机会、临时资源和世界自治字段。版本 8 正式绑定 V4.2 源文件、编译目录和 Canon 层级，并要求所有剧本道具声明 `category`、`criticality`、`storyBindingPolicy` 与 `operations`。`facts.json` 中的 GM 事实以及角色 `privateNotes`、`secrets`、决策档案、GM 条件、未发现出口和尚不可用的交互结果不能进入普通玩家状态接口或叙述模型请求。

## 5. 引用规则

- 每类实体内部 ID 不得重复；
- 角色的地点必须存在；
- 地点连接必须指向存在的地点；
- 容器必须且只能归属于一个角色或地点；
- 物品的容器必须存在；
- 关系双方必须是存在的角色；
- 线索的初始知情者必须存在；
- 场景地点、初始场景和玩家角色必须存在；
- `playerCharacterId` 指向的角色类型必须是 `player`。
- NPC 声明接受的礼物定义必须存在于当前剧本包；
- 物品关联的拒绝替代线索必须存在。
- 版本 3 出口必须有唯一 ID，钥匙、剧情条件和发现引用必须存在；
- 发现定义引用的地点、事实、线索、出口、条件和初始知情者必须存在。
- 物品检查必须引用真实物品；询问必须引用类型为 NPC 的角色；
- 检查或询问披露的事实与线索必须存在，线索所指事实必须同时列入该交互的披露事实；
- 版本 5 的每个场景必须包含 `narrativeGuidance`，且不得直接包含 GM 事实、秘密条件、隐藏出口、人物私密材料或组织私密目标；
- 版本 6 的每个 NPC 必须包含合法 `decisionProfile`；所有倾向值为 0～100，月收入不能为负，硬底线只能使用已声明枚举；
- 物品的 `economicValuePence` 不能为负，`tags` 用于后续规则与人物偏好，不能替代真实物品实例和所有权；
- 版本 8 的剧本道具必须声明 `category`、`criticality`、`storyBindingPolicy` 和非空 `operations`；关键道具默认只能使用 `script_defined_only` 绑定政策；
- `affordances.json` 中的地点机会必须引用真实地点、时间策略、资源模板和影响等级；机会模板不是确认事实，也不能绕过事件提交；
- `generation-policies.json` 只能声明 AI 可提出的普通内容类别、生命周期和影响上限，不能授予 AI 创建关键道具、永久地点、主线任务或重大后果的权限；

任一规则不满足时，整个剧本包拒绝加载，不允许“尽量启动”。

## 6. 版本与存档

加载器会对全部必需文件计算 SHA-256 内容指纹。创建存档时同时固定记录：

```text
scenarioId
scenarioVersion
scenarioContentHash
```

版本号表达作者声明，内容指纹证明实际文件内容。两者必须同时保存，因为作者可能误改内容却忘记升级版本号。

已存在存档不会因为磁盘上同名剧本包更新而自动改变。未来如果需要升级旧存档，必须提供显式迁移并验证事件数量和重放结果。

## 7. 初始事件

阶段 3A 编译的事件包括：

- `campaign.created`；
- `location.created`；
- `container.created`；
- `character.created`；
- `scene.started`；
- `item.created`；
- `clue.defined`；
- 可选的 `relationship.initialized`、`knowledge.learned` 和 `story.clue_revealed`。
- 版本 3 的 `story.condition_defined`、`discovery.defined`，以及初始知情者对应的 `location.exit_discovered`。
- 版本 4 的 `inspection.defined` 与 `inquiry.defined`。
- 版本 5 的 `scene.started` 使用事件 `schemaVersion: 2` 携带玩家安全叙事指引；旧剧本包仍生成版本 1，投影器兼容重放两种版本并拒绝未知版本。
- 版本 6 的 `character.created` 使用事件 `schemaVersion: 2` 携带私有 NPC 决策档案；旧剧本包仍生成版本 1，投影器兼容两种版本并拒绝未知版本。
- 版本 8 的 `campaign.created` 记录 `scenarioSourceVersion`、`scenarioSourceDocument`、`scenarioSourceSha256` 和 `scenarioCatalogSchemaVersion`，确保运行存档与 V4.2 原稿及编译目录绑定。

同一剧本内容与同一战役标识会得到相同的初始事件 ID，便于测试和重放比较。两个不同战役不会共享事件 ID。

## 8. 当前边界

阶段 3B 已允许请求和给予规则读取任意角色、物品和容器，不再依赖守卫、红酒和钥匙 ID。剧本包还可以配置：

- 角色 `aliases`；
- 角色明确接受的 `acceptedGiftDefinitionIds`；
- 物品 `aliases`；
- 物品的 `requestPolicy`；
- 礼物造成的 `giftEffects`；
- 请求被拒绝时可提供的 `refusalClueId`。

安全默认值是：NPC 不会自动接受未声明的礼物，`owner_discretion` 物品也不会因为玩家开口就自动交付。

地点现在支持父级层次、显式出口、移动耗时、初始可见性、剧情条件和钥匙要求。隐藏出口只有在规则提交 `location.exit_discovered` 后才对相应角色可见；玩家说“我知道暗道”不能跳过发现条件。当前仍未完成容器容量、堆叠数量拆分和真正的 AI 意图解析。

版本 4 的调查定义只描述内容，不直接改变状态：

- `inspection` 必须锚定真实物品和访问策略；物品不在可接触容器时不能检查；
- `inquiry` 必须锚定具体 NPC；NPC 不在场或不知道待披露事实时不能给出该答案；
- 完成后写入 `interaction.completed`，相同调查不能重复生成同一线索；
- 玩家接口只接收后端计算出的 `availableActions`，前端不自行判断可用性；
- 单回合达到场景 `maxMajorBeatsPerTurn` 后，复合行动暂停，不能一次揭开多层剧情。

版本 5 的场景叙事指引只约束表达与停止位置，不授予新事实：

- `premise`：当前场景的玩家安全前提；
- `hardAnchors`：不能被叙述随意改写的危机和因果；
- `flexibleApproaches`：调查、谈判、交易、施压等允许重排的解决策略；
- `stopBefore`：发现责任人、重大承诺、违法、不可逆代价或解决主线前必须归还控制权的位置；
- 指引随 `scene.started` v2 进入可重放投影，再由后端构造 Narrative Plan；它不是事件提案，也不能替代规则判定。

版本 6 的 NPC 决策档案为 AI 提供人物上下文，不是接受或拒绝条件表：

- `monthlyIncomePence` 与 `economicPressure` 表达经济处境；
- `giftOpenness`、`greed`、`integrity`、`riskAversion`、`institutionalLoyalty` 和 `corruptionOpenness` 表达可能互相冲突的性格与立场；
- `hardRefusals` 只描述普通利益不能越过的明确底线；
- AI 综合档案、关系、风险、物品价值和来源记忆提出决定，Python 只校验硬边界并提交事件；
- 档案经 `character.created` v2 进入私有可重放投影，不得在普通玩家状态或叙述请求中暴露具体数值。

版本 8 的地点机会和日常生成政策同样不是动作白名单：

- 地点机会声明资源类别、营业时间、参与者、价格范围、库存来源和 `storyImpact` 上限；
- 玩家提出未逐字写入剧本的合理行为时，AI 可以在机会边界内提出候选，程序必须重新校验地点、时间、资源、权限、故事影响和 NPC 认知；
- 普通日常候选默认只能创建 `mundane` 或 `contextual` 物品，且必须通过 `item.created` 事件获得容器、所有者和来源；
- 搜索无结果、售罄、买不起、关门、被拒绝和延期都是合法结果，必须记录行动与时间消耗；
- 只有显式的 `story.item_bound` 或剧本定义事件可以把普通实例绑定到故事条件，AI 叙述不能直接改变关键性。

## 9. 原稿与编译内容的边界

`灰港_黑潮王座_V4.2_AI_GM主线状态机与支线条件版.md` 是当前唯一内容来源，不会整篇直接塞入运行状态或模型上下文。正式剧本包只编译当前已经过结构校验、引用校验和测试的可玩切片。未编译章节仍是作者参考资料，不得被系统当成已经发生的事实。旧 V3 目录只作为历史存档兼容输入，不得被新战役默认加载。

V4.2 编译器必须保存源文件 SHA-256、条目源行号、片段指纹、Canon 层级（C0/C1/C2/G）和 `scenarioVersion`。G 层模板只有在满足触发条件并提交确认事件后，才能实例化为 C1 世界事实。

当前 `gray-harbor` 包只覆盖白鹭屋开局、最后通牒、债务询问、账本异常和第五章暗道发现所需的权威结构。仓库不再包含其他产品剧本或旧演示剧本包。
