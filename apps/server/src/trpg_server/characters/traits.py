"""Character ability taxonomy and language-style profile compilation.

This module turns explicit campaign biography/role text into bounded profile
metadata.  The result is never an authority for an action: abilities are
context labels and language style is presentation guidance only.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
from collections.abc import Mapping, Sequence
from typing import Any


@dataclass(frozen=True, slots=True)
class AbilitySpec:
    ability_id: str
    name: str
    domain: str
    description: str


# Keep this list deliberately finite and versionable.  It is broad enough for
# the professions and institutions in V4.2 without turning every occupation
# into a one-off skill.
ABILITY_CATALOG: tuple[AbilitySpec, ...] = (
    AbilitySpec("financial_accounting", "账务与记账", "finance", "记录、核对和解释日常账目。"),
    AbilitySpec("financial_audit", "审计与合规", "finance", "检查账目、流程和合规风险。"),
    AbilitySpec("credit_risk_assessment", "信贷与风险评估", "finance", "评估借贷、抵押和违约风险。"),
    AbilitySpec("trade_commerce", "商贸经营", "commerce", "进行买卖、市场判断和商业经营。"),
    AbilitySpec("negotiation", "谈判", "social", "在利益冲突中交换条件并达成安排。"),
    AbilitySpec("mediation_arbitration", "调解与仲裁", "social", "处理争端并提出可执行的折中方案。"),
    AbilitySpec("contract_drafting", "合同与文书起草", "law", "起草、审阅和处理正式合同文书。"),
    AbilitySpec("legal_reasoning", "法律分析", "law", "解释法律规则、证据和程序后果。"),
    AbilitySpec("organization_management", "组织管理", "leadership", "安排人员、流程、资源和日常职责。"),
    AbilitySpec("leadership_mobilization", "领导与动员", "leadership", "组织成员采取一致行动并承担责任。"),
    AbilitySpec("procurement_logistics", "采购与物流", "operations", "采购、储运和分配物资。"),
    AbilitySpec("cargo_port_operations", "码头与货运作业", "operations", "处理码头、货栈和装卸流程。"),
    AbilitySpec("information_gathering", "信息搜集", "intelligence", "从人物、地点和公开渠道获取信息。"),
    AbilitySpec("rumor_verification", "传闻核验", "intelligence", "比较来源并判断传闻的可靠程度。"),
    AbilitySpec("surveillance_tracking", "跟踪与监视", "intelligence", "持续观察、跟踪或记录目标动向。"),
    AbilitySpec("journalism_reporting", "新闻采写", "communication", "采访、核实并编写新闻报道。"),
    AbilitySpec("archival_research", "档案研究", "research", "检索、整理和解释历史档案。"),
    AbilitySpec("persuasion_oratory", "说服与演说", "communication", "通过表达、论证和演说影响他人。"),
    AbilitySpec("political_strategy", "政治策略", "politics", "在制度、派系和舆论之间安排策略。"),
    AbilitySpec("social_etiquette", "社交礼仪", "social", "在正式或复杂社交场合维持分寸。"),
    AbilitySpec("intimidation_enforcement", "街头威慑与执行", "security", "以威慑、催收或强制执行达成目的。"),
    AbilitySpec("deception_cover_identity", "欺骗与掩护身份", "intelligence", "隐藏意图、编造说法或维持掩护身份。"),
    AbilitySpec("forgery_document_handling", "文书伪造与处理", "law", "处理、辨认或制作具有法律效力的文书。"),
    AbilitySpec("teaching_mentoring", "教学与指导", "education", "传授知识、训练技能或指导新人。"),
    AbilitySpec("translation_interpreting", "翻译与口译", "communication", "在语言之间准确传达信息。"),
    AbilitySpec("policing_investigation", "警务与调查", "law", "依法调查、取证和处理治安事务。"),
    AbilitySpec("customs_inspection", "海关查验", "law", "检查货物、申报和进出港手续。"),
    AbilitySpec("civic_administration", "市政与许可行政", "administration", "处理登记、许可证和公共行政流程。"),
    AbilitySpec("clinical_medicine", "临床医疗", "medicine", "诊断、治疗和判断临床风险。"),
    AbilitySpec("nursing_care", "护理与照护", "medicine", "提供持续护理、照护和康复协助。"),
    AbilitySpec("first_aid_trauma", "急救与创伤处理", "medicine", "在紧急情况下稳定伤情并施行急救。"),
    AbilitySpec("pharmacy_herbalism", "药剂与草药", "medicine", "配制、识别和使用药剂或草药。"),
    AbilitySpec("mortuary_practice", "殡仪事务", "care", "处理遗体、葬仪和相关手续。"),
    AbilitySpec("religious_ritual", "宗教仪式", "religion", "主持或解释宗教仪式与教务。"),
    AbilitySpec("seamanship_navigation", "航海与导航", "maritime", "操作船只、判断航向和处理海上事务。"),
    AbilitySpec("railway_dispatch", "铁路调度", "transport", "安排列车、线路和货运时刻。"),
    AbilitySpec("mechanical_repair", "机械维修", "technical", "诊断、维护和修理机械设备。"),
    AbilitySpec("engineering_infrastructure", "工程与基础设施", "technical", "设计或维护建筑、管线和公共设施。"),
    AbilitySpec("industrial_quality_safety", "工业质检与安全", "technical", "检查生产质量、危险和作业安全。"),
    AbilitySpec("firefighting_rescue", "消防与救援", "emergency", "处理火灾、救援和现场安全。"),
    AbilitySpec("printing_press", "印刷与制版", "craft", "操作印刷、制版和小型出版流程。"),
    AbilitySpec("tailoring_costume", "裁缝与服装", "craft", "裁剪、缝制和维护服装。"),
    AbilitySpec("cooking_food_preparation", "烹饪与食物准备", "service", "准备食物、安排厨房和控制卫生。"),
    AbilitySpec("hospitality_bartending", "待客与酒水服务", "service", "接待客人、调酒和处理服务现场。"),
    AbilitySpec("music_performance", "音乐表演", "arts", "演奏、演唱或组织音乐表演。"),
    AbilitySpec("visual_art_design", "视觉艺术与设计", "arts", "进行绘画、视觉表达或设计工作。"),
    AbilitySpec("photography", "摄影", "arts", "操作摄影设备并记录可用影像。"),
    AbilitySpec("hairdressing_grooming", "理发与仪容", "service", "理发、修饰和维护仪容。"),
    AbilitySpec("unarmed_combat", "徒手格斗", "security", "在明确训练或经历基础上进行徒手搏斗。"),
    AbilitySpec("weapons_firearms", "武器与枪械", "security", "在明确训练或职责基础上使用武器。"),
    AbilitySpec("stealth_lock_security", "潜行与锁具安保", "security", "进行隐蔽行动、锁具处理或安保防护。"),
    AbilitySpec("carriage_driving", "马车与驾驶", "transport", "驾驶、调度和维护出租马车。"),
    AbilitySpec("athletics_boxing", "体能与拳赛", "security", "进行拳赛、体能训练或高强度体力活动。"),
)

ABILITY_BY_ID = {value.ability_id: value for value in ABILITY_CATALOG}


_ABILITY_RULES: tuple[tuple[str, tuple[str, ...], str, str], ...] = (
    ("financial_accounting", ("账务", "会计", "财务", "银行文员", "账房", "记账"), "advanced", "explicit_role"),
    ("financial_audit", ("审计", "合规", "核账", "税务"), "advanced", "explicit_role"),
    ("credit_risk_assessment", ("信贷", "贷款", "信用", "银行经理"), "advanced", "explicit_role"),
    ("trade_commerce", ("商人", "贸易商", "贸易", "进口商", "经纪", "商贸", "店主", "老板", "当铺老板", "煤商", "投机客", "摊贩", "商业天才", "经营"), "competent", "explicit_role"),
    ("negotiation", ("谈判", "谈判人", "谈判专家", "交涉", "保险"), "advanced", "explicit_role"),
    ("mediation_arbitration", ("仲裁", "调解", "纠纷", "工会主席", "协会会长"), "advanced", "explicit_role"),
    ("contract_drafting", ("合同", "律师", "法务", "法律顾问"), "advanced", "explicit_role"),
    ("legal_reasoning", ("律师", "法官", "检察官", "法律", "警务", "警探"), "advanced", "explicit_role"),
    ("organization_management", ("经理", "主管", "负责人", "管理", "主任", "会长", "校长"), "competent", "explicit_role"),
    ("leadership_mobilization", ("首领", "主席", "市长", "董事长", "议员", "会长", "队长", "组织者", "召集人", "负责人", "创始人"), "advanced", "explicit_role"),
    ("procurement_logistics", ("采购", "后勤", "供应", "物流", "仓储", "货运"), "competent", "explicit_role"),
    ("cargo_port_operations", ("码头", "货栈", "装卸", "港口货运", "港口作业", "港口走私", "工头", "煤栈"), "advanced", "explicit_role"),
    ("information_gathering", ("情报", "线人", "调查", "记者", "档案", "打听"), "competent", "explicit_role"),
    ("rumor_verification", ("情报", "记者", "调查", "档案员", "报纸"), "competent", "explicit_role"),
    ("surveillance_tracking", ("跟踪", "监视", "侦探", "线人", "调查员"), "advanced", "explicit_role"),
    ("journalism_reporting", ("记者", "报社", "编辑", "新闻", "报童"), "advanced", "explicit_role"),
    ("archival_research", ("档案", "图书馆", "文献", "研究员"), "advanced", "explicit_role"),
    ("persuasion_oratory", ("演说", "发言", "政治家", "政治代表", "政治接口", "劳工政治", "政治新派", "议员", "传教", "领袖"), "competent", "explicit_role"),
    ("political_strategy", ("市长", "议员", "政治家", "政治代表", "政治接口", "劳工政治", "政治新派", "理事", "改革派"), "advanced", "explicit_role"),
    ("social_etiquette", ("社交", "俱乐部", "酒店", "领班", "贵族", "委员会"), "competent", "role_context"),
    # Membership in a faction is deliberately not enough for a combat or
    # enforcement tag.  The role must describe an actual enforcement duty.
    ("intimidation_enforcement", ("收账", "打手", "执行头目", "暴力执行", "安保负责人"), "advanced", "explicit_role"),
    ("deception_cover_identity", ("间谍", "化名", "掩护身份", "卧底"), "competent", "script_experience"),
    ("forgery_document_handling", ("伪造", "借据", "文书", "登记", "书记官"), "competent", "explicit_role"),
    ("teaching_mentoring", ("教师", "教官", "校长", "导师", "培训", "师范"), "advanced", "explicit_role"),
    ("translation_interpreting", ("翻译", "口译", "领事馆", "语言"), "advanced", "explicit_role"),
    ("policing_investigation", ("警探", "警署", "警察", "警务", "侦探", "调查员", "警校"), "advanced", "explicit_role"),
    ("customs_inspection", ("海关", "查验", "申报"), "advanced", "explicit_role"),
    ("civic_administration", ("市政", "许可证", "登记处", "行政", "委员会", "公职"), "advanced", "explicit_role"),
    ("clinical_medicine", ("医生", "医师", "临床", "诊疗"), "expert", "explicit_role"),
    ("nursing_care", ("护士", "护理", "社工", "救济院", "照护"), "advanced", "explicit_role"),
    ("first_aid_trauma", ("急救", "创伤", "军医", "护士", "医生"), "competent", "script_experience"),
    ("pharmacy_herbalism", ("药剂", "药师", "药材", "草药"), "advanced", "explicit_role"),
    ("mortuary_practice", ("殡仪", "墓园", "掘墓", "葬仪"), "advanced", "explicit_role"),
    ("religious_ritual", ("神父", "主教", "教堂", "教会", "宗教"), "advanced", "explicit_role"),
    ("seamanship_navigation", ("船长", "远洋船员", "航海", "导航", "海军上校", "海军军官"), "advanced", "explicit_role"),
    ("railway_dispatch", ("铁路调度", "调度员", "列车调度", "车站调度"), "advanced", "explicit_role"),
    ("mechanical_repair", ("机械师", "机修", "机修厂", "机械维修", "修理厂", "维修", "机工"), "advanced", "explicit_role"),
    ("engineering_infrastructure", ("工程师", "建筑", "煤气公司", "基础设施"), "advanced", "explicit_role"),
    ("industrial_quality_safety", ("质检", "工业安全", "安全检查", "钢厂", "工厂安全"), "advanced", "explicit_role"),
    ("firefighting_rescue", ("消防", "救援", "火灾"), "advanced", "explicit_role"),
    ("printing_press", ("印刷", "印刷厂", "出版"), "advanced", "explicit_role"),
    ("tailoring_costume", ("裁缝", "缝纫", "服装师", "服装设计"), "advanced", "explicit_role"),
    ("cooking_food_preparation", ("厨师", "炊事", "厨房", "烹饪"), "expert", "explicit_role"),
    ("hospitality_bartending", ("酒保", "酒馆", "酒店", "餐厅", "歌厅", "旅馆", "侍女", "接待"), "competent", "explicit_role"),
    ("music_performance", ("歌手", "钢琴手", "音乐家", "演奏家", "音乐表演"), "advanced", "explicit_role"),
    ("visual_art_design", ("画家", "艺术家", "视觉艺术", "绘画", "视觉设计"), "advanced", "explicit_role"),
    ("photography", ("摄影", "摄影师"), "advanced", "explicit_role"),
    ("hairdressing_grooming", ("理发", "发型", "仪容"), "advanced", "explicit_role"),
    ("unarmed_combat", ("拳手", "格斗", "搏斗", "退伍兵", "陆军上尉"), "competent", "explicit_role"),
    ("weapons_firearms", ("枪械", "武器商", "武器", "军官", "上尉", "上校", "军火", "炮兵", "射击"), "competent", "explicit_role"),
    ("stealth_lock_security", ("安保", "锁具", "潜行", "锁匠", "私人侦探"), "competent", "script_experience"),
    ("carriage_driving", ("车夫", "出租车司机", "马车司机", "驾驶马车"), "advanced", "explicit_role"),
    ("athletics_boxing", ("拳赛", "拳击", "体能"), "advanced", "explicit_role"),
)


LANGUAGE_STYLE_KEYS = (
    "formality",
    "politeness",
    "directness",
    "verbosity",
    "pacing",
    "sentenceStyle",
    "addressTerms",
    "catchphrases",
    "pressureShift",
    "taboos",
    "sourceStatus",
    "sourceRefs",
    "notes",
)


def empty_language_style(status: str = "unknown") -> dict[str, Any]:
    return {
        "formality": None,
        "politeness": None,
        "directness": None,
        "verbosity": None,
        "pacing": None,
        "sentenceStyle": None,
        "addressTerms": [],
        "catchphrases": [],
        "pressureShift": None,
        "taboos": [],
        "sourceStatus": status,
        "sourceRefs": [],
        "notes": "",
    }


def _compact_refs(source_refs: Sequence[Any] | None) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for raw in source_refs or ():
        if isinstance(raw, Mapping):
            value = raw
            get = value.get
        else:
            get = lambda key, _raw=raw: getattr(_raw, key, None) or getattr(
                _raw, _camel(key), None
            )
        title = get("title")
        if not title:
            continue
        result.append(
            {
                "title": title,
                "sourceLine": get("sourceLine") or get("source_line"),
                "sourceEndLine": get("sourceEndLine") or get("source_end_line"),
                "sourceFingerprint": get("sourceFingerprint") or get("source_fingerprint"),
            }
        )
    return result


def _camel(value: str) -> str:
    head, *tail = value.split("_")
    return head + "".join(part[:1].upper() + part[1:] for part in tail)


def _source_text(source_refs: Sequence[Any] | None) -> str:
    chunks: list[str] = []
    for raw in source_refs or ():
        if isinstance(raw, Mapping):
            excerpt = raw.get("excerpt", "")
        else:
            excerpt = getattr(raw, "excerpt", "")
        if excerpt:
            chunks.append(str(excerpt))
    return "\n".join(chunks)


def _downgrade(level: str) -> str:
    return {"expert": "advanced", "advanced": "competent", "competent": "working", "working": "working"}[level]


_EXPLICIT_ROLE_CONTEXT = re.compile(
    r"(?:曾(?:任|做过)|做过|当过|担任|任职|从.{0,16}(?:一路|转入|进入)|"
    r"受过.{0,24}(?:训练|培训)|接受.{0,24}训练|长期从事|专门负责)"
)

_FAMILY_ROLE_MARKERS = (
    "女儿", "儿子", "妻子", "丈夫", "夫人", "侄女", "侄子", "妹妹",
    "弟弟", "姐姐", "哥哥", "母亲", "父亲", "女婿", "儿媳", "家属",
    "之女", "之子",
)


def _role_skill_match(role_text: str, keywords: tuple[str, ...]) -> bool:
    """Require an occupation cue, not merely a relative's title.

    Identities such as ``议员女儿`` and ``市长妻子`` contain a powerful
    office keyword but do not give the related character that office's
    abilities.  Ignore a keyword when a family marker occurs in its local
    phrase; direct office titles remain eligible.
    """

    for keyword in keywords:
        for match in re.finditer(re.escape(keyword), role_text):
            # Evaluate only the compact role phrase containing the keyword;
            # do not let a following family clause (``进口商、某人的丈夫``)
            # suppress a real occupation.
            left = max(
                role_text.rfind(value, 0, match.start())
                for value in ("、", "，", ",", ";", "；")
            )
            right_candidates = [
                value for value in ("、", "，", ",", ";", "；")
                if (index := role_text.find(value, match.end())) >= 0
            ]
            right = min(right_candidates, key=lambda value: role_text.find(value, match.end())) if right_candidates else ""
            end = role_text.find(right, match.end()) if right else len(role_text)
            phrase = role_text[left + 1:end]
            if any(marker in phrase for marker in _FAMILY_ROLE_MARKERS) or "软肋" in phrase:
                continue
            return True
    return False


def _source_skill_match(source_text: str, keywords: tuple[str, ...]) -> bool:
    """Match a skill only when the source states a concrete experience.

    Character biographies contain many incidental words such as ``安全`` or
    ``调查``.  A source-only ability therefore needs both a skill term and a
    nearby employment/training marker.  Identity/role matches are handled
    separately and remain the primary evidence path.
    """

    if not source_text:
        return False
    # Keep the window small enough that a keyword in a later relationship or
    # plot paragraph cannot accidentally become a biography fact.
    chunks = re.split(r"[\n。！？；]", source_text)
    for chunk in chunks:
        for context in _EXPLICIT_ROLE_CONTEXT.finditer(chunk):
            # A source keyword must be close to the employment/training verb;
            # otherwise a later plot example (for example “借钱给商人”) is
            # not evidence that this character can trade.
            nearby = chunk[max(0, context.start() - 4):context.end() + 10]
            if any(keyword in nearby for keyword in keywords):
                return True
    return False


def _infer_abilities(
    role_text: str,
    source_text: str,
    refs: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    values: list[dict[str, Any]] = []
    for ability_id, keywords, default_level, basis in _ABILITY_RULES:
        role_hit = _role_skill_match(role_text, keywords)
        source_hit = _source_skill_match(source_text, keywords)
        if not role_hit and not source_hit:
            continue
        status = "canon" if role_hit else "inferred"
        level = default_level if role_hit else _downgrade(default_level)
        values.append(
            {
                "abilityId": ability_id,
                "name": ABILITY_BY_ID[ability_id].name,
                "level": level,
                "sourceStatus": status,
                "confidence": 0.95 if role_hit else 0.65,
                "basis": basis if role_hit else "script_experience",
                "sourceRefs": refs,
                "notes": (
                    "由人物身份/职责直接确认。"
                    if role_hit
                    else "由剧本经历或组织语境推定，待人工复核。"
                ),
            }
        )
    # Keep profile payloads bounded and deterministic.  Taxonomy order is the
    # tie-breaker; high-confidence direct roles naturally remain first.
    values.sort(key=lambda value: (-float(value["confidence"]), value["abilityId"]))
    return values[:12]


def _language_section(text: str) -> str:
    match = re.search(
        r"(?:^|\n)\s*#{3,4}\s*演绎语言\s*(.*?)(?=\n\s*#{2,4}\s+|$)",
        text,
        flags=re.S,
    )
    return match.group(1).strip() if match else ""


def _infer_language_style(
    role_text: str,
    source_text: str,
    refs: list[dict[str, Any]],
    *,
    player: bool,
) -> dict[str, Any]:
    if player:
        return empty_language_style("player_defined")
    section = _language_section(source_text)
    text = section or role_text
    style = empty_language_style("canon" if section else "unknown")
    if not section:
        # Only assign a conservative inferred template where the occupation
        # gives a real presentation cue.  All other fields stay null.
        if any(value in role_text for value in ("律师", "法官", "检察官", "银行", "市政", "审计", "书记官")):
            style.update(formality="formal", politeness="medium", directness="direct", verbosity="moderate", pacing="measured", sentenceStyle="numeric", sourceStatus="inferred")
        elif any(value in role_text for value in ("医生", "护士", "工程师", "机械", "质检", "调度", "海关")):
            style.update(formality="technical", politeness="medium", directness="direct", verbosity="moderate", pacing="measured", sentenceStyle="mixed", sourceStatus="inferred")
        elif any(value in role_text for value in ("神父", "主教", "教堂", "宗教")):
            style.update(formality="ceremonial", politeness="high", directness="indirect", verbosity="moderate", pacing="slow", sentenceStyle="metaphorical", sourceStatus="inferred")
        elif any(value in role_text for value in ("记者", "编辑", "报童", "侦探", "调查")):
            style.update(formality="mixed", politeness="medium", directness="questioning", verbosity="moderate", pacing="quick", sentenceStyle="questioning", sourceStatus="inferred")
        elif any(value in role_text for value in ("酒保", "歌厅", "酒馆", "旅馆", "接待", "摊贩", "店主", "街头")):
            style.update(formality="colloquial", politeness="medium", directness="balanced", verbosity="moderate", pacing="quick", sentenceStyle="mixed", sourceStatus="inferred")
        elif any(value in role_text for value in ("首领", "帮派", "收账", "打手", "警署", "执行")):
            style.update(formality="colloquial", politeness="low", directness="direct", verbosity="terse", pacing="quick", sentenceStyle="short", sourceStatus="inferred")
        else:
            return style
    else:
        # Controlled summaries from the actual "演绎语言" prose.  We never
        # copy free-form lines, secrets, or supposed catchphrases into data.
        style["sourceRefs"] = refs
        style["formality"] = "formal" if any(k in text for k in ("正式", "礼貌", "官僚", "法律", "术语")) else "colloquial" if any(k in text for k in ("口语", "俚语", "粗俗", "嘴快", "笑话")) else "mixed"
        style["politeness"] = "high" if any(k in text for k in ("礼貌", "客气", "尊敬")) else "low" if any(k in text for k in ("粗鲁", "不耐烦", "嘲讽")) else "medium"
        style["directness"] = "direct" if any(k in text for k in ("直接", "不绕", "命令", "简短")) else "indirect" if any(k in text for k in ("绕弯", "委婉", "暗示")) else "balanced"
        style["verbosity"] = "terse" if any(k in text for k in ("简短", "少说", "惜字")) else "verbose" if any(k in text for k in ("详细", "长篇", "话多")) else "moderate"
        style["pacing"] = "quick" if any(k in text for k in ("快", "语速快", "嘴快")) else "slow" if any(k in text for k in ("慢", "停顿", "拖长")) else "measured"
        style["sentenceStyle"] = "short" if any(k in text for k in ("短句", "简短")) else "long" if any(k in text for k in ("长句", "复杂句")) else "mixed"
        if any(k in text for k in ("压力", "紧张", "失控")):
            style["pressureShift"] = "压力升高时更倾向于减少铺垫并强化原有表达习惯。"
        style["notes"] = "根据原文演绎语言段落归纳；口头禅和禁忌话题未作无依据扩写。"
    style["sourceRefs"] = refs
    return style


def build_character_traits(
    *,
    character_id: str,
    role: str = "",
    name: str = "",
    attributes: Mapping[str, Any] | None = None,
    source_refs: Sequence[Any] | None = None,
    source_text: str = "",
    player: bool = False,
) -> dict[str, Any]:
    """Compile bounded profile metadata from campaign-backed text."""

    attrs = dict(attributes or {})
    identity = str(attrs.get("identity", ""))
    # Organization membership is context, not a profession.  Keeping it out
    # of the role evidence prevents a generic faction label (for example a
    # gang or workers' association) from granting combat or security skills.
    role_text = " ".join(value for value in (name, role, identity) if value)
    refs = _compact_refs(source_refs)
    text = "\n".join(value for value in (source_text, _source_text(source_refs)) if value)
    return {
        "abilities": [] if player else _infer_abilities(role_text, text, refs),
        "languageStyle": _infer_language_style(role_text, text, refs, player=player),
    }


def ability_catalog_payload() -> list[dict[str, str]]:
    return [
        {
            "abilityId": value.ability_id,
            "name": value.name,
            "domain": value.domain,
            "description": value.description,
        }
        for value in ABILITY_CATALOG
    ]


__all__ = [
    "ABILITY_CATALOG",
    "ABILITY_BY_ID",
    "AbilitySpec",
    "ability_catalog_payload",
    "build_character_traits",
    "empty_language_style",
]
