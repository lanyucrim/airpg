"""Build the gray-harbor furniture atlas.

The default mode creates a conservative, reviewable seed from authored
structure names/purposes.  ``--ai`` uses the DeepSeek candidate adapter in
bounded batches; the returned JSON is still validated before this script
writes anything.  Neither mode creates runtime items or events.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps/server/src"))

from trpg_server.ai.platform.deepseek import DeepSeekSettings  # noqa: E402
from trpg_server.items.ai_items.furniture import (  # noqa: E402
    FurnitureStructureRequest,
    resolve_furniture_candidates,
)
from trpg_server.items.ai_items.deepseek_adapter import (  # noqa: E402
    DeepSeekFurnitureGenerationAdapter,
)
from trpg_server.locations.furniture import (  # noqa: E402
    FurnitureRecord,
    load_furniture_atlas,
)
from trpg_server.map.atlas import (  # noqa: E402
    GRAY_HARBOR_ATLAS_PATH,
    load_map_atlas,
)


OUTPUT = ROOT / "content/campaigns/gray-harbor/furniture-atlas.json"
MARKDOWN_OUTPUT = ROOT / "content/campaigns/gray-harbor/furniture-atlas.md"


def _hidden_structure(node: Any) -> bool:
    extra = getattr(node, "model_extra", None) or {}
    tag = str(extra.get("access") or extra.get("visibility") or "").casefold()
    if tag in {"hidden", "secret", "concealed"}:
        return True
    text = f"{getattr(node, 'name', '')} {getattr(node, 'purpose', '')}"
    return any(term in text for term in ("密道", "秘密通道", "隐蔽通道", "排水通道"))


def _templates(text: str) -> tuple[tuple[str, str, int, int], ...]:
    value = text.lower()
    if any(token in value for token in ("酒", "吧", "饮", "歌厅", "赌场", "牌桌")):
        return (
            ("bar_counter", "底部带柜门的吧台，收纳杯具、酒具与备用物料", 80000, 180000),
            ("bottle_cabinet", "带分隔格的酒瓶柜，避免玻璃瓶在搬动时互相碰撞", 70000, 210000),
            ("serving_sideboard", "带抽屉和下柜的服务边柜，收放餐巾、账单和小型器具", 65000, 160000),
        )
    if any(token in value for token in ("厨房", "备餐", "餐厅", "食堂", "面包", "烘焙", "茶点")):
        return (
            ("under_counter_cabinet", "操作台下的带门柜，存放锅具、餐盘和不立即使用的食材", 70000, 210000),
            ("cupboard", "存放餐具和厨房杂物的下柜", 50000, 180000),
            ("pantry", "存放干货和未即时使用食材的储藏柜", 65000, 240000),
        )
    if any(token in value for token in ("卧室", "宿舍", "住处", "住宅", "公寓", "寝室", "房间")):
        return (
            ("wardrobe", "存放衣物和个人用品的衣柜", 80000, 420000),
            ("bedside_table", "放置灯具、书籍和随身小物的床头桌", 18000, 45000),
            ("chest", "存放折叠衣物和私人物品的箱子", 50000, 180000),
        )
    if any(token in value for token in ("医院", "药房", "药品", "诊所", "治疗", "病房", "医务")):
        return (
            ("medicine_cabinet", "按类别存放药品和医疗耗材的药柜", 45000, 180000),
            ("equipment_cabinet", "存放可重复使用医疗器具的设备柜", 60000, 160000),
            ("apothecary_counter", "带下柜的配药台，收纳量杯、纸包和登记用具", 90000, 180000),
        )
    if any(token in value for token in ("账", "办公室", "登记", "法院", "银行", "市政", "报社", "事务所", "控制室", "档案")):
        return (
            ("drawer_desk", "带锁抽屉的书桌，收纳印章、笔墨和正在处理的文件", 40000, 105000),
            ("document_cabinet", "窄格文件柜，按日期和事项存放账册、卷宗或登记簿", 90000, 280000),
            ("lockbox", "可上锁的小型铁皮箱，适合暂存钥匙、印鉴和敏感小物", 30000, 55000),
        )
    if any(token in value for token in ("工厂", "机修", "车间", "钢", "煤", "仓", "码头", "货场", "军械", "修补", "工棚", "采石")):
        return (
            ("tool_chest", "带提手和分层托盘的工具箱，集中收纳扳手、钳子和小零件", 120000, 220000),
            ("parts_cabinet", "许多浅抽屉组成的零件柜，按规格分放螺栓、垫片和备件", 180000, 420000),
            ("material_bin", "加固木箱式料斗，存放煤料、木料或未加工的周转材料", 250000, 800000),
        )
    if any(token in value for token in ("学校", "课堂", "夜校", "图书馆", "教室")):
        return (
            ("drawer_desk", "带抽屉的课桌，收放练习簿、粉笔和个人文具", 30000, 100000),
            ("bookcase", "带背板和分层格口的书柜，存放教材、讲义和参考书", 100000, 500000),
            ("utility_cabinet", "带锁教具柜，存放地图、模型和公共教学用品", 80000, 240000),
        )
    if any(token in value for token in ("澡堂", "浴室", "洗衣", "盥洗")):
        return (
            ("locker", "带编号门扇的寄存柜，分格存放外衣和个人物品", 60000, 220000),
            ("linen_cabinet", "高窄布草柜，收纳干净毛巾、床单和备用衣物", 80000, 300000),
            ("laundry_basket", "有提手的深筐，集中放置待清洗织物并便于搬运", 30000, 140000),
        )
    if any(token in value for token in ("教堂", "教会", "礼拜", "墓园", "祈祷")):
        return (
            ("donation_chest", "带窄投入口的捐献箱，底部有锁定的取物门", 50000, 150000),
            ("vestment_cabinet", "高柜式礼仪用品柜，收纳布件、蜡烛和清洁物资", 70000, 260000),
            ("archive_cabinet", "木制档案柜，保存礼簿、墓园记录和往来文书", 90000, 300000),
        )
    if any(token in value for token in ("后院", "庭院", "公园", "市场", "户外", "花园", "烧烤")):
        return (
            ("grill", "带下层储物格的铁制烧烤架，收放木炭、烤网和夹具", 90000, 170000),
            ("weatherproof_cabinet", "覆有防雨油布的户外柜，存放摊位和园务用具", 100000, 350000),
            ("wood_bin", "厚木板拼成的燃料箱，存放木柴、木炭或园艺杂物", 100000, 300000),
        )
    if any(token in value for token in ("门厅", "门廊", "入口", "前厅", "走廊", "接待")):
        return (
            ("coat_cabinet", "带挂钩和下层柜格的衣帽柜，收放外衣、帽子与雨具", 70000, 260000),
            ("key_drawer", "带编号小抽屉的钥匙柜，集中保管房门和储物锁钥匙", 25000, 60000),
            ("parcel_cabinet", "带标签格口的收发柜，暂存信件、包裹和来客物品", 80000, 240000),
        )
    return (
        ("utility_cabinet", "带柜门和可调隔板的杂用柜，收纳本结构的日常用品", 70000, 240000),
        ("wall_cabinet", "固定在墙面的浅柜，适合存放常用小物与备用材料", 50000, 160000),
        ("drawer_chest", "带多个抽屉的收纳柜，将零散物品按用途分开保存", 60000, 190000),
    )


_KIND_NAMES = {
    "bar_counter": "吧台",
    "bottle_cabinet": "酒瓶柜",
    "serving_sideboard": "服务边柜",
    "under_counter_cabinet": "下柜",
    "cupboard": "橱柜",
    "pantry": "储藏柜",
    "wardrobe": "衣柜",
    "bedside_table": "床头桌",
    "drawer_chest": "抽屉柜",
    "medicine_cabinet": "药柜",
    "instrument_cabinet": "器械柜",
    "apothecary_counter": "配药台",
    "drawer_desk": "抽屉书桌",
    "document_cabinet": "文件柜",
    "lockbox": "锁箱",
    "tool_chest": "工具箱",
    "parts_cabinet": "零件柜",
    "material_bin": "料斗箱",
    "bookcase": "书架",
    "utility_cabinet": "杂用柜",
    "wall_cabinet": "壁柜",
    "locker": "寄存柜",
    "linen_cabinet": "布草柜",
    "donation_chest": "捐献箱",
    "vestment_cabinet": "礼仪用品柜",
    "archive_cabinet": "档案柜",
    "grill": "烧烤架",
    "weatherproof_cabinet": "防雨户外柜",
    "wood_bin": "燃料箱",
    "coat_cabinet": "衣帽柜",
    "key_drawer": "钥匙柜",
    "parcel_cabinet": "收发柜",
    "laundry_basket": "洗衣筐",
    "waste_bin": "杂物桶",
}


def _rich_description(
    location_name: str,
    structure_name: str,
    purpose: str,
    kind: str,
    base: str,
    setting_detail: str = "",
) -> str:
    context = f"{location_name}{structure_name}{purpose}"
    if any(token in context for token in ("港", "码头", "仓", "铁路", "钢厂", "煤")):
        material = "铆着铁角、刷过防潮漆的"
    elif any(token in context for token in ("医院", "药", "诊所")):
        material = "便于擦拭的浅色木制"
    elif any(token in context for token in ("银行", "法院", "市政", "事务所", "俱乐部")):
        material = "深色硬木配黄铜把手的"
    elif any(token in context for token in ("教堂", "教会", "墓园")):
        material = "旧木板拼接、边角磨圆的"
    elif any(token in context for token in ("工厂", "机修", "车间", "军械")):
        material = "厚木板和铸铁件加固的"
    else:
        material = "适合灰港潮湿空气的深色木制"
    detail = setting_detail.strip().rstrip("。")
    detail_sentence = f"剧本将这里描述为“{detail[:54]}”，所以家具表面留有相应的使用痕迹。" if detail else ""
    return (
        f"{material}{_KIND_NAMES.get(kind, kind)}，{base}。"
        f"它位于{structure_name}，与该处的{purpose or '日常活动'}相配，"
        f"{detail_sentence}内部空间用于收纳物品而非装饰。"
    )


def _seed_records() -> tuple[FurnitureRecord, ...]:
    atlas = load_map_atlas(GRAY_HARBOR_ATLAS_PATH)
    records: list[FurnitureRecord] = []
    for location in atlas.locations:
        if location.kind in {"city", "street", "district"}:
            continue
        for node in location.structure:
            templates = _templates(f"{location.name} {node.name} {node.purpose}")
            for index, (kind, description, weight, volume) in enumerate(templates, start=1):
                records.append(
                    FurnitureRecord(
                        furniture_id=f"furniture_{node.id}_{index}",
                        location_id=location.id,
                        structure_id=node.id,
                        kind=kind,
                        name=_KIND_NAMES.get(kind, kind),
                        description=_rich_description(
                            location.name,
                            node.name,
                            node.purpose,
                            kind,
                            description,
                            location.surface or location.resources or location.canon_notes,
                        ),
                        capacity_weight_grams=weight,
                        capacity_volume_cm3=volume,
                        visible=not _hidden_structure(node),
                        source_status="program_seeded",
                        confidence=0.72,
                        basis=("结构名称与用途", "灰港地点图册",),
                        source_refs=("atlas/location-atlas.json",),
                    )
                )
    return tuple(records)


def _ai_records() -> tuple[FurnitureRecord, ...]:
    atlas = load_map_atlas(GRAY_HARBOR_ATLAS_PATH)
    settings = DeepSeekSettings.from_environment()
    adapter = DeepSeekFurnitureGenerationAdapter(settings)
    records: list[FurnitureRecord] = []
    structures = [
        FurnitureStructureRequest(
            structure_id=node.id,
            location_id=location.id,
            location_name=location.name,
            structure_name=node.name,
            purpose=node.purpose,
            canon_notes=location.canon_notes,
        )
        for location in atlas.locations
        if location.kind not in {"city", "street", "district"}
        for node in location.structure
    ]
    for start in range(0, len(structures), 24):
        batch = tuple(structures[start : start + 24])
        candidates = resolve_furniture_candidates(adapter, batch)
        locations = {value.structure_id: value.location_id for value in batch}
        for index_by_structure, candidate in enumerate(candidates):
            ordinal = sum(value.structure_id == candidate.structure_id for value in candidates[:index_by_structure]) + 1
            records.append(
                FurnitureRecord(
                    furniture_id=f"furniture_{candidate.structure_id}_{ordinal}",
                    location_id=locations[candidate.structure_id],
                    structure_id=candidate.structure_id,
                    kind=candidate.kind,
                    name=candidate.name,
                    description=candidate.description,
                    capacity_weight_grams=candidate.capacity_weight_grams,
                    capacity_volume_cm3=candidate.capacity_volume_cm3,
                    visible=not _hidden_structure(
                        next(node for location in atlas.locations for node in location.structure if node.id == candidate.structure_id)
                    ),
                    source_status="model_generated",
                    confidence=candidate.confidence,
                    basis=candidate.basis,
                    source_refs=("atlas/location-atlas.json",),
                    model_audit={"provider": adapter.provider_name, "model": adapter.model_name},
                )
            )
    return tuple(records)


def write_atlas(records: tuple[FurnitureRecord, ...]) -> None:
    map_atlas = load_map_atlas(GRAY_HARBOR_ATLAS_PATH)
    from trpg_server.locations.furniture import FurnitureAtlas, FURNITURE_ATLAS_ID

    atlas = FurnitureAtlas(FURNITURE_ATLAS_ID, map_atlas.atlas_id, records)
    atlas.validate(map_atlas)
    payload = {
        "schemaVersion": 1,
        "atlasId": FURNITURE_ATLAS_ID,
        "locationAtlasId": map_atlas.atlas_id,
        "source": {
            "document": "灰港_黑潮王座_V4.2_AI_GM主线状态机与支线条件版.md",
            "locationAtlas": "atlas/location-atlas.json",
            "policy": "每个真实内部结构生成 1-3 个固定家具容器；街道、区域和城市不生成家具。",
        },
        "furniture": [record.to_mapping() for record in records],
    }
    OUTPUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    lines = [
        "# 灰港家具容器图册",
        "",
        f"结构家具记录：{len(records)} 条。每个内部结构固定生成 1-3 个家具容器。",
        "",
        "本资料由地点结构用途生成候选，运行时通过 `container.created` 事件物化；家具不是可携带物品。",
        "",
        "| 家具 ID | 结构 | 类型 | 名称 | 容量 | 来源 |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for record in records:
        lines.append(
            f"| `{record.furniture_id}` | `{record.structure_id}` | `{record.kind}` | {record.name} | {record.capacity_weight_grams}g / {record.capacity_volume_cm3}cm³ | {record.source_status} |"
        )
    MARKDOWN_OUTPUT.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ai", action="store_true", help="call DeepSeek in bounded batches")
    args = parser.parse_args()
    records = _ai_records() if args.ai else _seed_records()
    write_atlas(records)
    print(f"wrote {len(records)} furniture records to {OUTPUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
