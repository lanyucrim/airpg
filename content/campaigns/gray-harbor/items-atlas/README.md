# 灰港物品图册

本目录整理 V4.2 的物品基础资料、统一字段、重量、价值、堆叠规则和货币体系。

- `item-field-specification.md/json`：所有物品统一使用的 15 字段正式规范。
- `important-items.md/json`：核心定义、日常种子定义、四种货币定义和开局实例。
- `currency-system.md/json`：克朗、银盾、金冠、王库券四级货币、价值换算及对应物品定义 ID。
- `ai-items/daily-item-references.md/json`：AI 辅助的日常物品价格与单件重量缓存；苹果固定为 10 克朗基准。
- `ai-items/daily-item-definitions.md/json`：AI 生成的可复用日常物品定义；内嵌记录仍严格使用正式物品字段。
- `ai-items/era-technology-profile.md/json`：配方 AI 使用的剧本时代与技术边界，区分 Canon 和推导约束。
- `ai-items/generated-recipes.md/json`：已通过程序硬校验的普通物品配方缓存。
- `items/ai_items/durability.py`：初始耐久 AI 候选契约；只返回相对值，由程序换算和封顶。
- `properties.equipment`：可选的穿戴/持有类别子契约；只描述物品客观占用的身体槽位，不改变 15 个顶层字段。
- `properties.consumable`：类别无关的消耗品子契约；效果只作为所属领域待裁决候选。

所有物品统一保留数量、堆叠、单件重量、单件价值、状态、耐久、位置和 `isPlotItem`。任务道具价值由用户填写，尚未估值时保持 `null`。

耐久只属于非消耗的工具、服装和装备实例。崭新的小刀以 `100.0/100.0` 为标定基准，
模型根据名称、描述和“崭新、完整、生锈、磨损、破旧、损坏”等明确状态词提出相对上限
和剩余比例，程序再换算成 float 并检查类别上限。一次性消耗品不使用耐久；腐坏、受潮
和锈蚀时间演化已排除，行为损耗、维修或修理留待事件模块专题。

玩家的货币也遵循同一实例规则：只有放在玩家自有容器中的 `currency` 实例才是可用现金，`quantity * valueCrown` 只是在读取时派生的价值，不建立独立数值余额。当前图册没有来源可据的主角开局现金实例，因此不会凭空生成金额。

## 模块边界

剧情内容、证据意义、控制权、资料来源、事件审计和交易过程不属于物品基础字段，应由对应模块通过物品 `id` 引用。

## AI-物品参考缓存

`ai-items/` 是物品目录下的独立 AI 功能，不属于运行时物品状态。程序先按稳定
`itemKey`、名称和别名查询 `daily-item-references.json`；同一计量单位命中后同时
复用 `valueCrown` 与 `unitWeightGrams`，不会再次请求 AI。没有记录时只有设计期脚本
显式传入 `--allow-ai` 才能请求一次模型，同时估算当代美元零售价和单件克重。

模型不返回克朗价。程序以一个中等苹果 `10` 克朗为固定基准，根据美元价格比例
四舍五入得出正整数克朗；候选还必须通过字段、单位、价格、重量和置信度校验后才能
写表。计量单位必须明确，例如“一根带皮香蕉”和“一公斤香蕉”不能共享同一记录。
参考表只给未来普通物品定义补充原本为空的价格和重量，不覆盖人工值，也不代表任何
物品实例已经在世界中存在。

缓存命令示例：

```powershell
python scripts/cache_daily_item_reference.py --item-key orange_medium_each --name 橙子 --unit-description "一个中等大小、完整可购买的橙子"
```

上面的命令只查缓存；确认需要新增记录时才额外传入 `--allow-ai`。

## AI 日常物品定义生成

`daily-item-definitions.json` 只保存此前未定义的普通日常物品种类，不保存具体实例。
生成程序先查询正式 `important-items.json` 和本目录；已有定义或已缓存短语直接复用，
不调用 AI。确实缺失时，一次模型请求同时给出规范名称、客观描述、日常类别、堆叠、
明确计量单位、美元估价和克重，程序生成稳定定义 ID 并按苹果基准计算克朗。
新说法经一次归一化发现属于正式图册定义时，只缓存该说法到正式 `definitionId` 的映射，
不复制正式记录；后续相同说法也不再调用 AI。

目录中的每个 `item` 都必须通过当前正式物品定义校验。目录根部记录物品契约版本、
字段列表、契约指纹和生成策略指纹；以后物品字段新增、删除或改变语义时，必须同时
更新生成策略并迁移或重建旧目录，不能静默沿用旧结构。

当前生成器只接受 `food`、`drink`、`clothing`、`household`、`personal_care`、
`stationery`、`tool`、`material`、`container`，并强制 `isPlotItem=false`。
模型必须返回可空的 `equipment` 和 `consumable`，程序再写入 `properties`。AI 生成的
高/受限风险、重大效果和任何未要求领域裁决的消耗候选都会被拒绝；模型不能生成
通用用途、开锁权限、伤害判定或耐久规则。

```powershell
python scripts/generate_daily_item_definition.py --text "香喷喷的面包"
```

该命令默认只查询缓存。确认需要生成新定义时才传入 `--allow-ai`。当前功能不创建物品
实例，不判断来源，不确认玩家获得，也不提交库存或事件变化；这些属于后续独立设计。

## 装备属性子契约

需要穿戴或手持的物品在 `properties` 中声明：

```json
{
  "equipment": {
    "mode": "held",
    "slotIds": ["left_hand", "right_hand"],
    "handCount": 1
  }
}
```

`mode` 只能是 `held` 或 `worn`；`slotIds` 使用人物身体模块的稳定槽位 ID；
`held` 必须声明 `handCount` 为 1 或 2，`worn` 的 `handCount` 为 0。缺少
该子契约代表当前资料不足，运行时不会把物品自动当成装备。人物模块负责身体
伤势、槽位占用和最终装备事件，物品模块不写人物状态。`worn` 与 `held` 是
同一身体部位的独立层，例如手套可以占左右手的穿戴层，同时小刀占一只手的持有层。

## 通用消耗品属性子契约

可消耗性必须显式声明，不再从 `food` 或 `drink` 类别推断：

```json
{
  "consumable": {
    "schemaVersion": 1,
    "quantityPerUse": 1,
    "method": "burn",
    "targetKinds": ["location"],
    "riskClass": "moderate",
    "effectCandidates": [
      {
        "domain": "locations",
        "effectKind": "illumination",
        "summary": "燃烧时提供有限照明",
        "magnitude": "minor",
        "durationMinutes": 60,
        "requiresDomainResolution": true
      }
    ]
  }
}
```

该结构可以表示食物、饮品、燃料、清洁用品和其他一次使用后会减少的日常物品，
不为每个类别增加程序分支。物品层只允许 `item.consumed` 减少真实资源；候选效果
必须交给 `characters/items/locations/world` 中对应领域确认，不能直接修改状态。

## AI 配方判断

配方先按精确输入定义、数量和过程查询 `generated-recipes.json`。缓存缺失且显式允许
AI 时，模型只判断物理合理性与时代兼容性；程序会拒绝材料改写、低置信度、受限或
不存在技术、剧情/货币/文书材料、未知重量、非法产物和质量凭空增加。产物必须复用
日常物品定义生成链，已有定义零调用，缺失定义最多追加一次生成调用。

配方缓存不是库存变化。运行时仍要用具体实例重新验证所有权与数量，再生成材料消耗和
产物创建候选事件，由核心事务层原子提交。IT-03 通用使用方式和 IT-06 损耗/修理没有
在该功能中实现。
