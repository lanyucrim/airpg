# 灰港物品字段规范

本规范是灰港物品资料的正式字段方案。普通物品、功能物品、剧情道具和货币物品统一使用 15 个字段。

## 设计边界

物品记录只保存：

1. 物品本身客观具有的属性；
2. 程序为了确定物品当前放在哪里而必须保存的状态；
3. 一个简单的剧情道具判断。

剧情内容、证据意义、控制权、法律承认、人物认知、资料来源和事件审计不属于物品基础属性，不放入物品对象。它们由对应模块通过物品 `id` 引用。

不再使用 `fieldUsage`。未知或不适用的值使用 `null` 或 `{}`，不得为了填满字段而推定事实。

## 统一字段

| 字段 | 类型 | 含义 |
|---|---|---|
| `id` | string | 世界中这件物品或这一堆物品的唯一 ID；在定义表中表示定义 ID。 |
| `definitionId` | string | 物品种类 ID；定义记录与自身 `id` 相同，实例回指对应定义。 |
| `name` | string | 物品名称。 |
| `description` | string | 外形、材质和其他可观察特征；剧本未说明时明确写“未说明”。 |
| `category` | string | 物品大类，例如 `food`、`tool`、`document`、`currency`。 |
| `isPlotItem` | boolean | 是否为剧情道具；只判断是或否，不保存剧情内容。 |
| `quantity` | integer | 当前数量；不可堆叠物品固定为 1。 |
| `stackable` | boolean | 是否可以与同 `definitionId` 且状态一致的物品合并。 |
| `unitWeightGrams` | integer/null | 单件重量，单位克；未知时为 `null`。 |
| `valueCrown` | integer/null | 单件参考价值，单位克朗；尚未估值时为 `null`，不能用 0 表示未知。 |
| `condition` | string/null | 当前物理状态，例如 `intact`、`worn`、`damaged`、`broken`。定义记录或不适用时为 `null`。 |
| `durability` | object/null | 仅非消耗工具、服装和装备实例使用 `{current, max}`，两项均为 float；其他物品及所有定义为 `null`。 |
| `containerId` | string/null | 直接所在的背包、箱子、柜子或其他容器 ID。 |
| `locationId` | string/null | 未放入容器时，直接所在的地点或地点内部结构 ID。 |
| `properties` | object | 类别专属的少量客观属性；当前只允许 `volumeCm3`、`equipment`、`consumable`，没有时为 `{}`，不得写入剧情、证据、所有权或来源信息。 |

## 定义与实例

`definitions` 描述“这种物品是什么”，其中 `id` 与 `definitionId` 相同，`quantity` 表示一个标准单位，`containerId` 和 `locationId` 均为 `null`。

`instances` 描述世界中已经存在的具体物品，使用独立 `id`，并通过 `definitionId` 回指定义。定义不等于实例；只有定义而没有实例的物品，不应被判断为已经存在于世界中。

## 初始耐久

- 只有 `tool`、`clothing`、`equipment`，或带 `properties.equipment` 的非消耗实例使用耐久。
- 只要存在 `properties.consumable`，就按一次性消耗品处理，`durability` 必须为 `null`。
- 崭新的小刀固定为 `100.0/100.0` 标定基准；其他物品以材质、结构、名称和描述相对换算，并由程序限制上限。
- AI 只返回大类、标准状态、相对最大值、剩余比例、置信度和依据；程序计算最终 float 数值并校验，AI 输出不能直接写实例或事件。
- 定义记录不保存可变状态，所以即使定义属于工具或服装，`condition` 和 `durability` 仍为 `null`；创建具体实例时再确定初始值。
- 腐坏、受潮和锈蚀时间演化已排除。行为损耗、维修或修理尚未设计；未来事件只能通过物品模块的耐久校验接口提交候选变化。

## 计算规则

```text
总重量 = quantity * unitWeightGrams
总参考价值 = quantity * valueCrown
```

总重量和总参考价值由程序计算，不重复保存。

当 `containerId` 有值时，物品地点通过容器推导，`locationId` 必须为 `null`。物品直接放在地点中时，`locationId` 有值，`containerId` 为 `null`。两者不能同时有值。

堆叠至少要求以下内容一致：

```text
definitionId + condition + durability + containerId/locationId + properties
```

`stackable=false` 的物品不能合并，且 `quantity` 固定为 1。

## 货币

货币使用相同的 15 个字段，不增加专用字段：

- `category` 固定为 `currency`；
- 四种面额使用不同的 `definitionId`；
- `valueCrown` 保存单枚或单张面值；
- `quantity` 保存同一面额的数量；
- `stackable` 为 `true`；
- 总值按 `quantity * valueCrown` 计算。

不同面额的 `definitionId` 不同，因此不能错误堆叠。现金是物品，银行账户余额不是物品。

## `properties` 子契约

- `volumeCm3`：非负整数，表示单件体积；未知时不写该键。
- `equipment`：严格包含 `mode`、`slotIds`、`handCount`。`held` 只使用左右手并声明
  1 或 2 手；`worn` 使用明确身体槽位且 `handCount=0`。
- `consumable`：严格包含 schema 版本、每次数量、方法、目标类别、风险和效果候选。
  `quantityPerUse` 当前固定为 1；每个效果必须写 `requiresDomainResolution=true`。

`consumable` 不限于食物或饮品。它只表示一次使用会消耗真实物品，以及可能需要哪些
领域继续裁决；效果摘要不能直接当作人物恢复、地点照明、物品变化或世界状态事实。
通用 `usages`、行为权限、配方、来源、耐久演化和审计信息都不放入 `properties`。

## 三类示例

### 普通物品

```json
{
  "id": "item_daily_bread_001",
  "definitionId": "daily_rye_bread",
  "name": "黑麦面包",
  "description": "一条用粗黑麦烤制的硬面包。",
  "category": "food",
  "isPlotItem": false,
  "quantity": 2,
  "stackable": true,
  "unitWeightGrams": 450,
  "valueCrown": 12,
  "condition": "intact",
  "durability": null,
  "containerId": "protagonist_inventory",
  "locationId": null,
  "properties": {
    "consumable": {
      "schemaVersion": 1,
      "quantityPerUse": 1,
      "method": "eat",
      "targetKinds": ["character"],
      "riskClass": "low",
      "effectCandidates": [
        {
          "domain": "characters",
          "effectKind": "nourishment",
          "summary": "作为普通食物缓解饥饿",
          "magnitude": "minor",
          "durationMinutes": null,
          "requiresDomainResolution": true
        }
      ]
    }
  }
}
```

### 剧情道具

```json
{
  "id": "item_white_heron_deed_001",
  "definitionId": "GH-S01",
  "name": "白鹭屋总房契",
  "description": "剧本未说明该物品的外形、材质与尺寸。",
  "category": "document",
  "isPlotItem": true,
  "quantity": 1,
  "stackable": false,
  "unitWeightGrams": null,
  "valueCrown": null,
  "condition": "intact",
  "durability": null,
  "containerId": null,
  "locationId": "loc_5_5_12",
  "properties": {}
}
```

剧情内容、法律意义和证据用途不写进这个物品对象。

### 货币

```json
{
  "id": "item_silver_shield_stack_001",
  "definitionId": "currency_silver_shield",
  "name": "银盾",
  "description": "王国克朗体系的第二级面额。",
  "category": "currency",
  "isPlotItem": false,
  "quantity": 3,
  "stackable": true,
  "unitWeightGrams": null,
  "valueCrown": 1000,
  "condition": "intact",
  "durability": null,
  "containerId": "protagonist_inventory",
  "locationId": null,
  "properties": {}
}
```

总值为 `3,000` 克朗。这个堆叠实例本身才是角色拥有的现金；系统不另存一个数值余额，也不在此处定义支付或找零。
