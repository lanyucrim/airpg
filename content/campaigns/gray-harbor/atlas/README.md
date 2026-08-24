# 灰港地点图册目录

这个目录是 V4.2 剧本的地图与地点扩展层，和现有地点基础资料分开保存，便于编辑、审阅和重新生成。

## 文件

- `campaign-overview.md`：整部剧本的导航总览，包括九幕主线、六十个月时间轴、七大区域和 94 条支线索引。
- `campaign-overview.json`：上述总览的机器可读索引。
- `location-atlas.md`：按区域阅读的地图、地点结构、坐标和出行时间。
- `location-atlas.json`：96 个顶层地点（含剧本地点、基础资料地点、日常服务点和背景地点）；楼层、房间、厨房、地窖等全部嵌入所属地点的 `structure` 子对象，另有 45 条街道、街道连接和时间模型。街道是可通行的公共路由节点，但不是建筑地点，不生成结构，也不拥有功能区。

## 修改与生成

地图生成脚本是仓库根目录的 `scripts/build_gray_harbor_map.py`：

```text
python scripts/build_gray_harbor_map.py --check
python scripts/build_gray_harbor_map.py
```

脚本会重新读取剧本第五编和现有地点基础资料，因此不要直接手改生成文件后期待下次生成保留。应修改脚本中的 `REGIONS`、`ROUTES`、`LOCAL_STREETS`、`OFFSETS`、`base_coordinates` 或结构设计规则，再重新生成。

## 来源标记

- `source.status=canon`：来自剧本或现有地点基础资料的事实记录。
- `status=canon`（结构/街道）：剧本明确写出的内部结构或连接，例如白鹭屋地窖通往废面包房。
- `source.status=inferred`：为补足日常服务和居住背景而新增的设计层地点。
- `certainty=canon`：原文确认的结构；`certainty=atlas_design`：本图册确认存在的设计层结构。两者都不是“可能有”。
- `parentId` 与 `children`：地点层级。白鹭屋拥有厨房、各楼层、地窖和后院；这些子地点只存在于白鹭屋的 `structure` 中，不再作为顶层地点重复保存。

推定字段可以调整；不要把推定内容写入 `canonNotes`，也不要删除来源文档和行号。坐标是本项目的相对绝对坐标（单位 km），原点固定为白鹭屋，x 向东、y 向北，并不声称对应现实世界测绘坐标。

## 时间与路径计算

完整计算规则见 `location-atlas.md` 的“地点到地点的时间计算规则”。摘要如下：建筑出门到所属街道使用该建筑的 `streetPositionM`；街道上按相邻地点的 `streetPositionM` 差值移动；不同街道使用 `streetConnections` 的最短路径；最后按步行 4.5 km/h 或马车 10 km/h 换算。街道没有内部结构时间。坐标直线距离只用于校验。拥挤、天气、夜间封锁、找人、购票、装卸和等待不包含在纯移动时间内，应作为独立事件耗时结算。
