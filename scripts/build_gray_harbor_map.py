"""Build the editable Gray Harbor location atlas from the V4.2 source.

The source document remains authoritative for names and the four descriptive
fields. Geometry, building sub-areas, street links, and travel estimates are
explicitly marked as inferred so they can be revised without changing Canon.
"""
from __future__ import annotations

import argparse
import heapq
import json
import math
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "灰港_黑潮王座_V4.2_AI_GM主线状态机与支线条件版.md"
OUT_DIR = ROOT / "content" / "campaigns" / "gray-harbor" / "atlas"

# The atlas separates public streets from places on those streets.  A street
# record is a routing surface, not a building with rooms or a player-facing
# activity area.
STREET_POSITION_STEP_M = 600
LOCAL_DISTANCE_SCALE = 1.5

REGIONS = {
    "5.1": {"id": "candle_ward", "name": "烛巷区", "anchor": [0.0, 0.0], "note": "主角开局区域；白鹭屋为原点"},
    "5.2": {"id": "old_harbor", "name": "老港", "anchor": [1.8, -1.1], "note": "东南侧海港与海关带"},
    "5.3": {"id": "iron_bay", "name": "铁湾", "anchor": [3.6, -2.0], "note": "老港以东的工业与铁路带"},
    "5.4": {"id": "black_slope", "name": "黑坡", "anchor": [-1.9, -2.7], "note": "烛巷西南坡地贫民区"},
    "5.5": {"id": "golden_bell", "name": "金钟", "anchor": [2.6, 2.1], "note": "市中心金融、商业与市政区"},
    "5.6": {"id": "saint_bridge", "name": "圣桥", "anchor": [0.6, 3.7], "note": "教会、医院、学校与改革机构集中区"},
    "5.7": {"id": "white_cliff", "name": "白崖", "anchor": [5.0, 4.5], "note": "海崖上层住宅与外交区"},
}

# 100 m grid offsets. The first candle-ward entries are hand-anchored because
# the text gives street numbers and a direct underground connection.
OFFSETS = [
    [-0.02, 0.00], [0.18, 0.12], [0.35, -0.08], [0.52, 0.16],
    [0.08, 0.42], [-0.18, 0.38], [0.40, 0.48], [-0.40, 0.10],
    [-0.38, -0.18], [0.02, -0.42], [0.30, -0.38], [-0.18, -0.48],
]

ROUTES = [
    ("candle_ward", "old_harbor", "潮汐大道", 1.9),
    ("old_harbor", "iron_bay", "煤轨工业路", 2.1),
    ("candle_ward", "black_slope", "南井坡道", 2.0),
    ("candle_ward", "golden_bell", "栎木—金钟大道", 2.7),
    ("candle_ward", "saint_bridge", "圣桥上行路", 3.8),
    ("golden_bell", "saint_bridge", "钟桥大道", 2.1),
    ("golden_bell", "white_cliff", "白崖海景路", 3.0),
    ("saint_bridge", "white_cliff", "北门海崖路", 4.0),
]

LOCAL_STREETS = {
    "candle_ward": [
        ("candle_oak", "栎木街"), ("candle_organ", "风琴巷"), ("candle_lantern", "红灯街"),
        ("candle_candle_lane", "圣烛巷"), ("candle_back_lane", "烛巷后街"),
    ],
    "old_harbor": [
        ("harbor_fishbone", "鱼骨街"), ("harbor_customs", "海关大道"), ("harbor_wharf", "七码头路"),
        ("harbor_sailor", "外国水手街"), ("harbor_lighthouse", "灯塔堤路"),
    ],
    "iron_bay": [
        ("iron_coalrail", "煤轨大道"), ("iron_furnace", "黑炉路"), ("iron_union", "工会街"),
        ("iron_machine", "机修巷"), ("iron_workers", "女工宿舍街"),
    ],
    "black_slope": [
        ("slope_southwell", "南井坡道"), ("slope_main", "黑坡主街"), ("slope_coal", "煤场路"),
        ("slope_northbank", "北岸巷"), ("slope_cemetery", "墓园路"),
    ],
    "golden_bell": [
        ("bell_main", "栎木—金钟大道"), ("bell_square", "金钟广场街"), ("bell_bank", "银行街"),
        ("bell_court", "法院街"), ("bell_post", "邮电巷"),
    ],
    "saint_bridge": [
        ("bridge_rise", "圣桥上行路"), ("bridge_church", "教堂街"), ("bridge_hospital", "医院路"),
        ("bridge_apartment", "中产公寓街"),
        ("bridge_school", "学校街"), ("bridge_park", "公园环路"),
    ],
    "white_cliff": [
        ("cliff_seaview", "白崖海景路"), ("cliff_consulate", "外国领事街"), ("cliff_servants", "白崖女佣宿舍街"), ("cliff_race", "赛马会路"),
        ("cliff_wind", "海风路"), ("cliff_garden", "海崖花园路"),
    ],
}

INFERRED_HOUSING = [
    ("housing_oak_back", "栎木街后排出租屋", "candle_ward", "栎木街后排的低租金合住房；目前不承担主线事件。", [0.08, -0.18]),
    ("housing_organ_court", "风琴巷三户公寓", "candle_ward", "围绕小院的三户家庭住宅；目前不承担主线事件。", [-0.24, 0.25]),
    ("housing_harbor_boarding", "老港海员短租屋", "old_harbor", "按周出租床位的海员宿舍；目前不承担主线事件。", [1.90, -1.42]),
    ("housing_iron_boarding", "铁湾煤轨工棚", "iron_bay", "靠近铁路货场的工人临时宿舍；目前不承担主线事件。", [3.80, -2.30]),
    ("housing_slope_courtyard", "南井合租院", "black_slope", "围绕公共水泵的多户合租院；目前不承担主线事件。", [-1.92, -2.40]),
    ("housing_bell_clerk", "金钟职员公寓", "golden_bell", "面向银行和市政文员的窄身公寓；目前不承担主线事件。", [2.30, 1.82]),
    ("housing_bridge_students", "圣桥学生宿舍", "saint_bridge", "师范学校附近的学生宿舍；目前不承担主线事件。", [0.90, 3.46]),
    ("housing_cliff_servants", "白崖佣人小屋", "white_cliff", "豪宅区边缘的仆役居住带；目前不承担主线事件。", [4.55, 4.20]),
]

EMBEDDED_BASE_SUBLOCATIONS = {
    "white_heron_ground_floor", "white_heron_kitchen", "white_heron_second_floor",
    "white_heron_third_floor", "white_heron_cellar", "white_heron_backyard",
}

INFERRED_DAILY = [
    ("daily_candle_newsstand", "烛巷报刊摊", "candle_ward", "街角报刊、烟草与便笺的小摊；用于填补烛巷主街的日常服务。", [0.28, -0.30]),
    ("daily_candle_grocer", "栎木街杂货铺", "candle_ward", "面包、煤油、肥皂和罐头的社区杂货铺。", [-0.28, -0.28]),
    ("daily_harbor_rope_shop", "老港绳具铺", "old_harbor", "船绳、帆布、灯油和小型修补用品。", [1.55, -0.95]),
    ("daily_iron_repair", "铁湾修补铺", "iron_bay", "工人靴、工具和锅炉小件维修。", [3.35, -1.78]),
    ("daily_black_slope_water_pump", "南井公共水泵", "black_slope", "居民取水与交换消息的公共设施。", [-1.72, -2.58]),
    ("daily_golden_bell_newsstand", "金钟报刊亭", "golden_bell", "金融报纸、公告和电报摘要的街边摊位。", [2.82, 1.95]),
    ("daily_saint_bridge_laundry", "圣桥洗衣店", "saint_bridge", "面向学生、护士和小公务员的洗衣与熨烫店。", [0.42, 3.55]),
    ("daily_white_cliff_carriage", "白崖车夫站", "white_cliff", "豪宅区的预约马车与行李寄存点。", [4.78, 4.28]),
]

def clean(value: str) -> str:
    return re.sub(r"\s+", " ", value.replace("  ", " ")).strip(" 。")

def parse_source() -> list[dict]:
    lines = SOURCE.read_text(encoding="utf-8").splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith("# 第五编"))
    end = next((i for i, line in enumerate(lines[start + 1:], start + 1) if line.startswith("# 第六编")), len(lines))
    result: list[dict] = []
    pattern = re.compile(r"^### (5\.\d+\.\d+) (.+)$")
    for index in range(start, end):
        match = pattern.match(lines[index])
        if not match:
            continue
        chapter, name = match.groups()
        fields = {"surface": "", "people": "", "resources": "", "risk": ""}
        labels = {"表面功能": "surface", "常见人物": "people", "主要资源": "resources", "持续风险": "risk"}
        for line in lines[index + 1:index + 20]:
            if line.startswith("### "):
                break
            for label, key in labels.items():
                found = re.match(rf"^\*\*{label}\*\*：(.*)$", line)
                if found:
                    fields[key] = clean(found.group(1))
        result.append({
            "id": f"loc_{chapter.replace('.', '_')}",
            "chapter": chapter,
            "name": name.strip(),
            "source": {"status": "canon", "document": SOURCE.name, "line": index + 1},
            **fields,
        })
    if len(result) != 84:
        raise RuntimeError(f"Expected 84 locations from Fifth Part, found {len(result)}")
    return result

def kind_for(surface: str, name: str) -> str:
    if "公园" in name or "花园" in name or "墓园" in name or "采石场" in name or "市场" in name:
        return "open_space"
    if "码头" in name or "仓" in name or "货场" in name:
        return "logistics_site"
    if "宅" in name or "庄园" in name or "公寓" in name or "宿舍" in name:
        return "residence"
    return "building"


def is_street_record(name: str) -> bool:
    """Return whether a source heading names a street rather than a place."""
    return bool(re.search(r"(?:街|巷|大道|主街|坡道|堤路|环路|路)$", name))


# The source gives each place a function, regular visitors, resources and
# risks, but it does not prescribe a room list.  These are therefore explicit
# atlas-design records derived from those four fields (and the V4.2
# ``specialStructure`` notes), rather than a keyword-based room template.
# Keeping the catalogue keyed by the authored place name makes every design
# auditable and prevents a new place from silently receiving a misleading
# "主要使用区" fallback.
def _design(*entries: tuple[str, str] | tuple[str, str, str]) -> tuple[dict[str, str], ...]:
    return tuple(
        {
            "name": entry[0],
            "purpose": entry[1],
            **({"access": entry[2]} if len(entry) == 3 else {}),
        }
        for entry in entries
    )


STRUCTURE_DESIGNS: dict[str, tuple[dict[str, str], ...]] = {
    "蓝铃馆": _design(
        ("街面接待门厅", "接待来客、核验预约并分流工作人员"),
        ("客房走廊", "连接对外服务房间与公共盥洗处"),
        ("员工休息室", "员工交接、休息和领取班次物资"),
        ("地下独立账房", "保存营业账目和拒客名单的后台办公室"),
        ("地下安全室", "不接客的紧急避险和重要物品存放处", "private"),
    ),
    "黑猫赌场": _design(
        ("街面牌桌厅", "公开牌局、下注和夜间人流的核心空间"),
        ("二楼牌室", "较安静的高额牌局与熟客会面"),
        ("放贷人会面间", "核对借据、谈论债务和安排还款"),
        ("真假账房", "分别保管对外账本与实际现金流记录"),
        ("后门观察室", "从二楼小窗观察后门和来往人员"),
    ),
    "铁钩酒馆": _design(
        ("铁钩门廊", "从街道进入酒馆并接受熟客招呼"),
        ("旧帮会酒厅", "饮酒、交换街头消息和维持旧成员关系"),
        ("码头合照墙", "展示旧成员合照并辨认历史关系"),
        ("后院谈判桌", "处理帮派分歧、货运口角和临时会面"),
        ("酒水地窖", "储存酒水、煤炭和营业备用物资"),
    ),
    "月桂厅": _design(
        ("门廊衣帽间", "接待宾客、寄存外衣并安排入场"),
        ("高端沙龙", "公开社交、演讲和政商寒暄"),
        ("私人会客包厢", "进行预约制的小范围交谈"),
        ("茶点备餐室", "准备茶点并让侍者完成服务交接"),
        ("马车等候区", "散场后继续进行不在包厢内的关键谈话"),
    ),
    "夜莺歌厅": _design(
        ("售票门厅", "售票、验票和从街道分流观众"),
        ("歌舞厅", "歌舞演出、观众观看和公开社交"),
        ("乐队台", "乐师演奏、换场和保管演出器材"),
        ("后台化妆间", "艺人准备、换装和经理安排节目"),
        ("相邻店铺后台门", "连接两家相邻店铺的工作人员通路"),
    ),
    "旧面包房": _design(
        ("临街烘焙间", "废弃炉台和原有生产空间"),
        ("销售柜台", "面向街道的旧营业面"),
        ("后仓", "存放停业前遗留物资与文件"),
        ("楼上旧协会文件夹层", "墙体和夹层中保存旧协会文件"),
        ("地下排水连接口", "与白鹭屋地窖相连的隐蔽通道口（原文）", "hidden"),
    ),
    "栎木街市场": _design(
        ("栎木街摊位带", "白天食品杂货摊和夜间小贩的连续街面"),
        ("粮食杂货排", "集中展示食品、日用品和当日价格"),
        ("商户议价棚", "商户联盟讨论供应、摊位和保护费"),
        ("收摊记账台", "收摊前十五分钟交换消息并核对摊位账"),
        ("夜间小贩通道", "夜间进出、搬运和临时摆摊的侧向通路"),
    ),
    "圣烛小教堂": _design(
        ("教堂前廊", "迎接礼拜者、访客和受助居民"),
        ("小礼拜堂", "礼拜、谈话和公开布道"),
        ("救济厨房", "准备食物并向街区发放救济"),
        ("匿名求助簿室", "保存匿名求助记录并保护来访者隐私"),
        ("侧院与柴棚", "志愿者交接、储存燃料和短暂停留"),
    ),
    "灰兔旅馆": _design(
        ("旅馆登记柜台", "办理入住、收款和记录来客"),
        ("共用客房层", "外地客、临时枪手和推销员住宿"),
        ("早餐厅", "供应廉价餐食并观察住客往来"),
        ("洗衣杂物间", "清洗床品、存放行李和日常用品"),
        ("老板居室", "老板私下记录客人鞋泥、口音和行踪", "private"),
    ),
    "红砖澡堂": _design(
        ("澡堂换票门厅", "收取费用并分流洗浴与洗衣客人"),
        ("公共蒸汽浴室", "工人、性工作者和女佣洗浴"),
        ("女更衣区", "更衣、休息和交换家庭消息"),
        ("洗衣操作间", "浸洗、晾晒和熨烫衣物"),
        ("锅炉与布草房", "供应热水、煤料和清洁布草"),
    ),
    "一号码头": _design(
        ("深水泊位入口", "从港岸进入最老的深水泊位"),
        ("装卸栈桥", "装卸大宗货物并安排工班"),
        ("泊位调度台", "记录船期、靠泊顺序和钥匙交接"),
        ("海关交接棚", "在码头和海关之间核验货物"),
        ("潮位排水隧道口", "潮位低时可见的旧排水结构；当前按普通港区结构开放"),
    ),
    "海关总楼": _design(
        ("海关门厅", "接待报关人并分流行政业务"),
        ("报关柜台", "提交单据、缴费和办理通关手续"),
        ("货物查验场", "开箱查验、称重和记录扣押物"),
        ("扣押货场", "临时存放被扣货物并安排交接"),
        ("地下船单档案库", "保存十年船单并供授权人员检索"),
    ),
    "蓝盐仓": _design(
        ("仓门验收台", "登记进出货物、危险标识和钥匙"),
        ("化工货架区", "存放高价值化工品并保持分区"),
        ("药品隔离库", "单独保管药品和需要防火的货物"),
        ("仓库经理办公室", "安排工人、保险和出货批次"),
        ("西墙旧砖夹层", "早年夹层和被遮蔽的旧存放空间（隐蔽通道旁夹层）", "hidden"),
    ),
    "凯恩三号仓区": _design(
        ("仓区门岗", "核验车牌、货单和进出人员"),
        ("高架货架区", "现代化大宗仓储和分区保管"),
        ("装卸月台", "车辆靠台、吊装和货物交接"),
        ("仓库调度室", "维护比正式船单更早的调度表"),
        ("工会交接台", "处理工班、停工和货物责任交接"),
    ),
    "旧灯塔": _design(
        ("灯塔海岸入口", "从堤岸进入停用灯塔"),
        ("螺旋楼梯", "连接塔身各层并搬运小型物资"),
        ("旧灯具机房", "停用灯具、齿轮和维修痕迹"),
        ("港外观察层", "观察港外船只和海面动静"),
        ("旗语平台", "以旗语联系外海小船"),
    ),
    "圣玛丽海员医院": _design(
        ("海员医院接诊台", "登记伤员并分流急诊与普通病患"),
        ("廉价海员病房", "安置工伤者和需要持续观察的病人"),
        ("处置室", "清创、缝合和处理港口常见伤势"),
        ("修女药房", "发放药品并管理慈善库存"),
        ("医生工伤记录桌", "记录各公司工伤比例和真实伤亡"),
    ),
    "船员登记所": _design(
        ("船员登记门厅", "排队、领取号码并进入登记流程"),
        ("出海合同柜台", "办理雇佣、船期和工资登记"),
        ("船员档案架", "保存船员身份、航次和雇佣记录"),
        ("身份核验桌", "核对证件并识别重复使用的假名"),
        ("后门等候巷", "船代和工人在背面交接文件"),
    ),
    "第七码头咖啡馆": _design(
        ("码头咖啡柜台", "清晨点单、结账和短暂歇脚"),
        ("船代长桌", "保险经纪、船代和工人交换船期"),
        ("货运黑板墙", "记录半公开的货运粉笔标记"),
        ("港口后厨", "准备咖啡、早餐和便携食物"),
        ("靠码头侧门", "服务早班船员并快速进出"),
    ),
    "海门保险行": _design(
        ("保险行接待厅", "接收船东、货主和经纪人的询问"),
        ("航运核保桌", "评估船舶、航线和货物风险"),
        ("理赔档案室", "保存事故报告、合同和赔付记录"),
        ("现场照片阅览室", "核验可能成为案件证据的事故照片"),
        ("后部文书间", "整理副本、印章和待办赔案"),
    ),
    "煤栈码头": _design(
        ("煤栈门岗", "登记车辆、工班和煤炭来源"),
        ("煤炭装卸带", "装船、卸船和转运冬季煤炭"),
        ("煤堆场", "分区堆放煤炭并记录存量"),
        ("称重票据棚", "称重、开票和处理商户争议"),
        ("夜间火警观察台", "监视火星、脚印和夜间异常"),
    ),
    "联合工会大厅": _design(
        ("工会门厅", "接待工人、登记会员并分流会议"),
        ("代表大会厅", "举行工会会议、投票和派系协商"),
        ("互助基金桌", "办理罢工、救济和互助基金事务"),
        ("工伤名册地下室", "保存历次工伤名单和赔偿记录"),
        ("后勤厨房", "为长时间会议和罢工筹备提供食物"),
    ),
    "黑炉钢厂": _design(
        ("钢厂门岗", "核验班次、访客和工人安全装备"),
        ("炼铁炉台", "进行炼铁并承受高温生产风险"),
        ("轧钢生产线", "加工钢材、记录产量和换班"),
        ("设备维修坑", "检修炉体、传动件和起重设备"),
        ("夜班工头控制室", "掌握真实产量与设备故障", "private"),
    ),
    "东线铁路货场": _design(
        ("货场门房", "登记车次、货单和进出人员"),
        ("调车轨道区", "编组车厢并执行铁路调度"),
        ("铁路装卸台", "转运内陆货物和处理短装"),
        ("调度塔", "掌握变化的调车时刻和秘密货运", "private"),
        ("机车检修侧线", "临时停放和检查货运机车"),
    ),
    "灰烟机修厂": _design(
        ("机修厂收件台", "接收蒸汽机、吊机和维修委托"),
        ("蒸汽机修理间", "拆解、维修和试运行蒸汽设备"),
        ("吊机维修坑", "检修港口吊机和承重部件"),
        ("零件测量台", "通过磨损判断事故是否人为"),
        ("老师傅工作间", "保存经验、工具和待检零件"),
    ),
    "工人食堂“铁勺”": _design(
        ("铁勺取餐线", "供应廉价饭食并安排工人取餐"),
        ("工人公共餐厅", "工人家庭用餐和交换基层消息"),
        ("赊账板墙", "记录欠账并反映街区贫困程度"),
        ("大锅厨房", "烹饪、清洗和分配食材"),
        ("煤粮储放间", "存放煤料、粮食和备用餐具"),
    ),
    "第九消防站": _design(
        ("消防车库", "停放消防车并完成出勤集结"),
        ("水带晾晒塔", "晾晒、检查和维护消防水带"),
        ("值班休息室", "消防员轮班、休息和接警"),
        ("器材库", "存放呼吸器、斧具和救援装备"),
        ("消防长办公室", "处理预算、违规仓库和政治事务", "private"),
    ),
    "铁路浴室": _design(
        ("铁路浴室更衣厅", "按班次接待夜班工人"),
        ("工人洗浴厅", "洗浴、清洁煤尘和短暂休息"),
        ("蒸汽间", "提供热蒸汽并维持锅炉运行"),
        ("洗衣晾晒线", "处理工作服和工装清洗"),
        ("班次交接公告墙", "不同班次交接时交换消息"),
    ),
    "红炉拳赛场": _design(
        ("拳赛售票门", "验票、收款并分流赌客"),
        ("地下拳台", "进行拳赛、裁判和救护"),
        ("观众看台", "观看比赛、下注和寻找熟人"),
        ("赌注登记桌", "记录赌注和现金流"),
        ("拳手更衣室", "拳手准备、治疗和经纪人招募"),
    ),
    "机械夜校": _design(
        ("夜校门厅", "登记学生并安排晚间课程"),
        ("机械原理教室", "讲授机器、锅炉和维修基础"),
        ("制图工作室", "绘制零件图并练习工程读图"),
        ("工具图书室", "借用工具、教材和技术资料"),
        ("学生议事室", "年轻工人讨论改革和工会事务"),
    ),
    "旧军械库": _design(
        ("军械库门卫房", "核验进入人员和封存区域"),
        ("退役器材库", "存放退役武器、箱具和防护装备"),
        ("清点检验台", "核对库存清单与现实数量"),
        ("军械登记办公室", "保存调拨、封存和维修记录"),
        ("封闭后库", "存放尚未公开清点的危险物资", "private"),
    ),
    "煤气公司总站": _design(
        ("煤气总站接待厅", "处理用户、工程师和工单登记"),
        ("压力控制车间", "监测城市照明能源和管网压力"),
        ("地下管线图室", "保存城市地下设施战略资料", "private"),
        ("维修材料院", "存放管件、阀门和维修车辆"),
        ("夜班值守间", "夜间监测故障并应对停工"),
    ),
    "铁湾女工宿舍": _design(
        ("宿舍门厅", "女工出入、登记和领取生活用品"),
        ("女工合住房", "纺织与包装女工共同居住"),
        ("共用洗漱间", "洗漱、清洗工装和处理日常事务"),
        ("女工协会公告室", "发布班次、互助和选票消息"),
        ("宿舍管理员房", "管理床位、租金和跨行业女性联络", "private"),
    ),
    "南井社区": _design(
        ("南井公共井场", "居民取水、排队和交换街区消息"),
        ("坡地合住房巷", "矿工家庭和移民居民的日常出入口"),
        ("社区共用厨房", "居民共享燃料并准备低价食物"),
        ("租煤公告墙", "张贴房租、煤票和公共维修消息"),
        ("坡屋顶瞭望处", "观察煤场、街道和社区动静"),
    ),
    "乌鸦当铺": _design(
        ("当铺验货柜台", "评估物品、报价并办理当票"),
        ("抵押品陈列间", "展示可赎回或待售的抵押物"),
        ("当票账房", "通过当票追踪底层家庭经济轨迹"),
        ("修补工作台", "清洁、修理和重新估价抵押品"),
        ("后室保管库", "存放贵重抵押物和敏感账本", "private"),
    ),
    "黑坡诊所": _design(
        ("诊所候诊厅", "登记病人并等待医生分诊"),
        ("廉价诊室", "处理常见疾病、工伤和寒潮伤"),
        ("药品发放台", "按处方分配药品并记录短缺"),
        ("志愿者工作桌", "安排探访、救济和病人联络"),
        ("后部处置室", "处理需要隔离或持续观察的伤病"),
    ),
    "南井煤场": _design(
        ("煤场称重台", "称量进出煤炭并核对煤票"),
        ("冬煤堆场", "储存和分拣家庭冬季用煤"),
        ("煤票交易柜", "按煤票分配供应并控制欠账"),
        ("装车坡道", "为居民和商贩装运煤炭"),
        ("煤场看守棚", "夜间防火、防盗和监视纵火风险"),
    ),
    "圣桥坡脚学校": _design(
        ("学校门廊", "接送孩子并处理缺课登记"),
        ("穷人儿童教室", "提供基础教育和日常照护"),
        ("教师讲桌", "记录哪些家庭突然消失或欠租"),
        ("午餐分发室", "为学生提供简易餐食"),
        ("小操场", "课间活动、集合和邻里观察"),
    ),
    "红砖公寓群": _design(
        ("公寓内院门", "住户出入、收信和搬运生活物资"),
        ("共用楼梯间", "连接各层廉租房和公共设施"),
        ("家庭合住房", "数百家庭分散居住的房间组"),
        ("公用洗衣房", "清洗衣物、取水和交换邻里消息"),
        ("相连屋顶", "屋顶相通的追逐与观察路线"),
    ),
    "灰帽车站": _design(
        ("车站候车棚", "乘客等候出租马车和货车"),
        ("马车集散场", "安排车辆、车夫和城市移动"),
        ("货车装卸台", "接收货物并处理短途运输"),
        ("调度办公室", "记录车夫记忆中的时间、路线和乘客", "private"),
        ("马厩修理院", "保养车辆、马具和工作用马"),
    ),
    "旧采石场": _design(
        ("采石场边缘入口", "从坡地进入废弃采石坑"),
        ("废弃采石坑底", "街头少年和倾倒者活动的开阔地"),
        ("雨季积水洼", "积水冲出被埋物品和旧痕迹"),
        ("倾倒斜坡", "非法倾倒、藏匿和留下证据的区域"),
        ("碎石隐蔽角", "临时会面、藏物和观察来人的位置"),
    ),
    "北岸移民会馆": _design(
        ("会馆接待桌", "登记会员、翻译和求助事项"),
        ("多语会议厅", "移民家庭开会、互助和处理争端"),
        ("汇款办理台", "记录海外汇款和跨国关系"),
        ("共享厨房", "为新到居民提供食物和短暂住宿"),
        ("语言档案柜", "保存翻译、信件和社群记录"),
    ),
    "小圣人市场": _design(
        ("小圣人摊位排", "廉价食品和日常用品的摊位集合"),
        ("蔬菜称量台", "称量货物并比较当天食品价格"),
        ("卫生检查棚", "接受检查、处理腐坏货物和投诉"),
        ("妇女商贩遮棚", "商贩休息、议价和交换消息"),
        ("市场后巷", "进货、收摊和夜间搬运路线"),
    ),
    "黑坡墓园": _design(
        ("墓园铁门", "登记访客并进入贫民墓地"),
        ("贫民墓区", "排列无名墓和低价安葬位置"),
        ("墓园登记棚", "保存死亡记录和墓位登记"),
        ("小礼拜角", "举行简短葬礼和教会仪式"),
        ("掘墓工具棚", "存放工具并安排掘墓工作"),
    ),
    "退伍军人馆": _design(
        ("老兵纪念门厅", "接待退伍军人并展示旧物"),
        ("退伍互助会厅", "处理救济、工作和政治联系"),
        ("战术训练室", "练习武器技能和集体行动"),
        ("老兵档案桌", "保存服役、伤残和成员关系"),
        ("后院集合场", "集合、训练或发生反黑帮动员"),
    ),
    "西海银行总行": _design(
        ("银行营业大厅", "接待客户、存取款和办理公开业务"),
        ("柜台金库前室", "核验现金并完成日常清算"),
        ("信贷洽谈室", "评估贷款、抵押和信用"),
        ("二楼信贷档案室", "保存真正影响权力的信贷记录"),
        ("地下金库", "保管现金、票据和贵重抵押物", "private"),
    ),
    "格雷与合伙人事务所": _design(
        ("律师楼接待台", "接收委托并安排律师会面"),
        ("客户咨询室", "讨论合同、诉讼和法律策略"),
        ("年轻律师办公区", "起草文件、检索法规和准备案件"),
        ("隔离档案室", "按客户严格分隔文件和证据"),
        ("合伙人办公室", "处理重大客户和职业秘密", "private"),
    ),
    "《灰港纪事报》大楼": _design(
        ("报社前台", "接收投稿、线索和来访记者"),
        ("新闻编辑室", "采访、编辑和安排次日版面"),
        ("铅字排版间", "排版、校样和印前准备"),
        ("地下印刷机房", "运行印刷机并决定全城次日叙事"),
        ("照片暗房", "冲洗、保存和核验新闻照片"),
    ),
    "市政厅": _design(
        ("市政大厅", "接待市民并进行公开行政事务"),
        ("议会会议厅", "讨论预算、工程和公共政策"),
        ("许可证办公室", "办理影响小生意的执照和许可"),
        ("市政档案走廊", "保存决议、合同和工程文件"),
        ("市长办公室", "处理私人政治协商和城市权力", "private"),
    ),
    "王冠俱乐部": _design(
        ("王冠俱乐部门厅", "核验会员、寄存外衣并安排入场"),
        ("会员餐厅", "高端用餐和政商非正式协调"),
        ("派系包间", "不同房间连接不同服务员与信息源"),
        ("俱乐部图书酒吧", "阅读、饮酒和观察会员关系"),
        ("员工后勤通道", "服务员换班、送餐和传递消息"),
    ),
    "北桥信贷总店": _design(
        ("信贷门厅", "接待借款人并安排贷款咨询"),
        ("小额贷款柜台", "办理借贷、还款和抵押登记"),
        ("抵押品核验室", "评估资产并记录债权"),
        ("转让包后室", "保管能看出债务最终买家的文件", "private"),
        ("债权账房", "汇总账本、利息和逾期风险"),
    ),
    "金钟交易厅": _design(
        ("交易厅入口", "核验经纪、商人和合同来客"),
        ("商品竞价地板", "进行商品与航运合同交易"),
        ("价格黑板", "展示变化并反映城市未来一周情绪"),
        ("经纪人画廊", "观察商人、经纪和货物预期"),
        ("清算电报台", "确认成交、结算和交割通知"),
    ),
    "皇家法院分院": _design(
        ("法院公共门厅", "安检、登记和分流诉讼参与人"),
        ("审判法庭", "进行民刑事审理和宣判"),
        ("案件登记台", "接收诉状、排期和程序文件"),
        ("证物保管室", "保存案件证物和封存记录"),
        ("旁听席", "记者、家属和情报员交汇的公开区域"),
    ),
    "中央邮电局": _design(
        ("邮电营业厅", "办理电报、电话和公开邮务"),
        ("电报收发室", "接收、译码和发送电报"),
        ("电话交换台", "接通早期电话并记录故障"),
        ("邮件分拣间", "按线路分拣信件和包裹"),
        ("夜班技工室", "发现异常大量同一目的地电报", "private"),
    ),
    "金钟大酒店": _design(
        ("酒店大堂", "办理入住并观察谁与谁假装不认识"),
        ("酒店餐厅", "接待外地商人、权贵和外交客"),
        ("客房走廊", "连接客房、楼梯和公共盥洗处"),
        ("服务厨房", "准备餐食、管理行李和服务班次"),
        ("经理办公室", "处理安保、客人名单和特殊安排", "private"),
    ),
    "白狮餐厅": _design(
        ("白狮门厅", "迎宾、候位和安排政商客人"),
        ("固定桌餐厅", "固定桌位体现真实等级和社交关系"),
        ("开放式厨房", "准备午餐、酒水和餐后服务"),
        ("酒窖与备餐间", "存放酒水、餐具和备用食材"),
        ("后门收货处", "接收食材并处理供应商交接"),
    ),
    "商业登记处": _design(
        ("登记处门厅", "接待公司、房产和地契申请人"),
        ("公司登记柜台", "办理法人注册和变更"),
        ("地契档案室", "保存所有权和抵押文件"),
        ("文件核验桌", "检查印章、签名和伪造痕迹"),
        ("地下旧档案库", "保存地下合法化最终留下的记录"),
    ),
    "圣桥大教堂": _design(
        ("大教堂前阶", "迎接礼拜者、访客和慈善活动"),
        ("主教堂中殿", "礼拜、布道和公共仪式"),
        ("告解小室", "在宗教保护下进行私密告解", "private"),
        ("慈善登记办公室", "记录救济、捐款和受助人"),
        ("圣器室", "保管仪式用品和教会档案", "private"),
    ),
    "慈惠医院": _design(
        ("医院接诊大厅", "登记病人并分流急诊"),
        ("公共病房", "安置病人并记录公共卫生情况"),
        ("手术处置室", "处理重伤和枪伤"),
        ("药品配发室", "管理药品、床位和护士交接"),
        ("病历档案室", "统计枪伤和街区暴力变化", "private"),
    ),
    "女子改革联盟会所": _design(
        ("联盟接待室", "登记成员、访客和志愿者"),
        ("改革会议厅", "讨论禁娼、改良和公共议题"),
        ("竞选工作室", "组织宣传、志愿者和选票工作"),
        ("会议纪要档案室", "保存比公开声明更真实的派系记录"),
        ("茶室与休息廊", "非正式交流和内部调解"),
    ),
    "师范学校": _design(
        ("学校正门厅", "登记学生、访客和课程安排"),
        ("教育讲堂", "教授教学方法和公共课程"),
        ("实习教室", "进行课堂演练和教师培养"),
        ("教职员办公室", "安排课程、学生和学校事务"),
        ("学生宿舍", "学生居住和跨区信息交流", "private"),
    ),
    "公共图书馆": _design(
        ("图书馆入口台", "办理借阅、登记和访客咨询"),
        ("公共阅览厅", "学生、记者和市民阅读"),
        ("旧报纸室", "查阅二十年地产、案件和政治记录"),
        ("书库与目录台", "定位档案、书籍和借阅记录"),
        ("馆员办公室", "处理审查、保管和特殊借阅", "private"),
    ),
    "圣桥药房": _design(
        ("药房街面柜台", "接待顾客、配药和收款"),
        ("药剂调配台", "称量、混合和包装药品"),
        ("问诊小室", "询问症状并提供基础用药建议"),
        ("药品储藏室", "按种类保存药品并记录短缺"),
        ("后门处置室", "为旧铁钩成员处理伤口"),
    ),
    "儿童救济院": _design(
        ("救济院接待室", "登记儿童、志愿者和临时安置"),
        ("儿童起居宿舍", "提供安全居住和日常照护"),
        ("共用餐厨房", "准备救济餐食和分配物资"),
        ("儿童学习室", "观察儿童政策的实际效果"),
        ("修女办公室", "管理捐款、领养和志愿者", "private"),
    ),
    "检察署旧楼": _design(
        ("检察署接待台", "接收报案、咨询和案件材料"),
        ("检察官办公廊", "处理起诉、调查和日常文书"),
        ("证人询问室", "进行正式询问和证词记录"),
        ("分层证据档案室", "按权限保管调查证据", "private"),
        ("旧楼侧门", "工作人员、证人和文件的侧向出入口"),
    ),
    "警察训练学校": _design(
        ("训练学校门厅", "登记新警员和访客"),
        ("警务课堂", "教授法律、程序和改革教材"),
        ("操练院", "进行体能、队列和现场训练"),
        ("教官办公室", "安排课程并处理旧派抵制", "private"),
        ("警员宿舍", "新警员住宿和非正式交流", "private"),
    ),
    "圣桥公园": _design(
        ("公园东侧入口", "从街道进入公共绿地"),
        ("公共演讲草坪", "举行演讲、集会和政治活动"),
        ("游行环道", "安排队伍、群众和警戒路线"),
        ("公园乐台", "举办音乐、公告和临时集会"),
        ("林荫长椅区", "普通市民休息和观察来人"),
    ),
    "自由律师社": _design(
        ("律师社接待台", "登记求助者并安排法律咨询"),
        ("民权咨询室", "处理法律援助和权利问题"),
        ("共享办公区", "年轻律师协作、起草文件"),
        ("法律资料室", "保存案例、法规和援助材料"),
        ("盟友会议室", "组织对抗黑潮压力的非黑帮合作"),
    ),
    "凯恩宅邸": _design(
        ("凯恩宅邸门厅", "门房接待访客并安排入内"),
        ("家族客厅", "接待亲友、商界来客和家族成员"),
        ("航运家族餐厅", "通过餐桌座位体现家族权力"),
        ("家族书房", "处理航运账目、继承和私人权威", "private"),
        ("仆役服务翼", "厨房、洗衣和家政人员工作区", "private"),
    ),
    "福斯特宅邸": _design(
        ("福斯特宅邸门厅", "低调接待访客并控制出入"),
        ("安静起居室", "进行有限的私人金融社交"),
        ("家宴餐室", "家庭用餐和少量熟人会面"),
        ("家庭照片室", "保存比珠宝更能显示私人价值的照片"),
        ("银行家书房", "处理私密金融文件和家族事务", "private"),
    ),
    "白崖赛马会": _design(
        ("赛马会售票门", "验票、收取赌注并分流观众"),
        ("白崖看台", "观看比赛、社交和观察贵族商人"),
        ("马匹围场", "参赛马匹检录和赛前准备"),
        ("赌注登记台", "记录合法赛事和赌债"),
        ("看台包厢", "进行政治交易和私密会面", "private"),
    ),
    "海风俱乐部": _design(
        ("海风俱乐部门厅", "核验会员并安排访客"),
        ("海军关系休息室", "船东、军官和旧海军人士交流"),
        ("航运图书室", "查阅航线、舰船和海军资料"),
        ("会员餐厅", "处理航运、人脉和排外阶层社交"),
        ("理事办公室", "管理会员、会费和俱乐部政策", "private"),
    ),
    "白崖慈善厅": _design(
        ("慈善厅入口", "接待捐款人、改革者和受助者"),
        ("募款大厅", "举办大型募款、宴会和公开活动"),
        ("捐款登记台", "保存精英捐款名单和承诺"),
        ("慈善委员会室", "协调项目、名誉和改革议题"),
        ("物资储藏间", "存放救济物资和活动用品"),
    ),
    "格兰特市长宅": _design(
        ("市长宅邸门厅", "门房接待并控制访客进入"),
        ("家庭客厅", "政客、家人和熟人短暂会面"),
        ("早餐会餐室", "以早餐会进行非正式政治协调"),
        ("市长私人书房", "处理私人政治文件和谈判", "private"),
        ("家政服务翼", "厨房、仆役和家庭后勤", "private"),
    ),
    "布兰特庄园": _design(
        ("庄园门厅", "接待访客并安排家庭与地产事务"),
        ("建筑商会客厅", "讨论地产、工程和家族关系"),
        ("地产图纸室", "保存地契、建筑图纸和项目文件"),
        ("儿子艺术工作室", "处理与父亲价值观冲突的创作空间"),
        ("庄园后勤院", "车马、维修和家政人员出入"),
    ),
    "月影花园": _design(
        ("海崖花园入口", "从公共道路进入维护良好的花园"),
        ("月影主步道", "游客和上层家庭散步"),
        ("海崖观景台", "观海、接待访客和观察来人"),
        ("玫瑰花圃", "维护昂贵的园艺区和季节活动"),
        ("黄昏长椅区", "常见的秘密谈判和私下会面位置"),
    ),
    "皇家海景酒店": _design(
        ("皇家酒店大堂", "接待王室、中央官员和调查团"),
        ("海景客房层", "安排外交客、调查团和高级访客"),
        ("外交餐厅", "进行正式宴请和中央权力交接"),
        ("服务后场", "管理行李、餐食和员工班次"),
        ("安保办公室", "控制访客名单和严密安保", "private"),
    ),
    "旧灯宅": _design(
        ("旧灯宅门厅", "进入长期空置的贵族宅邸"),
        ("蒙尘起居室", "保留旧贵族生活痕迹和临时会面空间"),
        ("旧家族书库", "存放产权、继承和政治资料"),
        ("禁酒酒窖", "传闻中的地下酒窖，尚未确认（秘密通道旁的隐藏酒窖）", "hidden"),
        ("海崖防炮通道", "连接旧海崖防御设施的隐蔽通道", "hidden"),
    ),
    "红磨坊酒馆": _design(
        ("红磨坊门廊", "接待街坊、酒客和临时访客"),
        ("酒馆主厅", "饮酒、用餐和公开交谈"),
        ("后院旧马厩", "安排重要谈判、马车和后勤"),
        ("酒馆账房", "处理进货、欠账和日常经营"),
        ("酒水储藏间", "存放酒桶、食材和备用用品"),
    ),
    "烛巷报刊摊": _design(
        ("报纸展示架", "展示当日报纸、公告和烟草"),
        ("烟草便笺柜", "出售烟草、便笺和小型日用品"),
        ("摊主折叠台", "收款、包装和交换街坊消息"),
        ("夜间收摊箱", "锁存报纸、零钱和未售货品"),
    ),
    "栎木街杂货铺": _design(
        ("杂货门面", "接待居民并展示面包、肥皂和罐头"),
        ("煤油日用品架", "存放煤油、肥皂和家庭用品"),
        ("散装称量台", "称量粮食、罐头和小额货物"),
        ("冷凉食品柜", "保存易腐食品和当日补货"),
        ("后院送货门", "接收批发货并处理空箱"),
    ),
    "老港绳具铺": _design(
        ("绳具展示墙", "陈列船绳、帆布和灯油"),
        ("帆布裁剪台", "按船只需求裁切和修补帆布"),
        ("小件修补台", "维修船具、扣件和日常工具"),
        ("订单登记桌", "记录船代、码头工和交付时间"),
        ("后部绳卷库", "存放成卷绳索和防潮用品"),
    ),
    "铁湾修补铺": _design(
        ("修补铺柜台", "接收工人靴、工具和锅炉小件"),
        ("靴具工作台", "维修工人靴和皮革用品"),
        ("锅炉零件架", "分类存放常用螺栓、阀件和小件"),
        ("工具试装角", "检查修复后的工具和零件"),
        ("后部工具房", "存放待修品、煤油和备用工具"),
    ),
    "南井公共水泵": _design(
        ("公共井泵台", "居民取水和排队的核心位置"),
        ("洗涤石阶", "洗衣、清洗器皿和交换消息"),
        ("取水遮棚", "避雨、等候和临时存放水桶"),
        ("维修工具箱", "保管水泵扳手和替换皮垫"),
    ),
    "金钟报刊亭": _design(
        ("金融报纸架", "展示金融报纸和市场摘要"),
        ("市政公告板", "张贴许可、法院和公共公告"),
        ("电报摘要台", "整理电报和价格的街边摘要"),
        ("报摊收纳箱", "收存零钱、未售报纸和便笺"),
    ),
    "圣桥洗衣店": _design(
        ("洗衣收件柜台", "接收学生、护士和公务员衣物"),
        ("浸洗水槽区", "浸泡、清洗并分类衣物"),
        ("熨烫工作间", "熨烫、折叠和标记衣物"),
        ("后院晾晒绳", "晾晒大件衣物和床单"),
        ("清洁用品柜", "存放肥皂、煤料和备用布料"),
    ),
    "白崖车夫站": _design(
        ("预约车夫台", "安排豪宅区马车和预约时间"),
        ("候车棚", "乘客、车夫和行李短暂等候"),
        ("行李寄存架", "接收、标记和交还行李"),
        ("车夫休息室", "车夫交班、用餐和交换路线消息"),
        ("马车停放院", "停放、检查和调度预约马车"),
    ),
    "栎木街后排出租屋": _design(
        ("后排出租屋门阶", "住户进出、收信和搬运生活用品"),
        ("合租客厅", "低租金住户共用的起居空间"),
        ("分隔睡房", "租户床位和个人物品存放"),
        ("共用灶间", "住户备餐、烧水和分配燃料"),
        ("后院洗晒处", "清洗衣物和堆放杂物"),
    ),
    "风琴巷三户公寓": _design(
        ("公寓小院门", "三户住家共享的进出门和收信处"),
        ("三户家门廊", "连接三户家庭和公共楼梯"),
        ("共用洗漱间", "住户取水、清洗和处理日常事务"),
        ("院内晾衣线", "晾晒衣物并交换邻里消息"),
        ("房东小室", "管理租金、维修和住户安排", "private"),
    ),
    "老港海员短租屋": _design(
        ("海员短租登记台", "按周登记床位和出入时间"),
        ("海员铺位厅", "短期船员集中住宿"),
        ("共用洗漱间", "处理港口工作后的清洁"),
        ("简易伙房", "为短租海员提供热食"),
        ("船员锁柜排", "存放航海用品和个人行李"),
    ),
    "铁湾煤轨工棚": _design(
        ("工棚换班门", "工人出入、点名和换班"),
        ("煤轨铺位厅", "铁路工人临时住宿"),
        ("工棚伙房", "准备夜班饭食和热水"),
        ("共用洗漱间", "清洗煤尘和工作服"),
        ("工头角落", "安排床位、班次和临时纪律", "private"),
    ),
    "南井合租院": _design(
        ("合租院公共井场", "围绕水泵安排取水和邻里交流"),
        ("多户起居间", "矿工家庭共用的居住空间"),
        ("共用厨房", "分配煤料并准备家庭饭食"),
        ("洗衣棚", "清洗衣物、晾晒和交换消息"),
        ("屋顶连廊", "观察坡地街道和煤场动静"),
    ),
    "金钟职员公寓": _design(
        ("职员公寓门厅", "银行和市政文员进出、收信"),
        ("窄身套房", "文员日常居住和存放文件"),
        ("共用厨房", "住户备餐并错峰用餐"),
        ("共用洗衣处", "处理制服和家庭衣物"),
        ("租户账册室", "管理房租、维修和住户名单", "private"),
    ),
    "圣桥学生宿舍": _design(
        ("学生宿舍门厅", "登记学生、访客和夜间出入"),
        ("公共自习室", "学生学习、写信和跨区交流"),
        ("学生铺位厅", "师范学校学生共同居住", "private"),
        ("共用洗漱间", "洗漱、洗衣和日常交接"),
        ("舍监办公室", "管理床位、纪律和访客", "private"),
    ),
    "白崖佣人小屋": _design(
        ("佣人小屋服务门", "豪宅仆役出入和领取工作安排"),
        ("仆役合住房", "女佣、厨师和车夫轮班居住", "private"),
        ("洗衣工作间", "清洗豪宅用品和工作服"),
        ("简易伙房", "准备仆役餐食和热水"),
        ("值夜小室", "安排夜班、传递豪宅消息", "private"),
    ),
}


def structures(item: dict) -> list[dict]:
    if is_street_record(item["name"]):
        return []
    if item["name"] == "灰港":
        # The city shell is an orientation scope, not an enterable building.
        return []
    if item["name"] == "白鹭屋":
        designs = _design(
            ("一楼前厅/酒吧/接待", "接待、酒水和公开谈话"),
            ("厨房", "备餐、清洗和员工后勤"),
            ("二楼六间接待房", "六间独立接待房（原文）"),
            ("三楼员工住处", "员工休息与合住空间"),
            ("地窖", "酒水储藏；白鹭屋另有地下通道入口"),
            ("后院", "后勤、短暂停留与后门缓冲区"),
            ("屋顶观察点", "观察栎木街两个入口（原文）"),
        )
        certainty = "canon"
    elif item["name"] == "旧面包房":
        designs = STRUCTURE_DESIGNS[item["name"]]
        certainty = "atlas_design"
    else:
        designs = STRUCTURE_DESIGNS.get(item["name"])
        if designs is None:
            raise RuntimeError(
                f"地点 {item['name']} 缺少显式结构设计；请先补充 STRUCTURE_DESIGNS"
            )
        certainty = "atlas_design"
    canon_names = {"一楼前厅/酒吧/接待", "厨房", "二楼六间接待房", "三楼员工住处", "地窖", "后院", "屋顶观察点"} if item["name"] == "白鹭屋" else {"地下排水连接口"} if item["name"] == "旧面包房" else set()
    materialized: list[dict] = []
    for index, entry in enumerate(designs, 1):
        node = {
            "id": f"{item['id']}__{index}",
            "name": entry["name"],
            "parentId": item["id"],
            "purpose": entry["purpose"],
            "exists": True,
            "certainty": "canon" if entry["name"] in canon_names else certainty,
        }
        if entry.get("access"):
            node["access"] = entry["access"]
        materialized.append(node)
    return materialized

def build() -> dict:
    # Street headings in the source describe public right-of-way, not a
    # building. They are represented by `streets` below and must not receive
    # an invented room layout.
    source_locations = parse_source()
    street_records = [item for item in source_locations if is_street_record(item["name"])]
    locations = [item for item in source_locations if not is_street_record(item["name"])]
    base_payload = json.loads((ROOT / "content" / "campaigns" / "gray-harbor" / "locations.json").read_text(encoding="utf-8"))
    base_locations = base_payload.get("locations", base_payload)
    # Existing location data has a few hierarchy nodes and named sub-rooms that are not
    # separate Fifth-Part headings. Keep them as first-class atlas records.
    by_name = {item["name"]: item for item in locations}
    base_coordinates = {
        "gray_harbor": [0.0, 0.0], "candle_ward": [0.0, 0.0], "oak_street": [0.0, 0.0],
        "white_heron_ground_floor": [0.0, 0.0], "white_heron_kitchen": [0.0, -0.01],
        "white_heron_second_floor": [0.0, 0.01], "white_heron_third_floor": [0.0, 0.02],
        "white_heron_cellar": [0.0, -0.01], "white_heron_backyard": [-0.01, 0.0],
        "red_mill_tavern": [0.60, 0.0],
    }
    for runtime in base_locations:
        if runtime.get("id") in EMBEDDED_BASE_SUBLOCATIONS:
            continue
        if runtime.get("kind") == "street":
            # The runtime street id remains a routing alias, but the atlas
            # street record is its only source of street facts.
            continue
        runtime_name = runtime["name"]
        if runtime_name == "栎木街15号废弃面包房" and "旧面包房" in by_name:
            by_name["旧面包房"].setdefault("aliases", []).append(runtime_name)
            continue
        if runtime_name in by_name:
            by_name[runtime_name].setdefault("aliases", []).append(runtime["id"])
            continue
        parent_id = runtime.get("parentId", "candle_ward")
        if parent_id == "gray_harbor":
            region_id, region_name = "gray_harbor_scope", "灰港（城市总览）"
            coordinate = {"xKm": base_coordinates.get(runtime["id"], [0.0, 0.0])[0], "yKm": base_coordinates.get(runtime["id"], [0.0, 0.0])[1], "basis": "inferred_scope_anchor"}
        elif parent_id in ("candle_ward", "oak_street", "white_heron_house"):
            region_id, region_name = "candle_ward", "烛巷区"
            coordinate = {"xKm": base_coordinates.get(runtime["id"], [0.0, 0.0])[0], "yKm": base_coordinates.get(runtime["id"], [0.0, 0.0])[1], "basis": "base_hierarchy_anchor"}
        else:
            region_id, region_name = "candle_ward", "烛巷区"
            coordinate = {"xKm": base_coordinates.get(runtime["id"], [0.0, 0.0])[0], "yKm": base_coordinates.get(runtime["id"], [0.0, 0.0])[1], "basis": "base_hierarchy_anchor"}
        extra = {
            "id": f"runtime_{runtime['id']}",
            "chapter": "supplemental.canon",
            "recordType": "hierarchy_node",
            "name": runtime_name,
            "aliases": list(runtime.get("aliases", [])),
            "source": {"status": "canon", "document": "content/campaigns/gray-harbor/locations.json", "recordId": runtime["id"]},
            "surface": runtime.get("description", ""),
            "people": "",
            "resources": "",
            "risk": "",
            "regionId": region_id,
            "regionName": region_name,
            "kind": runtime.get("kind", "building"),
            "coordinate": coordinate,
            "structure": structures({
                "id": f"runtime_{runtime['id']}",
                "name": runtime_name,
                "surface": runtime.get("description", ""),
            }),
            "canonNotes": runtime.get("description", ""),
            "inferredNotes": "项目原有地点基础资料中的层级补充；与第五编地点记录互为导航，不新增剧情事实。",
        }
        locations.append(extra)
        by_name[runtime_name] = extra
    for item_id, name, region_id, description, coordinate in INFERRED_DAILY:
        if name in by_name:
            continue
        region = next(r for r in REGIONS.values() if r["id"] == region_id)
        extra = {
            "id": item_id, "chapter": "supplemental.inferred", "recordType": "daily_service", "name": name, "aliases": [],
            "source": {"status": "inferred", "document": SOURCE.name, "reason": "补足空街道日常服务"},
            "surface": description, "people": "商贩、居民或通勤者", "resources": "日常补给与街坊消息", "risk": "价格波动与夜间治安",
            "regionId": region_id, "regionName": region["name"], "kind": "daily_service",
            "coordinate": {"xKm": coordinate[0], "yKm": coordinate[1], "basis": "inferred_daily_anchor"},
            "structure": structures({"id": item_id, "name": name, "surface": description}),
            "canonNotes": "", "inferredNotes": "为填补空街道而添加的日常服务点，不承载主线关键事实。",
        }
        locations.append(extra)
        by_name[name] = extra
    for item_id, name, region_id, description, coordinate in INFERRED_HOUSING:
        if name in by_name:
            continue
        region = next(r for r in REGIONS.values() if r["id"] == region_id)
        extra = {
            "id": item_id, "chapter": "supplemental.inferred", "recordType": "housing",
            "name": name, "aliases": [],
            "source": {"status": "inferred", "document": SOURCE.name, "reason": "补充可居住背景地点"},
            "surface": description, "people": "租户、房东或值夜人", "resources": "床位、厨房、洗衣与邻里关系", "risk": "租金、火灾与房东驱逐",
            "regionId": region_id, "regionName": region["name"], "kind": "residence",
            "coordinate": {"xKm": coordinate[0], "yKm": coordinate[1], "basis": "inferred_housing_anchor"},
            "structure": structures({"id": item_id, "name": name, "surface": description}),
            "canonNotes": "", "inferredNotes": "背景居住点；除非玩家主动介入，不自动生成主线或支线事件。",
        }
        locations.append(extra)
        by_name[name] = extra
    for item in locations:
        if item["chapter"] in ("supplemental.canon", "supplemental.inferred"):
            region = next((r for r in REGIONS.values() if r["id"] == item["regionId"]), {"id": item["regionId"], "name": item["regionName"], "anchor": [0.0, 0.0]})
            ordinal = 0
        else:
            region_key = item["chapter"].rsplit(".", 1)[0]
            region = REGIONS[region_key]
            ordinal = int(item["chapter"].split(".")[-1]) - 1
        x, y = region["anchor"]
        if item["name"] == "白鹭屋":
            x, y = 0.0, 0.0
        elif item["chapter"] in ("supplemental.canon", "supplemental.inferred"):
            x, y = item["coordinate"]["xKm"], item["coordinate"]["yKm"]
        elif item["name"] in ("旧面包房", "栎木街市场"):
            x, y = (-0.03, 0.00) if item["name"] == "旧面包房" else (0.02, -0.42)
        elif item["name"] == "圣烛小教堂":
            x, y = (0.16, -0.56)
        else:
            ox, oy = OFFSETS[ordinal]
            x, y = round(x + ox * LOCAL_DISTANCE_SCALE, 3), round(y + oy * LOCAL_DISTANCE_SCALE, 3)
        item.setdefault("aliases", [])
        existing_kind = item.get("kind")
        atlas_kind = (
            existing_kind
            if item["chapter"] == "supplemental.canon"
            and existing_kind in {"city", "district", "street"}
            else kind_for(item["surface"], item["name"])
        )
        item.update({
            "regionId": region["id"],
            "regionName": region["name"],
            "kind": atlas_kind,
            "coordinate": {"xKm": x, "yKm": y, "basis": "inferred_grid"},
            "structure": item.get("structure") if item.get("recordType") in ("housing", "daily_service") else structures(item),
            "canonNotes": item["surface"],
            "inferredNotes": "建筑分区、坐标、街道连接与时间均为可调整的合理化补充；未改变原文事件条件。",
        })
    # Every location owns its internal sublocations. A room/floor is embedded
    # in `structure` and is never emitted as a second top-level location.
    by_id = {item["id"]: item for item in locations}
    for item in locations:
        item.setdefault("recordType", "location")
        item["parentId"] = item.get("regionId")
        item["parentType"] = "region"
        item["children"] = []
        for index, child in enumerate(item.get("structure", []), 1):
            child.setdefault("exists", True)
            if "certainty" not in child:
                child["certainty"] = "canon" if child.get("status") == "canon" else "atlas_design"
            child.setdefault("id", f"{item['id']}__{index}")
            child["parentId"] = item["id"]
            child["parentType"] = "location"
            child["recordType"] = "sublocation"
            item["children"].append(child["id"])
    origin = next(item for item in locations if item["name"] == "白鹭屋")
    for item in locations:
        distance = math.hypot(item["coordinate"]["xKm"] - origin["coordinate"]["xKm"], item["coordinate"]["yKm"] - origin["coordinate"]["yKm"])
        walk = 0 if distance == 0 else max(1, round(distance * 60 / 4.5))
        carriage = 0 if distance == 0 else max(1, round(distance * 60 / 10))
        item["travelFromWhiteHeron"] = {"distanceKm": round(distance, 2), "walkMinutes": walk, "horseCarriageMinutes": carriage, "basis": "inferred_speed_model"}
        item["internalTransitMinutes"] = {"short": 1, "long": 3 if item["kind"] == "building" else 5, "basis": "inferred"}
    # Assign every location to a named street. The assignment is explicit so a
    # route can be reconstructed without guessing from region names.
    canon_streets = {"栎木街", "风琴巷", "鱼骨街", "外国水手街", "铁湾女工宿舍街"}
    street_defs = {street_id: {"id": street_id, "name": name, "regionId": region_id, "exists": True, "certainty": "canon" if name in canon_streets else "atlas_design", "status": "canon" if name in canon_streets else "inferred", "locationIds": []}
                   for region_id, defs in LOCAL_STREETS.items() for street_id, name in defs}
    explicit_street_names = {
        "栎木街": "candle_oak", "风琴巷": "candle_organ", "外国水手街": "harbor_sailor",
        "鱼骨街": "harbor_fishbone", "煤场": "iron_coalrail", "铁湾女工宿舍": "iron_workers",
        "南井": "slope_southwell", "黑坡": "slope_main", "邮电": "bell_post",
        "金钟": "bell_main", "圣桥": "bridge_rise", "圣烛": "candle_candle_lane", "领事": "cliff_consulate",
        "白崖": "cliff_seaview",
    }
    per_region: dict[str, list[dict]] = {}
    for item in locations:
        per_region.setdefault(item["regionId"], []).append(item)
    for region_id, region_items in per_region.items():
        defs = LOCAL_STREETS.get(region_id, [("candle_back_lane", "烛巷后街")])
        street_counts: dict[str, int] = {}
        for ordinal, item in enumerate(region_items):
            street_id = None
            for token, candidate in explicit_street_names.items():
                if token in item["name"] and candidate in street_defs:
                    street_id = candidate
                    break
            if street_id is None:
                street_id = defs[ordinal % len(defs)][0]
            item["streetIds"] = [street_id]
            street_counts[street_id] = street_counts.get(street_id, 0) + 1
            item["streetPositionM"] = street_counts[street_id] * STREET_POSITION_STEP_M
            if street_id in street_defs and item.get("kind") not in {"city", "district"}:
                street_defs[street_id]["locationIds"].append(item["id"])
    for name, position in {"白鹭屋": 0, "栎木街市场": 2 * STREET_POSITION_STEP_M, "红磨坊酒馆": 3 * STREET_POSITION_STEP_M}.items():
        if name in by_name:
            by_name[name]["streetPositionM"] = position
    for region_id, defs in LOCAL_STREETS.items():
        anchor = next(r["anchor"] for r in REGIONS.values() if r["id"] == region_id)
        for index, (street_id, name) in enumerate(defs):
            angle = (index - 2) * 0.35
            start = [round(anchor[0] - 0.65 + angle, 3), round(anchor[1] - 0.35 - angle * 0.2, 3)]
            end = [round(anchor[0] + 0.65 + angle, 3), round(anchor[1] + 0.35 - angle * 0.2, 3)]
            street_defs[street_id].update({"centerline": {"startKm": start, "endKm": end}, "sequence": index + 1})
    # Canonical street headings extracted from the source use the same
    # routing representation as inferred streets, but do not necessarily
    # have a hand-authored centerline. Use their region anchor until a more
    # precise survey is supplied.
    for street in street_defs.values():
        if "centerline" in street:
            continue
        region = next((value for value in REGIONS.values() if value["id"] == street["regionId"]), None)
        anchor = region["anchor"] if region is not None else [0.0, 0.0]
        street["centerline"] = {"startKm": list(anchor), "endKm": list(anchor)}
        street["sequence"] = 0
    streets = list(street_defs.values())
    street_connections = []
    for region_id, defs in LOCAL_STREETS.items():
        for (left_id, _), (right_id, _) in zip(defs, defs[1:]):
            distance = round(0.30 * LOCAL_DISTANCE_SCALE, 2)
            street_connections.append({"fromStreetId": left_id, "toStreetId": right_id, "distanceKm": distance, "junction": "横向交叉口", "status": "inferred"})
            street_connections.append({"fromStreetId": right_id, "toStreetId": left_id, "distanceKm": distance, "junction": "横向交叉口", "status": "inferred"})
    # Canonical local route used by the worked example: the market can reach
    # the chapel without traversing every parallel lane in the ward.
    for left_id, right_id, distance in (("candle_oak", "candle_back_lane", 0.28 * LOCAL_DISTANCE_SCALE), ("candle_back_lane", "candle_candle_lane", 0.15 * LOCAL_DISTANCE_SCALE)):
        street_connections.append({"fromStreetId": left_id, "toStreetId": right_id, "distanceKm": distance, "junction": "栎木街横街口", "status": "inferred"})
        street_connections.append({"fromStreetId": right_id, "toStreetId": left_id, "distanceKm": distance, "junction": "栎木街横街口", "status": "inferred"})
    for index, (from_region, to_region, name, distance) in enumerate(ROUTES, 1):
        from_defs = LOCAL_STREETS[from_region]; to_defs = LOCAL_STREETS[to_region]
        corridor_id = f"corridor_{index:02d}"
        streets.append({"id": corridor_id, "name": name, "regionId": None, "regionIds": [from_region, to_region], "exists": True, "certainty": "atlas_design", "status": "inferred", "locationIds": [], "centerline": {"startKm": next(r["anchor"] for r in REGIONS.values() if r["id"] == from_region), "endKm": next(r["anchor"] for r in REGIONS.values() if r["id"] == to_region)}, "sequence": 0})
        street_connections.extend([
            {"fromStreetId": from_defs[-1][0], "toStreetId": corridor_id, "distanceKm": round(distance / 2, 2), "junction": "区域边界", "status": "inferred"},
            {"fromStreetId": corridor_id, "toStreetId": to_defs[0][0], "distanceKm": round(distance / 2, 2), "junction": "区域边界", "status": "inferred"},
            {"fromStreetId": to_defs[0][0], "toStreetId": corridor_id, "distanceKm": round(distance / 2, 2), "junction": "区域边界", "status": "inferred"},
            {"fromStreetId": corridor_id, "toStreetId": from_defs[-1][0], "distanceKm": round(distance / 2, 2), "junction": "区域边界", "status": "inferred"},
        ])
    # Re-space each street's actual places along its frontage.  The coordinate
    # remains useful for the atlas view, while this ordered position is the
    # authoritative local travel measure.
    for street in street_defs.values():
        members = [item for item in locations if street["id"] in item.get("streetIds", [])]
        members.sort(key=lambda item: (item["coordinate"]["xKm"], item["coordinate"]["yKm"], item["id"]))
        for index, item in enumerate(members):
            if item["name"] == "白鹭屋":
                item["streetPositionM"] = 0
            else:
                item["streetPositionM"] = (index + 1) * STREET_POSITION_STEP_M

    # Embedded sublocations inherit the parent frontage for lookup purposes.
    for item in locations:
        for child in item.get("structure", []):
            child["streetIds"] = list(item.get("streetIds", []))
    for street in street_defs.values():
        street["locationIds"] = [
            item["id"]
            for item in locations
            if street["id"] in item.get("streetIds", [])
            and item.get("kind") not in {"city", "district"}
        ]
    adjacency: dict[str, list[tuple[str, float]]] = {}
    for edge in street_connections:
        adjacency.setdefault(edge["fromStreetId"], []).append((edge["toStreetId"], edge["distanceKm"]))
    def shortest_street_path(start: str, target: str) -> tuple[list[str], float]:
        queue = [(0.0, start, [start])]
        best: dict[str, float] = {start: 0.0}
        while queue:
            distance, node, path = heapq.heappop(queue)
            if node == target:
                return path, round(distance, 2)
            if distance > best.get(node, float("inf")):
                continue
            for neighbor, weight in adjacency.get(node, []):
                candidate = distance + weight
                if candidate < best.get(neighbor, float("inf")):
                    best[neighbor] = candidate
                    heapq.heappush(queue, (candidate, neighbor, path + [neighbor]))
        return [start], 0.0
    origin_street = (origin.get("streetIds") or ["candle_oak"])[0]
    for item in locations:
        target_street = (item.get("streetIds") or [origin_street])[0]
        path, distance = shortest_street_path(origin_street, target_street)
        if target_street == origin_street:
            distance = round(abs(item.get("streetPositionM", 0) - origin.get("streetPositionM", 0)) / 1000, 2)
        item["streetTravelFromWhiteHeron"] = {"streetPath": path, "distanceKm": distance, "walkMinutes": 0 if distance == 0 else max(1, round(distance * 60 / 4.5)), "horseCarriageMinutes": 0 if distance == 0 else max(1, round(distance * 60 / 10)), "basis": "street_graph"}
    market_path, market_distance = shortest_street_path(by_name["栎木街市场"]["streetIds"][0], by_name["圣烛小教堂"]["streetIds"][0])
    route_examples = [{
        "fromLocationId": by_name["栎木街市场"]["id"],
        "toLocationId": by_name["圣烛小教堂"]["id"],
        "streetPath": market_path,
        "distanceKm": market_distance,
        "walkMinutes": max(1, round(market_distance * 60 / 4.5)),
        "horseCarriageMinutes": max(1, round(market_distance * 60 / 10)),
        "calculation": "先沿栎木街到烛巷后街，再经横向交叉口进入圣烛巷；按街段长度相加，不使用两点直线距离。",
        "status": "inferred",
    }]
    routes = []
    for a, b, street, distance in ROUTES:
        routes.append({"fromRegion": a, "toRegion": b, "street": street, "distanceKm": distance, "status": "inferred"})
        routes.append({"fromRegion": b, "toRegion": a, "street": street, "distanceKm": distance, "status": "inferred"})
    # A directly usable adjacency graph: only neighboring places on the same
    # street are linked. A street is the connector, never a place-to-place
    # substitute and never a container for invented rooms.
    location_links = []
    for street in streets:
        street_items = sorted(
            (item for item in locations if street["id"] in item.get("streetIds", [])),
            key=lambda item: item.get("streetPositionM", 0),
        )
        for left, right in zip(street_items, street_items[1:]):
            distance = round(abs(right.get("streetPositionM", 0) - left.get("streetPositionM", 0)) / 1000, 2)
            street_id = (left.get("streetIds") or [None])[0]
            street_name = next((street["name"] for street in streets if street["id"] == street_id), f"{left['regionName']}内部街网")
            for a, b in ((left, right), (right, left)):
                location_links.append({"fromLocationId": a["id"], "toLocationId": b["id"], "streetId": street_id, "street": street_name, "distanceKm": distance, "status": "inferred"})
    def add_link(a_name: str, b_name: str, street: str, distance: float, status: str) -> None:
        a = next(x for x in locations if x["name"] == a_name); b = next(x for x in locations if x["name"] == b_name)
        location_links.extend([
            {"fromLocationId": a["id"], "toLocationId": b["id"], "streetId": (a.get("streetIds") or [None])[0], "street": street, "distanceKm": distance, "status": status},
            {"fromLocationId": b["id"], "toLocationId": a["id"], "streetId": (b.get("streetIds") or [None])[0], "street": street, "distanceKm": distance, "status": status},
        ])
    add_link("白鹭屋", "旧面包房", "栎木街后巷/地下排水通道", 0.04, "canon")
    add_link("白鹭屋", "栎木街市场", "栎木街", 0.42, "inferred")
    if any(x["name"] == "红磨坊酒馆" for x in locations):
        add_link("白鹭屋", "红磨坊酒馆", "栎木街—红磨坊巷", 0.60, "canon")
    return {
        "schemaVersion": 1,
        "atlasId": "gray-harbor-v42-location-atlas",
        "source": {"document": SOURCE.name, "fifthPartLocationCount": 84, "canonLocationRecordCount": 88, "embeddedSubLocationCount": 6, "inferredBackgroundRecordCount": 16, "totalLocationRecordCount": len(locations), "canonRule": "地点名称与四项描述来自原文；补充字段不得覆盖事件/状态权威。"},
        "coordinateSystem": {"origin": "白鹭屋", "unit": "km", "xPositive": "east", "yPositive": "north", "precision": "0.01 km display; 0.001 km storage"},
        "speedModel": {"walkingKmh": 4.5, "horseCarriageKmh": 10.0, "indoorMinutesPerSegment": 1},
        "scopes": [{"id": "gray_harbor_scope", "name": "灰港", "type": "city", "childRegionIds": [r["id"] for r in REGIONS.values()]}],
        "regions": [dict(r, parentScopeId="gray_harbor_scope") for r in REGIONS.values()],
        "locations": locations,
        "streets": streets,
        "streetConnections": street_connections,
        "routeExamples": route_examples,
        "streetLinks": routes,
        "locationLinks": location_links,
    }

def render_md(data: dict) -> str:
    out = ["# 灰港《黑潮王座》V4.2 地点与地图图册", "", "本目录由剧本第五编自动提取并补充地图层。地点名称、表面功能、常见人物、主要资源、持续风险标记为 **原文 Canon**；坐标、街道、建筑分区和出行时间标记为 **地图设计层**。每个结构条目都有 `exists=true`，并用 `certainty=canon`（原文确认）或 `certainty=atlas_design`（本图册确认设计）区分来源。", "", "## 使用约定", "", "- 原点：白鹭屋；坐标单位为 km，x 向东、y 向北。", "- 速度模型：步行 4.5 km/h，马车 10 km/h；拥挤、雨雪、封锁和夜间应在游戏结算时增加修正。", "- 地点拥有 `parentId`、`children` 和 `structure`；房间属于父建筑，不与父建筑并列。", "- 街道是地点之间的公共连接层，可以作为角色在街上行走时的当前位置，但不是建筑或功能地点；街道没有 `structure`、房间或地点能力。地点通过 `streetIds` 和 `streetPositionM` 挂接到街道。", "- 相对出行时间优先沿 `streetConnections` 计算；坐标直线距离只用于校验和缺少街道时的兜底。", "", "## 七大区域与绝对位置", "", "| 区域 | 原点坐标 (km) | 相对白鹭屋位置 | 区域内街道角色 |", "|---|---:|---|---|"]
    for r in data["regions"]:
        x, y = r["anchor"]
        direction = ("东" if x > 0.3 else "西" if x < -0.3 else "中部") + ("北" if y > 0.3 else "南" if y < -0.3 else "")
        out.append(f"| {r['name']} | ({x:.1f}, {y:.1f}) | 白鹭屋的{direction}方向 | {r['note']} |")
    out += ["", "## 地点清单", "", "| 编号 | 地点 | 区域 | 坐标 km | 所属街道 | 原文功能 | 结构（确定存在） | 徒步/马车自白鹭屋 |", "|---|---|---|---:|---|---|---|---:|"]
    for item in data["locations"]:
        c = item["coordinate"]; t = item["travelFromWhiteHeron"]
        street_names = "、".join(next((street["name"] for street in data["streets"] if street["id"] == sid), sid) for sid in item.get("streetIds", []))
        structure = "、".join(f"{s['name']}（{s['certainty']}）" for s in item["structure"])
        out.append(f"| {item['chapter']} | {item['name']} | {item['regionName']} | ({c['xKm']:.2f}, {c['yKm']:.2f}) | {street_names} | {item['surface']} | {structure} | {t['walkMinutes']} / {t['horseCarriageMinutes']} 分钟 |")
    out += ["", "## 地点父子关系", "", "`children` 是父地点拥有的子地点 ID；`structure` 是该地点内部已确认存在的结构对象。白鹭屋的厨房、二楼、三楼、地窖和后院都是白鹭屋的子地点，不是独立建筑。", "", "| 地点 | 父地点 | 子地点 |", "|---|---|---|"]
    for item in data["locations"]:
        if item.get("children") or item.get("parentType") == "location":
            parent = next((x["name"] for x in data["locations"] if x["id"] == item.get("parentId")), None)
            parent = parent or next((x["name"] for x in data["regions"] if x["id"] == item.get("parentId")), None)
            parent = parent or next((x["name"] for x in data.get("scopes", []) if x["id"] == item.get("parentId")), item.get("parentId", ""))
            children = "、".join(next((x["name"] for x in item.get("structure", []) if x["id"] == cid), cid) for cid in item.get("children", []))
            out.append(f"| {item['name']} | {parent} | {children or '—'} |")
    out += ["", "## 街道清单", "", "| 街道 | 所属区域 | 端点坐标 | 所属地点数 | 状态 |", "|---|---|---|---:|---|"]
    for street in data["streets"]:
        center = street.get("centerline", {})
        endpoints = f"{center.get('startKm', '—')} → {center.get('endKm', '—')}"
        region_name = next((r["name"] for r in data["regions"] if r["id"] == street.get("regionId")), "区域连接")
        out.append(f"| {street['name']} | {region_name} | {endpoints} | {len(street.get('locationIds', []))} | {street['status']} |")
    out += ["", "## 街道连接顺序", "", "每一条连接都写明起点街道、终点街道、交叉口和长度；地点所属街道见上表和 JSON 的 `streetIds`。", "", "| 起点街道 | 终点街道 | 交叉口 | 距离 km |", "|---|---|---|---:|"]
    for connection in data["streetConnections"]:
        a = next((x["name"] for x in data["streets"] if x["id"] == connection["fromStreetId"]), connection["fromStreetId"])
        b = next((x["name"] for x in data["streets"] if x["id"] == connection["toStreetId"]), connection["toStreetId"])
        out.append(f"| {a} | {b} | {connection['junction']} | {connection['distanceKm']:.2f} |")
    example = data["routeExamples"][0]
    out += ["", "## 相对位置推断示例：栎木街市场 → 圣烛小教堂", "", "- 两点坐标分别为 `(0.02, -0.42)` 与 `(0.16, -0.56)`；直线距离约 0.21 km，只用于校验。", f"- 实际路线沿 `{' → '.join(example['streetPath'])}`，街段长度相加约 {example['distanceKm']:.2f} km。", f"- 按步行 4.5 km/h 约 {example['walkMinutes']} 分钟，马车约 {example['horseCarriageMinutes']} 分钟；买东西、等车、避让人群不计入。"]
    out += ["", "## 地点到地点的时间计算规则", "", "### A. 同一地点内部", "", "房间、楼层、厨房、地窖和后院是父地点的 `structure` 子对象，不按城市街道计算。使用结构边的移动分钟数：相邻分区通常 1 分钟，跨楼层或较长内部通道 3 分钟，大型医院/仓库/学校跨区 5 分钟。", "", "```text", "内部用时 = 结构路径上每一段的分钟数之和", "```", "", "### B. 同一条街道", "", "读取两个地点的 `streetPositionM`：", "", "```text", "街道距离 = abs(终点 streetPositionM - 起点 streetPositionM) / 1000", "步行分钟 = round(街道距离 / 4.5 × 60)", "马车分钟 = round(街道距离 / 10 × 60)", "```", "", "### C. 不同街道", "", "1. 读取起点和终点的 `streetIds`。", "2. 在 `streetConnections` 中寻找街道图的最短路径（当前生成器使用 Dijkstra）。", "3. 将每个街道交叉口连接到交叉口两侧最近的实际地点；玩家不会进入街道节点。", "4. 把路径中的 `distanceKm` 相加，再按步行或马车速度换算时间。", "", "```text", "街道距离 = 最短街道路径中各连接 distanceKm 之和", "总分钟 = round(街道距离 / 交通速度 × 60)", "```", "", "### D. 最终结算修正", "", "纯移动时间不包含以下耗时；这些必须由游戏状态和事件另行增加：", "", "- 买票、找人、等车、装卸；", "- 拥挤、雨雪、夜间、封锁和警戒；", "- 建筑门禁、营业时间、守卫盘问；", "- 角色携带货物、受伤或需要护送。", "", "因此最终时间为：", "", "```text", "最终用时 = 内部移动时间或街道移动时间 + 等待/准备/风险修正时间", "```", "", "当前 JSON 已保存白鹭屋到各地点的 `streetTravelFromWhiteHeron`，并保存市场到教堂的 `routeExamples`。任意两个地点的通用查询应沿同一规则读取 `streetConnections`，不能由 AI 凭叙述自行估计。"]
    out += ["", "## 街道与区域连接", "", "以下为可编辑的主干连接；剧本未给出完整街网的部分均标记为推定。地点级双向邻接图完整保存在 JSON 的 `locationLinks`，其中白鹭屋—旧面包房地下通道与白鹭屋—红磨坊酒馆的连接保留原文依据。街道连接不改变地点所有权或事件条件。", "", "| 起点区域 | 终点区域 | 街道/路线 | 距离 km | 步行 | 马车 | 状态 |", "|---|---|---|---:|---:|---:|---|"]
    for link in data["streetLinks"]:
        a = next(r["name"] for r in data["regions"] if r["id"] == link["fromRegion"]); b = next(r["name"] for r in data["regions"] if r["id"] == link["toRegion"])
        w = max(1, round(link["distanceKm"] * 60 / 4.5)); c = max(1, round(link["distanceKm"] * 60 / 10))
        out.append(f"| {a} | {b} | {link['street']} | {link['distanceKm']:.1f} | {w} 分钟 | {c} 分钟 | {link['status']} |")
    out += ["", "## 建筑内部移动与日常时间", "", "- 白鹭屋：一楼前厅↔厨房 1 分钟；一楼↔二楼 1 分钟；二楼↔三楼 1 分钟；厨房↔地窖 1 分钟；地窖↔旧面包房地下通道 4 分钟；后门↔栎木街 1—2 分钟。", "- 其他建筑：同一楼层相邻分区约 1 分钟，跨层或穿过作业区约 3 分钟，大型仓厂/医院/学校跨区约 5 分钟；拥挤、警戒和封锁状态另加时间。", "- 日常准备时间不计入上述纯移动时间：买票、安检、找人、装卸、换班和等候应按事件单独结算。", "", "## 编辑审计", "", "- 第五编命名地点：84；原文/基础资料地点 94；另有 8 个日常服务点和 8 个可居住背景点，合计 %d 条记录。" % len(data["locations"]), "- 本图册不把设计层内容写回 V4.2 Canon；修改坐标、结构、街道和背景住处时保留 `certainty=atlas_design` 或 `source.status=inferred`。", "- ‘基础资料地点’指项目原有的导航节点，不是额外剧情，也不改变地点权属。"]
    out = [
        line.replace(
            "玩家不会进入街道节点",
            "玩家可以进入街道节点；街道没有房间或功能区",
        )
        for line in out
    ]
    return "\n".join(out) + "\n"

def render_overview(data: dict) -> tuple[str, dict]:
    """Create a compact whole-campaign index without copying the source prose."""
    catalog_path = ROOT / "content" / "campaigns" / "gray-harbor" / "v4.2-catalog.json"
    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    acts = []
    for state in catalog["mainlineStateMachine"]["states"]:
        attrs = state.get("attributes", {})
        acts.append({"id": state["id"], "title": state["title"], "historyStage": attrs.get("historyStage"), "powerStage": attrs.get("powerStage"), "goal": attrs.get("powerGoal")})
    timeline = []
    for event in catalog.get("timeline", []):
        attrs = event.get("attributes", {})
        if attrs.get("campaignMonth"):
            timeline.append({"month": attrs["campaignMonth"], "title": event["title"], "sourceLine": event.get("sources", [{}])[0].get("sourceLine")})
    timeline.sort(key=lambda x: x["month"])
    quests = []
    for quest in catalog.get("sideQuests", []):
        attrs = quest.get("attributes", {})
        quests.append({"id": attrs.get("questId", quest["id"]), "title": quest["title"], "relation": attrs.get("mainlineRelationship", ""), "status": attrs.get("initialStatus", "DORMANT")})
    summary = {
        "schemaVersion": 1,
        "scenarioId": catalog["scenarioId"],
        "scenarioVersion": catalog["scenarioVersion"],
        "sourceDocument": catalog["sourceDocument"],
        "canonLayers": [{"id": x["id"], "title": x["title"]} for x in catalog["canonLayers"]],
        "acts": acts,
        "regions": [{"id": r["id"], "name": r["name"], "anchor": r["anchor"]} for r in data["regions"]],
        "timelineMonths": timeline,
        "counts": {"locations": len(data["locations"]), "regions": len(data["regions"]), "characters": len(catalog.get("characters", [])), "organizations": len(catalog.get("organizations", [])), "sideQuests": len(quests), "eventSeeds": len(catalog.get("eventSeeds", []))},
        "sideQuests": quests,
    }
    out = ["# 灰港《黑潮王座》V4.2 剧本整理总览", "", f"来源：`{catalog['sourceDocument']}`（V{catalog['scenarioVersion']}，编译地点 {len(data['locations'])} 个）。本文件是导航索引，不替代原剧本；所有状态改变仍以事件和 V4.2 编译目录为准。", "", "## 一、运行规则摘要", "", "1. 世界时间、历史阶段与主角权力阶段分开推进；日期到了，历史事件发生，不能因主角未准备好而冻结。", "2. 主角输入、AI 输出和叙述不是事实；重要变化必须先通过校验事件，再更新状态。", "3. 九幕目标是权力证据，不是线性打卡；某幕失败登记缺口并继续，不能自动重开。", "4. 支线是可失效的世界事件模板；完成一条支线不能单独完成整幕，也不会凭空奖励房产、现金或关键道具。", "5. 地点名与原文四项描述为 Canon；地图图册中的坐标、街道、建筑分区和时间是可调整推定。", "", "## 二、九幕主线", "", "| 幕 | 历史/权力阶段 | 目标 |", "|---|---|---|"]
    for act in acts:
        out.append(f"| {act['historyStage']} / {act['powerStage']} | {act['title']} | {act['goal']} |")
    out += ["", "## 三、七大区域", "", "| 区域 | 相对白鹭屋原点 | 剧本争夺的核心 |", "|---|---|---|"]
    stakes = {"烛巷区": "人的秘密与街坊保护", "老港": "货物的时间与通关", "铁湾": "工人的身体与生产", "黑坡": "煤、房租、药与生存成本", "金钟": "信用、合同与体面", "圣桥": "合法性的语言", "白崖": "继承、外交与上层关系"}
    for r in data["regions"]:
        x, y = r["anchor"]
        out.append(f"| {r['name']} | ({x:.1f}, {y:.1f}) km | {stakes[r['name']]} |")
    out += ["", "## 四、六十个月世界时钟", "", "编译目录中的月度时间窗是唯一世界时间轴：第 1—36 个月是三年主线窗口，第 37—60 个月是主线未完成或完成后的后台延展槽。地点与支线应从该时钟派生。", "", "| 月份 | 时间窗 |", "|---:|---|"]
    for event in timeline:
        out.append(f"| {event['month']} | {event['title']} |")
    out += ["", "## 五、支线索引（94 条）", "", "支线默认 `DORMANT`，满足条件后才可 `AVAILABLE`；失败或忽略会留下新状态。以下仅列标题与主线关联，详细条件以 V4.2 编译目录为准。", "", "| ID | 支线 | 关联主线 | 初始状态 |", "|---|---|---|---|"]
    for q in quests:
        relation = re.sub(r"\s+", " ", q["relation"]).strip().replace("|", "/")
        out.append(f"| {q['id']} | {q['title']} | {relation} | {q['status']} |")
    out += ["", "## 六、资料文件", "", "- `location-atlas.json`：96 个顶层地点、每个地点的嵌入式子地点结构、16 条背景设计记录、45 条街道、街道连接、出行时间与来源标记。街道是可通行的公共路由节点，但不是建筑地点，不生成结构或功能区。", "- `location-atlas.md`：按区域查阅的地图、父子结构和街道网络。", "- `campaign-overview.json`：本总览的机器可读索引。", "- 生成脚本：`scripts/build_gray_harbor_map.py`。", "", "## 七、修改原则", "", "修改地图设计层时只改 `coordinate`、`structure`、`streets`、`streetConnections` 或带 `atlas_design` 的字段；不要把设计层内容写入 `canonNotes`，也不要删除原文来源行号。新增地点先标记 `source.status=inferred`，并明确 `recordType` 与父地点。"]
    return "\n".join(out) + "\n", summary

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    data = build()
    if args.check:
        assert len({x["id"] for x in data["locations"]}) == 96
        assert sum(1 for x in data["locations"] if x["chapter"].startswith("5.")) == 78
        assert len(data["streets"]) == 45
        generic_structure_labels = {
            "临街入口",
            "主要使用区",
            "后勤/储藏区",
            "后门或侧门",
            "临街柜台",
            "后储藏间",
            "基础资料层级节点",
        }
        street_names = {street["name"] for street in data["streets"]}
        for item in data["locations"]:
            if item["name"] in street_names or item["name"] == "灰港":
                assert not item["structure"], f"街道/城市壳不应有结构: {item['name']}"
                if item["name"] == "灰港":
                    assert item["kind"] == "city", "灰港城市壳必须保留 city 类型"
                continue
            assert len(item["structure"]) >= 2, f"地点结构过少: {item['name']}"
            assert all(
                node["name"] not in generic_structure_labels
                for node in item["structure"]
            ), f"地点仍使用泛化结构: {item['name']}"
            assert all(node.get("exists") is True for node in item["structure"])
        hidden_nodes = [
            node
            for item in data["locations"]
            for node in item["structure"]
            if node.get("access") in {"hidden", "secret", "concealed"}
        ]
        assert hidden_nodes
        assert all(
            any(term in f"{node['name']} {node['purpose']}" for term in ("密道", "秘密通道", "隐蔽通道", "排水通道"))
            for node in hidden_nodes
        )
        assert any(x["name"] == "白鹭屋" and x["coordinate"] == {"xKm": 0.0, "yKm": 0.0, "basis": "inferred_grid"} for x in data["locations"])
        assert data["routeExamples"][0]["streetPath"] == ["candle_oak", "candle_back_lane", "candle_candle_lane"]
        street_ids = {street["id"] for street in data["streets"]}
        assert all(item.get("streetIds") and set(item["streetIds"]).issubset(street_ids) for item in data["locations"])
        connection_pairs = {(edge["fromStreetId"], edge["toStreetId"]) for edge in data["streetConnections"]}
        route = data["routeExamples"][0]["streetPath"]
        assert all((route[index], route[index + 1]) in connection_pairs for index in range(len(route) - 1))
        assert all(child_id not in {x["id"] for x in data["locations"]} for item in data["locations"] for child_id in item["children"])
        assert all(not item["structure"] for item in data["locations"] if item["name"] in {street["name"] for street in data["streets"]})
        assert all(
            item.get("streetPositionM", 0) >= 0
            for item in data["locations"]
        )
        print("ok: %d top-level locations (embedded sublocations), %d streets, %d directed location links" % (len(data["locations"]), len(data["streets"]), len(data["locationLinks"])))
        return
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "location-atlas.json").write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (OUT_DIR / "location-atlas.md").write_text(render_md(data), encoding="utf-8")
    overview_md, overview_json = render_overview(data)
    (OUT_DIR / "campaign-overview.md").write_text(overview_md, encoding="utf-8")
    (OUT_DIR / "campaign-overview.json").write_text(json.dumps(overview_json, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {OUT_DIR / 'location-atlas.json'}")
    print(f"wrote {OUT_DIR / 'location-atlas.md'}")
    print(f"wrote {OUT_DIR / 'campaign-overview.md'}")
    print(f"wrote {OUT_DIR / 'campaign-overview.json'}")

if __name__ == "__main__":
    main()
