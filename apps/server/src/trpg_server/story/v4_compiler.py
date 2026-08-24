from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from trpg_server.characters.traits import build_character_traits


CanonLayerId = Literal["C0", "C1", "C2", "G"]
FactStatus = Literal[
    "authoritative_definition",
    "current_state_schema",
    "generation_boundary",
    "scheduled_window",
    "template",
]


class CatalogModel(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")


class SourceReference(CatalogModel):
    title: str = Field(min_length=1)
    level: int = Field(ge=0, le=6)
    source_line: int = Field(ge=1, alias="sourceLine")
    source_end_line: int = Field(ge=1, alias="sourceEndLine")
    source_fingerprint: str = Field(
        min_length=64,
        max_length=64,
        alias="sourceFingerprint",
    )
    excerpt: str = ""


class CanonLayerDefinition(CatalogModel):
    id: CanonLayerId
    title: str = Field(min_length=1)
    description: str = Field(min_length=1)
    source: SourceReference


class CatalogEntry(CatalogModel):
    id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    kind: str = Field(min_length=1)
    canon_layer: CanonLayerId = Field(alias="canonLayer")
    fact_status: FactStatus = Field(alias="factStatus")
    instantiated: bool
    sources: list[SourceReference] = Field(min_length=1)
    attributes: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def generation_templates_are_not_facts(self) -> CatalogEntry:
        if self.canon_layer == "G" and self.instantiated:
            raise ValueError("G-layer catalog entries cannot be instantiated facts")
        return self


class MainlineStateMachine(CatalogModel):
    schema_version: Literal[1] = Field(default=1, alias="schemaVersion")
    history_stage_field: Literal["HISTORY_STAGE"] = Field(
        default="HISTORY_STAGE",
        alias="historyStageField",
    )
    power_stage_prefix: Literal["M"] = Field(default="M", alias="powerStagePrefix")
    source: SourceReference
    rules: list[SourceReference]
    states: list[CatalogEntry]


class V42Catalog(CatalogModel):
    schema_version: Literal[2] = Field(default=2, alias="schemaVersion")
    scenario_id: str = Field(alias="scenarioId", min_length=1)
    scenario_version: Literal["4.2"] = Field(default="4.2", alias="scenarioVersion")
    source_document: str = Field(alias="sourceDocument", min_length=1)
    source_sha256: str = Field(
        min_length=64,
        max_length=64,
        alias="sourceSha256",
    )
    source_line_count: int = Field(ge=1, alias="sourceLineCount")
    canon_layers: list[CanonLayerDefinition] = Field(alias="canonLayers")
    mainline_state_machine: MainlineStateMachine = Field(alias="mainlineStateMachine")
    districts: list[CatalogEntry]
    characters: list[CatalogEntry]
    organizations: list[CatalogEntry]
    locations: list[CatalogEntry]
    affordances: list[CatalogEntry]
    critical_items: list[CatalogEntry] = Field(alias="criticalItems")
    event_seeds: list[CatalogEntry] = Field(alias="eventSeeds")
    documents: list[CatalogEntry]
    timeline: list[CatalogEntry]
    side_quests: list[CatalogEntry] = Field(alias="sideQuests")
    generation_policies: list[CatalogEntry] = Field(alias="generationPolicies")
    audit_rules: list[CatalogEntry] = Field(alias="auditRules")

    @model_validator(mode="after")
    def validate_catalog_invariants(self) -> V42Catalog:
        if {layer.id for layer in self.canon_layers} != {"C0", "C1", "C2", "G"}:
            raise ValueError("catalog must define C0, C1, C2, and G canon layers")

        collections = (
            self.mainline_state_machine.states,
            self.districts,
            self.characters,
            self.organizations,
            self.locations,
            self.affordances,
            self.critical_items,
            self.event_seeds,
            self.documents,
            self.timeline,
            self.side_quests,
            self.generation_policies,
            self.audit_rules,
        )
        identifiers = [entry.id for values in collections for entry in values]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("catalog entry ids must be globally unique")
        return self


@dataclass(frozen=True, slots=True)
class Heading:
    line: int
    level: int
    title: str


HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
BOLD_FIELD_RE = re.compile(r"\*\*(.+?)\*\*：(.*?)(?=\s*\*\*[^*]+\*\*：|$)")
CANON_LAYER_RE = re.compile(r"^\d+\.\s+\*\*(C[012]|G)｜(.+?)\*\*：(.*)$")


class MarkdownSource:
    def __init__(self, text: str) -> None:
        self.text = text
        self.lines = text.splitlines()
        self.headings = [
            Heading(line=index, level=len(match.group(1)), title=match.group(2))
            for index, value in enumerate(self.lines, start=1)
            if (match := HEADING_RE.match(value)) is not None
        ]

    def heading(self, title: str) -> Heading:
        try:
            return next(value for value in self.headings if value.title == title)
        except StopIteration as error:
            raise ValueError(f"missing required heading: {title}") from error

    def end_line(self, heading: Heading) -> int:
        return next(
            (
                value.line - 1
                for value in self.headings
                if value.line > heading.line and value.level <= heading.level
            ),
            len(self.lines),
        )

    def children(
        self,
        parent_title: str,
        *,
        level: int,
        pattern: re.Pattern[str],
    ) -> list[tuple[Heading, re.Match[str]]]:
        parent = self.heading(parent_title)
        end = self.end_line(parent)
        result: list[tuple[Heading, re.Match[str]]] = []
        for heading in self.headings:
            if heading.line <= parent.line or heading.line > end or heading.level != level:
                continue
            match = pattern.fullmatch(heading.title)
            if match is not None:
                result.append((heading, match))
        return result

    def top_level(self, pattern: re.Pattern[str]) -> list[tuple[Heading, re.Match[str]]]:
        result: list[tuple[Heading, re.Match[str]]] = []
        for heading in self.headings:
            if heading.level != 1:
                continue
            match = pattern.fullmatch(heading.title)
            if match is not None:
                result.append((heading, match))
        return result

    def source(self, heading: Heading) -> SourceReference:
        return self.source_range(
            title=heading.title,
            level=heading.level,
            start_line=heading.line,
            end_line=self.end_line(heading),
        )

    def source_range(
        self,
        *,
        title: str,
        level: int,
        start_line: int,
        end_line: int,
    ) -> SourceReference:
        block_lines = self.lines[start_line - 1:end_line]
        block = "\n".join(block_lines)
        excerpt = " ".join(
            value.strip()
            for value in block_lines[1:]
            if value.strip() and not value.lstrip().startswith("#")
        )[:700]
        return SourceReference(
            title=title,
            level=level,
            sourceLine=start_line,
            sourceEndLine=end_line,
            sourceFingerprint=hashlib.sha256(block.encode("utf-8")).hexdigest(),
            excerpt=excerpt,
        )

    def block(self, heading: Heading) -> str:
        return "\n".join(self.lines[heading.line - 1:self.end_line(heading)])


def _fields(block: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for line in block.splitlines():
        for match in BOLD_FIELD_RE.finditer(line.strip()):
            result[match.group(1).strip()] = match.group(2).strip().rstrip("。")
    return result


def _split_values(value: str) -> list[str]:
    return [
        item.strip()
        for item in re.split(r"[、，,；;]", value)
        if item.strip()
    ]


def _entry(
    *,
    identifier: str,
    title: str,
    kind: str,
    canon_layer: CanonLayerId,
    fact_status: FactStatus,
    instantiated: bool,
    sources: list[SourceReference],
    attributes: dict[str, Any] | None = None,
) -> CatalogEntry:
    return CatalogEntry(
        id=identifier,
        title=title,
        kind=kind,
        canonLayer=canon_layer,
        factStatus=fact_status,
        instantiated=instantiated,
        sources=sources,
        attributes=attributes or {},
    )


def _compile_canon_layers(source: MarkdownSource) -> list[CanonLayerDefinition]:
    start = source.heading("V4.1 Canon优先级（AI/GM必须遵守）")
    end_line = source.end_line(start)
    result: list[CanonLayerDefinition] = []
    for line_number in range(start.line + 1, end_line + 1):
        line = source.lines[line_number - 1]
        match = CANON_LAYER_RE.match(line)
        if match is None:
            continue
        layer_id, title, description = match.groups()
        result.append(CanonLayerDefinition(
            id=layer_id,
            title=title,
            description=description,
            source=source.source_range(
                title=f"{layer_id}｜{title}",
                level=0,
                start_line=line_number,
                end_line=line_number,
            ),
        ))
    return result


def _compile_mainline(source: MarkdownSource) -> MainlineStateMachine:
    states: list[CatalogEntry] = []
    pattern = re.compile(r"第四编·第(\d+)幕：(.+)")
    for heading, match in source.top_level(pattern):
        number = int(match.group(1))
        title = match.group(2)
        goal = title.split("——", 1)[1] if "——" in title else title
        states.append(_entry(
            identifier=f"MAINLINE-M{number}",
            title=title,
            kind="mainline_power_stage",
            canon_layer="C0",
            fact_status="authoritative_definition",
            instantiated=True,
            sources=[source.source(heading)],
            attributes={
                "historyStage": number,
                "powerStage": f"M{number}",
                "powerGoal": goal,
                "completionRequiresConfirmedEvidence": True,
                "historyProgressDoesNotGrantPower": True,
            },
        ))
    state_machine = source.heading("6.1 AI GM唯一主线状态机")
    rules = [source.source(state_machine)]
    for title in (
        "6.1.1 必须长期保存的状态字段",
        "6.1.2 主线强制推进的总原则",
        "6.1.3 什么情况下需要“重新开始”",
        "6.2 九幕硬推进与最坏进入方式",
    ):
        rules.append(source.source(source.heading(title)))
    return MainlineStateMachine(
        source=source.source(state_machine),
        rules=rules,
        states=states,
    )


def _compile_districts(source: MarkdownSource) -> list[CatalogEntry]:
    values: list[CatalogEntry] = []
    for heading, match in source.children(
        "9. 灰港的七个城区与它们真正争夺的东西",
        level=2,
        pattern=re.compile(r"9\.(\d+)\s+(.+)"),
    ):
        number, name = match.groups()
        if int(number) > 7:
            continue
        values.append(_entry(
            identifier=f"DISTRICT-{int(number):02d}",
            title=name,
            kind="district",
            canon_layer="C0",
            fact_status="authoritative_definition",
            instantiated=True,
            sources=[source.source(heading)],
            attributes={"ordinal": int(number)},
        ))
    return values


def _compile_characters(source: MarkdownSource) -> list[CatalogEntry]:
    profile_by_name: dict[str, tuple[Heading, str]] = {}
    for heading, match in source.children(
        "第二编：核心人物人格圣经",
        level=2,
        pattern=re.compile(r"(\d+)\.\s+(.+)"),
    ):
        profile_by_name[match.group(2)] = (heading, "core")
    for heading, match in source.children(
        "第七编：次级人物库——让每条街都有自己的意志",
        level=2,
        pattern=re.compile(r"7\.(\d+)\s+(.+)"),
    ):
        profile_by_name[match.group(2)] = (heading, "secondary")

    characters: list[CatalogEntry] = []
    for relationship_heading, match in source.children(
        "第三十六编：139名NPC全关系网——硬关系与接触渠道分离",
        level=2,
        pattern=re.compile(r"36\.\d+｜(P\d{3})\s+(.+)"),
    ):
        character_id, name = match.groups()
        if name not in profile_by_name:
            raise ValueError(f"missing character profile for {character_id} {name}")
        profile_heading, profile_kind = profile_by_name[name]
        relationship_fields = _fields(source.block(relationship_heading))
        profile_fields = _fields(source.block(profile_heading))
        profile_source = source.source(profile_heading)
        relationship_source = source.source(relationship_heading)
        attributes = {
            "characterId": character_id,
            "profileKind": profile_kind,
            "age": profile_fields.get("年龄"),
            "identity": relationship_fields.get("身份", profile_fields.get("身份")),
            "primaryDistrict": relationship_fields.get(
                "主要活动区",
                profile_fields.get("常见区域"),
            ),
            "organizationNetwork": relationship_fields.get("明确组织/工作网络"),
            "driverAnchor": relationship_fields.get("核心驱动力锚点"),
            "weaknessAnchor": relationship_fields.get("已知弱点锚点"),
            "relationshipAxesRequireEvents": True,
        }
        attributes.update(
            build_character_traits(
                character_id=character_id,
                name=name,
                role=str(attributes.get("identity") or ""),
                attributes=attributes,
                source_refs=[profile_source, relationship_source],
                source_text=source.block(profile_heading),
            )
        )
        characters.append(_entry(
            identifier=f"CHARACTER-{character_id}",
            title=name,
            kind="character",
            canon_layer="C0",
            fact_status="authoritative_definition",
            instantiated=True,
            sources=[profile_source, relationship_source],
            attributes=attributes,
        ))
    return characters


def _compile_organizations(source: MarkdownSource) -> list[CatalogEntry]:
    values: list[CatalogEntry] = []
    for heading, match in source.children(
        "第三编：势力不是阵营——组织人格与内部政治",
        level=2,
        pattern=re.compile(r"3\.(\d+)\s+(.+)"),
    ):
        number, name = match.groups()
        if int(number) > 16:
            continue
        values.append(_entry(
            identifier=f"ORGANIZATION-{int(number):02d}",
            title=name,
            kind="organization",
            canon_layer="C0",
            fact_status="authoritative_definition",
            instantiated=True,
            sources=[source.source(heading)],
            attributes={"ordinal": int(number), "singleAttitudeForbidden": True},
        ))
    return values


def _compile_locations(source: MarkdownSource) -> tuple[list[CatalogEntry], list[CatalogEntry]]:
    locations: list[CatalogEntry] = []
    affordances: list[CatalogEntry] = []
    for heading, match in source.children(
        "第三十九编：84个地点运营状态卡（按地点类型重建）",
        level=2,
        pattern=re.compile(r"39\.\d+｜(L\d{3})\s+(.+)"),
    ):
        location_id, name = match.groups()
        fields = _fields(source.block(heading))
        location_type = fields.get("类型", "未分类")
        function = fields.get("功能", "")
        resources = _split_values(fields.get("主要资源", ""))
        location_source = source.source(heading)
        locations.append(_entry(
            identifier=f"LOCATION-{location_id}",
            title=name,
            kind="location",
            canon_layer="C0",
            fact_status="authoritative_definition",
            instantiated=True,
            sources=[location_source],
            attributes={
                "locationId": location_id,
                "locationType": location_type,
                "function": function,
                "resources": resources,
                "knownManagementInterface": fields.get("已知常驻/管理接口"),
                "controlDimensions": fields.get("真正需要判断的控制维度"),
                "recordTypes": fields.get("正常会产生的记录类型"),
                "forbiddenInference": fields.get("禁止套用的推理"),
                "specialStructure": fields.get("已确认的隐藏/特殊结构"),
                "autonomyUpdate": fields.get("世界自治更新"),
            },
        ))
        action_kinds = _affordance_kinds(name, location_type, function)
        affordances.append(_entry(
            identifier=f"AFFORDANCE-{location_id}",
            title=f"{name}的日常生成边界",
            kind="world_affordance",
            canon_layer="C2",
            fact_status="generation_boundary",
            instantiated=True,
            sources=[location_source],
            attributes={
                "locationId": location_id,
                "suggestedActionKinds": action_kinds,
                "resourceCategories": resources,
                "storyImpactCeiling": "soft",
                "temporaryEntityKinds": _temporary_entity_kinds(location_type),
                "notActionWhitelist": True,
                "requiresValidatedInstantiationEvent": True,
            },
        ))
    return locations, affordances


def _affordance_kinds(name: str, location_type: str, function: str) -> list[str]:
    text = f"{name} {location_type} {function}"
    kinds = {"observe", "search", "social"}
    if any(token in text for token in ("商业", "市场", "商店", "酒馆", "餐厅", "旅馆", "酒店", "赌场")):
        kinds.update({"commerce", "work"})
    if any(token in text for token in ("餐", "厨房", "酒馆", "市场", "食品", "旅馆", "酒店")):
        kinds.add("meal")
    if any(token in text for token in ("工厂", "码头", "仓", "矿", "车站", "工会", "工业")):
        kinds.add("work")
    if any(token in text for token in ("车站", "码头", "港", "道路", "桥")):
        kinds.add("travel")
    if any(token in text for token in ("医院", "学校", "法院", "警", "市政", "登记", "教堂", "公共")):
        kinds.add("inquire")
    if any(token in text for token in ("住宅", "旅馆", "酒店", "宿舍", "白鹭屋")):
        kinds.add("rest")
    return sorted(kinds)


def _temporary_entity_kinds(location_type: str) -> list[str]:
    if "商业" in location_type:
        return ["customer", "worker", "vendor"]
    if any(token in location_type for token in ("公共", "政府", "司法", "医疗")):
        return ["visitor", "worker"]
    return ["visitor", "worker"]


def _compile_critical_items(source: MarkdownSource) -> list[CatalogEntry]:
    values: list[CatalogEntry] = []
    for heading, match in source.children(
        "第三十四编：30件核心关键道具（已逐项核验）",
        level=2,
        pattern=re.compile(r"34\.\d+｜(GH-S\d{2})\s+(.+)"),
    ):
        item_id, name = match.groups()
        fields = _fields(source.block(heading))
        values.append(_entry(
            identifier=f"CRITICAL-ITEM-{item_id}",
            title=name,
            kind="critical_item_definition",
            canon_layer="C0",
            fact_status="authoritative_definition",
            instantiated=True,
            sources=[source.source(heading)],
            attributes={
                "itemId": item_id,
                "criticality": "major_key",
                "storyBindingPolicy": "script_defined_only",
                "mutableStateCanonLayer": "C1",
                "class": fields.get("等级/类别"),
                "originAndStartingLocation": fields.get("正常起始位置/产生链"),
                "nominalAndActualControl": fields.get("名义/实际控制"),
                "validators": fields.get("谁能核验"),
                "reliableUse": fields.get("可靠用途"),
                "forbiddenInference": fields.get("明确不能推出"),
                "versionPolicy": fields.get("时间与版本"),
                "stateRecordFormat": fields.get("状态记录格式"),
            },
        ))
    return values


def _compile_event_seeds(source: MarkdownSource) -> list[CatalogEntry]:
    values: list[CatalogEntry] = []
    for heading, match in source.children(
        "第四十编：微观世界事件生成器——事件种子不是Canon",
        level=2,
        pattern=re.compile(r"40\.(\d+)｜(.+)"),
    ):
        number, title = match.groups()
        values.append(_entry(
            identifier=f"EVENT-SEED-{int(number):03d}",
            title=title,
            kind="world_event_seed",
            canon_layer="G",
            fact_status="template",
            instantiated=False,
            sources=[source.source(heading)],
            attributes={
                "ordinal": int(number),
                "requiresInstantiationEvent": True,
                "defaultStoryImpact": "routine",
            },
        ))
    return values


def _compile_documents(source: MarkdownSource) -> list[CatalogEntry]:
    values: list[CatalogEntry] = []
    for heading, match in source.children(
        "第四十一编：文书模板库——模板绝不等于真实文件",
        level=2,
        pattern=re.compile(r"41\.\d+｜(D\d{2})\s+(.+)"),
    ):
        document_id, title = match.groups()
        fields = _fields(source.block(heading))
        values.append(_entry(
            identifier=f"DOCUMENT-TEMPLATE-{document_id}",
            title=title,
            kind="document_template",
            canon_layer="G",
            fact_status="template",
            instantiated=False,
            sources=[source.source(heading)],
            attributes={
                "documentTemplateId": document_id,
                "normalCreatorAndCustody": fields.get("正常创建者/保存链"),
                "minimumFields": fields.get("最低字段"),
                "proofBoundary": fields.get("证明边界"),
                "instantiationRule": fields.get("实例化规则"),
                "requiresInstantiationEvent": True,
            },
        ))
    return values


def _compile_timeline(source: MarkdownSource) -> list[CatalogEntry]:
    values: list[CatalogEntry] = []
    for heading, match in source.children(
        "第四十五编：唯一后台时间轴——60个月与36个月主轴对齐",
        level=2,
        pattern=re.compile(r"45\.(\d+)｜(.+)"),
    ):
        month = int(match.group(1))
        title = match.group(2)
        fixed = month <= 36
        values.append(_entry(
            identifier=f"TIMELINE-MONTH-{month:02d}",
            title=title,
            kind="world_timeline_window",
            canon_layer="C0" if fixed else "G",
            fact_status="scheduled_window" if fixed else "template",
            instantiated=False,
            sources=[source.source(heading)],
            attributes={
                "campaignMonth": month,
                "fixedCalendarWindow": fixed,
                "eventHasOccurred": False,
                "requiresWorldTimeSettlement": True,
                "postMainlineConditionalSlot": not fixed,
            },
        ))
    return values


def _compile_side_quests(source: MarkdownSource) -> list[CatalogEntry]:
    values: list[CatalogEntry] = []
    for heading, match in source.children(
        "第八编：支线事件库——支线必须反过来塑造主线",
        level=2,
        pattern=re.compile(r"支线(\d{3})：(.+)"),
    ):
        number, title = match.groups()
        fields = _fields(source.block(heading))
        values.append(_entry(
            identifier=f"SIDE-QUEST-SQ{number}",
            title=title,
            kind="side_quest_protocol",
            canon_layer="G",
            fact_status="template",
            instantiated=False,
            sources=[source.source(heading)],
            attributes={
                "questId": f"SQ{number}",
                "initialStatus": "DORMANT",
                "worldEventProperty": fields.get("世界事件属性"),
                "prerequisites": fields.get("前置条件"),
                "expiration": fields.get("失效条件"),
                "completionRewardBoundary": fields.get("完成奖励"),
                "mainlineRelationship": fields.get("主线关系"),
                "failureOrIgnore": fields.get("失败/忽略"),
                "completionCannotFinishActAlone": True,
            },
        ))
    return values


def _compile_policy_entries(
    source: MarkdownSource,
    definitions: tuple[tuple[str, str, CanonLayerId, FactStatus], ...],
    *,
    kind: str,
) -> list[CatalogEntry]:
    values: list[CatalogEntry] = []
    for identifier, title, layer, status in definitions:
        heading = source.heading(title)
        values.append(_entry(
            identifier=identifier,
            title=title,
            kind=kind,
            canon_layer=layer,
            fact_status=status,
            instantiated=layer != "G",
            sources=[source.source(heading)],
        ))
    return values


def compile_v42_markdown(source_path: Path, scenario_id: str) -> V42Catalog:
    text = source_path.read_text(encoding="utf-8")
    source = MarkdownSource(text)
    locations, affordances = _compile_locations(source)
    generation_policies = _compile_policy_entries(
        source,
        (
            ("GENERATION-POLICY-ITEMS", "第三十三编：V4.1关键道具与世界状态协议", "C0", "generation_boundary"),
            ("GENERATION-POLICY-EVENT-SEEDS", "第四十编：微观世界事件生成器——事件种子不是Canon", "G", "template"),
            ("GENERATION-POLICY-DOCUMENTS", "第四十一编：文书模板库——模板绝不等于真实文件", "G", "template"),
            ("GENERATION-POLICY-SOCIAL", "第四十四编：人物社交与收买协议——不再随机分配礼物偏好", "C2", "generation_boundary"),
            ("GENERATION-POLICY-DAILY-LIFE", "第四十七编：城市生活细节的Canon边界", "C2", "generation_boundary"),
        ),
        kind="generation_policy",
    )
    audit_rules = _compile_policy_entries(
        source,
        (
            ("AUDIT-CANON-PRIORITY", "V4.1 Canon优先级（AI/GM必须遵守）", "C0", "authoritative_definition"),
            ("AUDIT-HARD-NEGATIONS", "本次修复的硬性否定规则", "C0", "authoritative_definition"),
            ("AUDIT-GM-CHECKLIST", "第四十八编：GM/AI逻辑检查表", "C0", "authoritative_definition"),
            ("AUDIT-RETRIEVAL-ORDER", "第五十编：AI检索与叙事时的Canon读取顺序", "C0", "authoritative_definition"),
            ("AUDIT-WORLD-WRITEBACK", "第五十一编：世界状态写回协议——避免“莫名其妙多出东西”", "C0", "authoritative_definition"),
            ("AUDIT-DEPRECATIONS", "第五十二编：V4.1修复索引与废止清单", "C0", "authoritative_definition"),
        ),
        kind="audit_rule",
    )
    return V42Catalog(
        scenarioId=scenario_id,
        sourceDocument=source_path.name,
        sourceSha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
        sourceLineCount=len(source.lines),
        canonLayers=_compile_canon_layers(source),
        mainlineStateMachine=_compile_mainline(source),
        districts=_compile_districts(source),
        characters=_compile_characters(source),
        organizations=_compile_organizations(source),
        locations=locations,
        affordances=affordances,
        criticalItems=_compile_critical_items(source),
        eventSeeds=_compile_event_seeds(source),
        documents=_compile_documents(source),
        timeline=_compile_timeline(source),
        sideQuests=_compile_side_quests(source),
        generationPolicies=generation_policies,
        auditRules=audit_rules,
    )


def write_v42_catalog(
    source: Path,
    output: Path,
    scenario_id: str,
) -> V42Catalog:
    catalog = compile_v42_markdown(source, scenario_id)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(catalog.model_dump(by_alias=True), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return catalog


def load_v42_catalog(path: Path) -> V42Catalog:
    return V42Catalog.model_validate_json(path.read_text(encoding="utf-8"))
