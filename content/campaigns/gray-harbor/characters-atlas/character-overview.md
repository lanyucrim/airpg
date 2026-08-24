# 灰港人物图册总览

- V4.2 Canon 人物：**139**
- 运行时人物资料：**142**（另含玩家 1 名、开局补充人物 2 名）
- 背景人物模板：**9**
- 能力词表：**53** 种
- 玩家角色：`protagonist`（艾拉·帕克）
- 战役初始时间：海历621年10月17日23:00
- 地点来源：`../atlas/location-atlas.json`

## 资料边界

本目录是人物资料层。`canon`、`inferred`、`unknown`、`template` 四种状态必须区分。人物住处、物品、状态、能力和行动候选都不能覆盖原始剧本事实。能力只是带来源的上下文标签，不直接决定行动成败。

`character-inventories.json` 只保存人物容器和物品实例引用；物品的名称、数量、状态、价值和功能只能从 `../items.json` 与 `../items-atlas/` 查询，人物图册不复制这些权威数据。

日常行动由 AI 提出候选，程序只校验地点、时间、权限、移动耗时、状态和剧情边界。剧情覆盖优先于日常候选，但仍需事件确认。

## 文件索引

| 文件 | 内容 |
|---|---|
| `character-profiles.json/md` | 全部人物基础档案、能力、语言风格、来源、住处和地点引用 |
| `character-states.json/md` | 基线、战役开始状态和每日状态日志结构 |
| `character-inventories.json/md` | 每个人物独立背包、容器和权威物品实例引用 |
| `relationship-atlas.json/md` | 有向关系、六轴、-100..100好感度和发展钩子 |
| `character-routines.json/md` | AI驱动行动候选与剧情覆盖接口 |
| `background-character-templates.json/md` | 可后续实例化的背景人物模板 |
| `character-abilities.json` | 53 种能力的稳定 ID、领域和定义 |
