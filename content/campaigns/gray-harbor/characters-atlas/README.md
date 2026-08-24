# 灰港人物图册

本目录整理 V4.2 人物、玩家角色、能力、语言风格、关系、背包、每日状态、住处和 AI 日常行动候选。

## 来源和边界

- `v4.2-catalog.json` 和 V4.2 原稿是 Canon 来源。
- `../atlas/location-atlas.json` 是地点、街道、结构和移动耗时的唯一引用来源。
- `character-inventories.json` 只保存人物容器归属和物品实例/定义引用；`../items.json` 与 `../items-atlas/` 是物品字段的唯一来源。
- 本目录不修改运行时剧本包，不把推定资料自动写入世界状态。
- 能力和语言风格是带来源的上下文资料；`inferred` 不能冒充 `canon`。
- 玩家角色的能力和语言风格由玩家定义，生成器不替玩家定型。

## 重新生成

```text
python scripts/build_gray_harbor_characters.py
```

生成器会重新读取当前人物、物品、容器、关系、日程和地点图册，输出本目录的 JSON 与 Markdown。
