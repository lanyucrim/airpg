from __future__ import annotations

import json
import re
import sys
from collections import defaultdict
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CAMPAIGN = ROOT / "content" / "campaigns" / "gray-harbor"
ATLAS_DIR = CAMPAIGN / "characters-atlas"
SERVER_SRC = ROOT / "apps" / "server" / "src"
if str(SERVER_SRC) not in sys.path:
    sys.path.insert(0, str(SERVER_SRC))

from trpg_server.characters.traits import (  # noqa: E402
    ABILITY_BY_ID,
    ability_catalog_payload,
    build_character_traits,
)
from trpg_server.characters.inventory import ensure_inventory_containers  # noqa: E402
from trpg_server.items.catalog import load_item_atlas  # noqa: E402


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def load_initial_item_instances() -> tuple[str, list[dict]]:
    """Load canonical initial instances for validating character item references."""

    seed = read_json(CAMPAIGN / "items.json")
    atlas_file = seed.get("atlasFile")
    if not isinstance(atlas_file, str):
        raise ValueError("items.json must declare atlasFile")
    relative_atlas_path = Path(atlas_file)
    if relative_atlas_path.is_absolute() or ".." in relative_atlas_path.parts:
        raise ValueError("items.json atlasFile must stay inside the campaign")
    seed_instances = seed.get("instances")
    if not isinstance(seed_instances, list):
        raise ValueError("items.json must contain an instances list")
    atlas = load_item_atlas(CAMPAIGN / relative_atlas_path)
    atlas_instances = [dict(value) for value in atlas.instances]
    if seed_instances != atlas_instances:
        raise ValueError("items.json instances must exactly mirror the item atlas")
    return relative_atlas_path.as_posix(), atlas_instances


def norm(value: str) -> str:
    return re.sub(r"[^\w\u4e00-\u9fff]", "", value or "").lower()


def source_ref(source: dict) -> dict:
    return {
        "title": source.get("title"),
        "level": source.get("level"),
        "sourceLine": source.get("sourceLine"),
        "sourceEndLine": source.get("sourceEndLine"),
        "sourceFingerprint": source.get("sourceFingerprint"),
        "status": "canon",
    }


def extract_section(text: str, heading: str) -> str:
    marker = f"**{heading}**"
    start = text.find(marker)
    if start < 0:
        return ""
    body = text[start + len(marker):]
    next_heading = re.search(r"\n\s*\*\*[^*]+\*\*", body)
    if next_heading:
        body = body[:next_heading.start()]
    return body.strip(" ：:\n")


def original_character_section(document: str, title: str) -> tuple[str, str]:
    """Return the full original character block and its daily-routine subsection."""
    headings = list(re.finditer(r"^##\s+[^\n]+$", document, re.M))
    title_key = norm(title)
    for index, match in enumerate(headings):
        heading = match.group(0)
        if title_key not in norm(heading):
            continue
        end = headings[index + 1].start() if index + 1 < len(headings) else len(document)
        block = document[match.start():end].strip()
        routine_match = re.search(r"^###\s+日常行动轨迹\s*$", block, re.M)
        routine = ""
        if routine_match:
            routine_end = re.search(r"^###\s+|^##\s+", block[routine_match.end():], re.M)
            routine_body_end = routine_match.end() + (routine_end.start() if routine_end else len(block))
            routine = block[routine_match.end():routine_body_end].strip()
        return block, routine
    return "", ""


def location_map(atlas: dict) -> tuple[dict, dict, set[str]]:
    all_locations = {}
    names = {}
    for loc in atlas.get("locations", []):
        all_locations[loc["id"]] = loc
        names[norm(loc.get("name", ""))] = loc["id"]
        for child in loc.get("structure", []):
            all_locations[child["id"]] = child
            names[norm(child.get("name", ""))] = child["id"]
    return all_locations, names, {s["id"] for s in atlas.get("streets", [])}


def build_runtime_mapping(catalog_chars: list[dict], runtime_chars: list[dict]) -> dict[str, str]:
    catalog_ids = {
        str(character["attributes"]["characterId"])
        for character in catalog_chars
    }
    result: dict[str, str] = {}
    claimed_catalog_ids: set[str] = set()
    for runtime in runtime_chars:
        rid = runtime["id"]
        if rid == "protagonist":
            result[rid] = rid
            continue
        catalog_id = runtime.get("catalogCharacterId")
        if catalog_id is None:
            result[rid] = rid
            continue
        if catalog_id not in catalog_ids:
            raise ValueError(f"unknown catalogCharacterId: {rid}->{catalog_id}")
        if catalog_id in claimed_catalog_ids:
            raise ValueError(f"duplicate catalogCharacterId binding: {catalog_id}")
        claimed_catalog_ids.add(catalog_id)
        result[rid] = catalog_id
    return result


def atlas_location_for_runtime(runtime_location: str | None) -> str | None:
    return {
        "white_heron_ground_floor": "loc_5_1_1__1",
        "white_heron_kitchen": "loc_5_1_1__2",
        "white_heron_second_floor": "loc_5_1_1__3",
        "white_heron_third_floor": "loc_5_1_1__4",
    }.get(runtime_location)


def residence_for(pid: str, runtime: dict | None) -> list[dict]:
    # Only the protagonist/roommate relationship establishes a sleeping place.
    # Other workplace locations remain unknown instead of becoming invented facts.
    if pid in {"protagonist", "P002"}:
        return [{
            "type": "workplace_sleeping",
            "locationId": "loc_5_1_1",
            "structureId": "loc_5_1_1__4",
            "streetIds": ["candle_oak"],
            "certainty": "canon" if pid == "P002" else "inferred",
            "source": {
                "status": "canon" if pid == "P002" else "inferred",
                "reason": "莉亚被剧本明确写为主角室友；主角住处由同一室友关系推定。",
            },
        }]
    return []


def status_record(pid: str, name: str, runtime: dict | None) -> dict:
    mental = {"mood": "未记录", "stress": "未知", "focus": "未知", "sleepDebt": "未知", "notes": ""}
    physical = {"health": "未记录", "fatigue": "未知", "pain": "未知", "hunger": "未知", "externalInjuries": [], "mobility": "未知"}
    status = "unknown"
    if pid in {"protagonist", "P001", "P004", "P002"}:
        mental.update({"stress": "高", "focus": "受债务危机牵引"})
        physical.update({"health": "稳定", "fatigue": "中等", "mobility": "正常"})
        status = "inferred"
    elif pid in {"P003", "P006"}:
        mental.update({"stress": "中等", "focus": "工作优先"})
        physical.update({"health": "稳定", "fatigue": "中等", "mobility": "正常"})
        status = "inferred"
    elif pid in {"P011", "iron_hook_collector_one", "iron_hook_collector_two"}:
        mental.update({"stress": "低", "focus": "执行任务", "mood": "警觉"})
        physical.update({"health": "稳定", "fatigue": "低", "mobility": "正常"})
        status = "inferred"
    return {
        "id": f"state_{pid}",
        "characterId": pid,
        "baseline": {
            "mental": mental.copy(),
            "physical": physical.copy(),
            "sourceStatus": status,
            "source": "campaign_start_context" if status == "inferred" else "unknown",
        },
        "current": {
            "mental": mental,
            "physical": physical,
            "asOfWorldTime": "海历621年10月17日23:00",
            "sourceStatus": status,
            "sourceEvents": [],
        },
        "dailyStateLog": [],
        "dailyStatePolicy": {
            "aiMayPropose": True,
            "programMustValidate": True,
            "sourceEventsRequired": True,
            "fixedSchedule": False,
        },
    }


def parse_catalog_relations(catalog_chars: list[dict]) -> list[dict]:
    relations: list[dict] = []
    seen = set()
    for character in catalog_chars:
        pid = character["attributes"]["characterId"]
        sources = character.get("sources", [])
        if len(sources) < 2:
            continue
        excerpt = sources[1].get("excerpt", "")
        hard = excerpt
        if "**硬关系（Canon）**" in excerpt:
            hard = excerpt.split("**硬关系（Canon）**", 1)[1]
        if "**接触渠道" in hard:
            hard = hard.split("**接触渠道", 1)[0]
        relation_pattern = re.compile(
            r"-\s*(P\d{3})\s+\*\*([^*]+)\*\*：(.*?)(?=\s+-\s*P\d{3}\s+\*\*|\s+\*\*接触渠道|\s*$)",
            re.S,
        )
        for target, label, detail in relation_pattern.findall(hard):
            key = (pid, target, "hard")
            if key in seen:
                continue
            seen.add(key)
            target_name, _, relation_label = label.partition("｜")
            relation_label = relation_label.strip()
            relations.append({
                "id": f"rel_{pid}_{target}_hard",
                "subjectId": pid,
                "objectId": target,
                "targetName": target_name.strip(),
                "relationType": relation_label or "hard_relation",
                "relationSubtype": None,
                "relationLabel": relation_label or None,
                "description": detail.strip(),
                "axes": {"closeness": None, "respect": None, "fear": None, "dependence": None, "suspicion": None, "debt": None},
                "affinity": {"value": None, "scale": "-100..100", "layer": "design_projection", "trend": "unknown"},
                "stage": "unknown",
                "canonStatus": "canon",
                "milestones": [],
                "developmentHooks": [],
                "sourceEvents": [],
                "source": source_ref(sources[1]),
            })
        contact = ""
        if "**接触渠道" in excerpt:
            contact = excerpt.split("**接触渠道", 1)[1]
        if "**关系更新规则" in contact:
            contact = contact.split("**关系更新规则", 1)[0]
        for target, name in re.findall(r"(P\d{3})\s+([^；。]+)", contact):
            key = (pid, target, "contact")
            if key in seen:
                continue
            seen.add(key)
            relations.append({
                "id": f"rel_{pid}_{target}_contact",
                "subjectId": pid,
                "objectId": target,
                "relationType": "接触渠道",
                "relationSubtype": "work_or_district_contact",
                "description": f"{name.strip()}仅被列为工作或街区接触渠道，不据此推定亲近、共享秘密或互相效忠。",
                "axes": {"closeness": None, "respect": None, "fear": None, "dependence": None, "suspicion": None, "debt": None},
                "affinity": {"value": None, "scale": "-100..100", "layer": "design_projection", "trend": "unknown"},
                "stage": "unknown",
                "canonStatus": "canon_contact_only",
                "milestones": [],
                "developmentHooks": [],
                "sourceEvents": [],
                "source": source_ref(sources[1]),
            })
    return relations


def normalize_runtime_relationships(raw: list[dict], runtime_to_pid: dict[str, str]) -> list[dict]:
    result = []
    for rel in raw:
        subject = runtime_to_pid.get(rel["subjectId"], rel["subjectId"])
        obj = runtime_to_pid.get(rel["objectId"], rel["objectId"])
        if not subject.startswith("P") and subject != "protagonist":
            continue
        if not obj.startswith("P") and obj != "protagonist":
            continue
        favor = rel.get("favor")
        trust = rel.get("trust")
        affinity = None
        if favor is not None or trust is not None:
            affinity = max(-100, min(100, ((favor or 0) + (trust or 0)) * 10))
        result.append({
            "id": f"rel_{subject}_{obj}_initial",
            "subjectId": subject,
            "objectId": obj,
            "relationType": "initial_runtime_projection",
            "relationSubtype": None,
            "description": "现有 relationships.json 的开局关系投影。",
            "sourceProjection": rel,
            "axes": {
                "closeness": favor,
                "respect": rel.get("respect"),
                "fear": rel.get("fear"),
                "dependence": None,
                "suspicion": rel.get("suspicion"),
                "debt": None,
            },
            "affinity": {"value": affinity, "scale": "-100..100", "layer": "design_projection", "trend": "stable" if affinity is not None else "unknown"},
            "stage": "unknown",
            "canonStatus": "canon_runtime_projection",
            "milestones": [],
            "developmentHooks": [],
            "sourceEvents": [],
            "source": {"status": "canon", "file": "relationships.json"},
        })
    return result


def routine_record(pid: str, character: dict, runtime: dict | None, schedules: list[dict], location_ids: set[str], original_routine: str = "") -> dict:
    source_text = (character.get("sources") or [{}])[0].get("excerpt", "")
    narrative = original_routine or extract_section(source_text, "日常行动轨迹")
    candidates = []
    for schedule in schedules:
        if schedule.get("characterId") == (runtime or {}).get("id"):
            mapped = atlas_location_for_runtime(schedule.get("locationId"))
            if mapped and mapped in location_ids:
                candidates.append({
                    "activity": "现有日程中的候选活动地点",
                    "locationIds": [mapped],
                    "timeWindows": [{"startMinute": schedule["startMinute"], "endMinute": schedule["endMinute"]}],
                    "purpose": "runtime_schedule_seed",
                    "companions": [],
                    "triggers": [],
                    "avoidConditions": [],
                    "likelihood": "medium",
                    "visibility": schedule.get("availability", "unknown"),
                    "variability": "high",
                    "sourceScheduleId": schedule["id"],
                    "isFixed": False,
                })
    return {
        "id": f"routine_{pid}",
        "characterId": pid,
        "routineNarrative": narrative,
        "candidateActivities": candidates,
        "routineConstraints": {
            "aiDriven": True,
            "programValidationOnly": True,
            "mustUseAtlasTravelTime": True,
            "noTeleportation": True,
            "ordinaryActionsCannotAdvanceMajorPlot": True,
        },
        "plotOverrideSlots": [],
        "sourceStatus": "canon" if narrative or candidates else "unknown",
    }


def profile_md(profile: dict) -> str:
    canon = profile["canonProfile"]
    attrs = canon.get("attributes", {})
    lines = [f"## {profile['id']}｜{profile['name']}", "", f"- 类型：`{profile['characterType']}`", f"- 编译状态：`{canon.get('factStatus', 'unknown')}`", f"- Canon 层：`{canon.get('canonLayer', 'unknown')}`", f"- 身份：{attrs.get('identity') or profile.get('runtimeRecord', {}).get('role') or '未记录'}", f"- 主要区域：{attrs.get('primaryDistrict') or '未记录'}"]
    abilities = profile.get("abilities", [])
    lines.extend(["", "### 能力", ""])
    if abilities:
        for ability in abilities:
            lines.append(
                f"- `{ability['abilityId']}`：{ability['name']} / "
                f"{ability.get('level') or '未定'}（{ability.get('sourceStatus', 'unknown')}）"
            )
    else:
        lines.append("- 未有足够剧本依据，暂不列入能力候选。")
    style = profile.get("languageStyle", {})
    lines.extend([
        "",
        "### 语言风格",
        "",
        f"- 状态：`{style.get('sourceStatus', 'unknown')}`；正式度：{style.get('formality') or '未定'}；直接程度：{style.get('directness') or '未定'}；语速：{style.get('pacing') or '未定'}；句式：{style.get('sentenceStyle') or '未定'}",
        f"- 压力变化：{style.get('pressureShift') or '未记录'}",
    ])
    if profile.get("runtimeCharacterId"):
        lines.append(f"- 运行时角色 ID：`{profile['runtimeCharacterId']}`")
    if profile.get("residences"):
        for residence in profile["residences"]:
            lines.append(f"- 住处：`{residence['locationId']}` / `{residence.get('structureId')}`（{residence['certainty']}）")
    else:
        lines.append("- 住处：未确认")
    original_routine = canon.get("originalDailyRoutine", "")
    if original_routine:
        lines.extend(["", "### 原稿日常行动轨迹", "", original_routine])
    if canon.get("sources"):
        lines.append("- 来源：" + "; ".join(f"{s.get('title')}（第{s.get('sourceLine')}行）" for s in canon["sources"]))
    for source in canon.get("sources", []):
        excerpt = source.get("excerpt")
        if excerpt:
            lines.extend(["", f"### 来源摘录：{source.get('title')}", "", excerpt])
    return "\n".join(lines)


def main() -> None:
    catalog = read_json(CAMPAIGN / "v4.2-catalog.json")
    runtime_pack = read_json(CAMPAIGN / "characters.json")
    manifest = read_json(CAMPAIGN / "manifest.json")
    raw_relationships = read_json(CAMPAIGN / "relationships.json").get("relationships", [])
    containers = read_json(CAMPAIGN / "containers.json").get("containers", [])
    item_atlas_file, items = load_initial_item_instances()
    schedules = read_json(CAMPAIGN / "schedules.json").get("schedules", [])
    atlas = read_json(CAMPAIGN / "atlas" / "location-atlas.json")
    source_document_path = ROOT / manifest["sourceDocument"]
    source_document = source_document_path.read_text(encoding="utf-8") if source_document_path.exists() else ""
    location_ids, _, street_ids = location_map(atlas)
    catalog_chars = catalog["characters"]
    runtime_chars = runtime_pack["characters"]
    runtime_to_pid = build_runtime_mapping(catalog_chars, runtime_chars)
    pid_to_runtime = {
        pid: rid
        for rid, pid in runtime_to_pid.items()
        if rid != "protagonist" and pid.startswith("P")
    }
    runtime_by_id = {c["id"]: c for c in runtime_chars}

    profiles = []
    for character in catalog_chars:
        attrs = character.get("attributes", {})
        pid = attrs.get("characterId")
        rid = pid_to_runtime.get(pid)
        if rid is None and character.get("instantiated") and character.get("canonLayer") != "G":
            rid = f"catalog_{str(pid).lower()}"
        runtime = runtime_by_id.get(rid) if rid else None
        runtime_location = atlas_location_for_runtime(runtime.get("locationId")) if runtime else None
        original_section, original_routine = original_character_section(source_document, character.get("title", ""))
        current_location = None
        if runtime_location:
            atlas_loc = location_ids.get(runtime_location, {})
            current_location = {
                "locationId": runtime_location,
                "streetIds": atlas_loc.get("streetIds", []),
                "sourceStatus": "canon",
            }
        record = {
            "id": pid,
            "runtimeCharacterId": rid,
            "name": character.get("title"),
            "characterType": "npc",
            "canonProfile": {
                "canonLayer": character.get("canonLayer"),
                "factStatus": character.get("factStatus"),
                "instantiated": character.get("instantiated", False),
                "attributes": attrs,
                "sources": character.get("sources", []),
                "originalScriptSection": original_section,
                "originalDailyRoutine": original_routine,
                "runtimeRecord": runtime,
            },
            "supplementalProfile": {
                "status": "inferred_fields_are_explicitly_marked",
                "notes": "本资料层补充不覆盖 Canon，也不自动写入运行时事实。",
            },
            **build_character_traits(
                character_id=pid,
                name=character.get("title", ""),
                role=str(attrs.get("identity", "")),
                attributes=attrs,
                source_refs=character.get("sources", []),
                source_text=original_section,
            ),
            "currentLocation": current_location,
            "residences": residence_for(pid, runtime),
            "inventoryRef": f"inventory_{pid}",
            "stateRef": f"state_{pid}",
            "routineRef": f"routine_{pid}",
            "relationshipRefs": [],
            "plotOverrideSlots": [],
            "visibility": {"public": {"name": True, "identity": True}, "gmOnly": {"sources": True, "privateNotes": True, "secrets": True, "decisionProfile": True}},
            "provenance": [source_ref(s) for s in character.get("sources", [])],
        }
        profiles.append(record)

    protagonist = next(c for c in runtime_chars if c["id"] == "protagonist")
    protagonist_traits = build_character_traits(
        character_id="protagonist",
        name=protagonist["name"],
        role=protagonist.get("role", ""),
        player=True,
    )
    profiles.insert(0, {
        "id": "protagonist",
        "runtimeCharacterId": "protagonist",
        "name": protagonist["name"],
        "characterType": "player",
        "canonProfile": {"canonLayer": "C0", "factStatus": "authoritative_runtime_definition", "instantiated": True, "attributes": {}, "sources": [{"status": "canon", "file": "characters.json"}], "runtimeRecord": protagonist},
        "supplementalProfile": {"status": "inferred_fields_are_explicitly_marked", "notes": "不得替玩家决定姓名、性格、情感、职业态度或终局立场。"},
        **protagonist_traits,
        "currentLocation": {"locationId": "loc_5_1_1__1", "streetIds": ["candle_oak"], "sourceStatus": "canon"},
        "residences": residence_for("protagonist", protagonist),
        "inventoryRef": "inventory_protagonist",
        "stateRef": "state_protagonist",
        "routineRef": "routine_protagonist",
        "relationshipRefs": [],
        "plotOverrideSlots": [],
        "visibility": {"public": {"name": True, "role": True}, "gmOnly": {"privateNotes": True}},
        "provenance": [{"status": "canon", "file": "characters.json", "characterId": "protagonist"}],
    })

    mapped_runtime_ids = {
        profile["runtimeCharacterId"]
        for profile in profiles
        if profile["runtimeCharacterId"] is not None
    }
    for runtime in runtime_chars:
        runtime_id = runtime["id"]
        if runtime_id in mapped_runtime_ids:
            continue
        traits = build_character_traits(
            character_id=runtime_id,
            name=runtime["name"],
            role=runtime.get("role", ""),
            attributes={"identity": runtime.get("role", "")},
            source_refs=[{"status": "canon", "file": "characters.json"}],
            source_text="\n".join(
                [
                    runtime.get("privateNotes", ""),
                    *runtime.get("motivations", []),
                    *runtime.get("fears", []),
                ]
            ),
        )
        runtime_location = atlas_location_for_runtime(runtime.get("locationId"))
        atlas_loc = location_ids.get(runtime_location, {}) if runtime_location else {}
        profiles.append({
            "id": runtime_id,
            "runtimeCharacterId": runtime_id,
            "name": runtime["name"],
            "characterType": runtime.get("type", "npc"),
            "canonProfile": {
                "canonLayer": "runtime_supplement",
                "factStatus": "authoritative_runtime_definition",
                "instantiated": True,
                "attributes": {},
                "sources": [{"status": "canon", "file": "characters.json"}],
                "runtimeRecord": runtime,
                "originalSection": "",
                "originalDailyRoutine": "",
            },
            "supplementalProfile": {
                "status": "runtime_authored_non_catalog_character",
                "notes": "该人物来自开局人物文件，不占用 V4.2 的 P 编号。未知资料保持空值。",
            },
            **traits,
            "currentLocation": (
                {
                    "locationId": runtime_location,
                    "streetIds": atlas_loc.get("streetIds", []),
                    "sourceStatus": "canon",
                }
                if runtime_location
                else None
            ),
            "residences": [],
            "inventoryRef": f"inventory_{runtime_id}",
            "stateRef": f"state_{runtime_id}",
            "routineRef": f"routine_{runtime_id}",
            "relationshipRefs": [],
            "plotOverrideSlots": [],
            "visibility": {
                "public": {"name": True, "role": True},
                "gmOnly": {"privateNotes": True},
            },
            "provenance": [
                {"status": "canon", "file": "characters.json", "characterId": runtime_id}
            ],
        })
        mapped_runtime_ids.add(runtime_id)

    # Inventories are one record per character. Item data remains canonical in
    # the item atlas; this view stores only the item references held through a
    # character-owned container.
    runtime_character_ids = [profile["runtimeCharacterId"] for profile in profiles]
    inventory_resolution = ensure_inventory_containers(runtime_character_ids, containers)
    resolved_containers = [
        *containers,
        *(
            {
                "id": container.container_id,
                "kind": container.kind,
                "ownerCharacterId": container.owner_character_id,
                "locationId": container.location_id,
                "sourceStatus": "derived_bootstrap_inventory",
            }
            for container in inventory_resolution.generated
        ),
    ]
    profile_id_by_runtime_id = {
        profile["runtimeCharacterId"]: profile["id"]
        for profile in profiles
    }
    owner_containers = defaultdict(list)
    for container in resolved_containers:
        owner = container.get("ownerCharacterId")
        if owner:
            owner_containers[profile_id_by_runtime_id[owner]].append(container)
    inventory_by_pid = {}
    for profile in profiles:
        pid = profile["id"]
        owned_containers = owner_containers.get(pid, [])
        owned_container_ids = {container["id"] for container in owned_containers}
        item_refs = [
            {
                "instanceId": item["id"],
                "definitionId": item["definitionId"],
                "containerId": item["containerId"],
                "source": {"status": "canon", "file": "items.json"},
            }
            for item in items
            if item["containerId"] in owned_container_ids
        ]
        inventory_by_pid[pid] = {
            "id": f"inventory_{pid}", "characterId": pid,
            "containers": [{"id": c["id"], "kind": c["kind"], "locationId": c.get("locationId"), "sourceStatus": c.get("sourceStatus", "canon")} for c in owned_containers],
            "itemRefs": item_refs,
            "emptyReason": None if item_refs or owned_containers else "剧本尚未明确个人物品或专属容器",
            "sourcePolicy": "container_derived_item_references",
        }

    state_by_pid = {p["id"]: status_record(p["id"], p["name"], p.get("canonProfile", {}).get("runtimeRecord")) for p in profiles}
    routine_by_pid = {}
    for profile in profiles:
        routine_by_pid[profile["id"]] = routine_record(
            profile["id"],
            next((c for c in catalog_chars if c.get("attributes", {}).get("characterId") == profile["id"]), {"sources": []}),
            profile.get("canonProfile", {}).get("runtimeRecord"),
            schedules,
            set(location_ids),
            profile.get("canonProfile", {}).get("originalDailyRoutine", ""),
        )
    routine_by_pid["protagonist"]["routineNarrative"] = "玩家角色行动由玩家决定；AI 只能提出环境和 NPC 候选，不能替玩家决定行动。"

    relationships = normalize_runtime_relationships(raw_relationships, runtime_to_pid)
    relationships.extend(parse_catalog_relations(catalog_chars))
    rel_refs = defaultdict(list)
    for rel in relationships:
        rel_refs[rel["subjectId"]].append(rel["id"])
        if rel["objectId"] != rel["subjectId"]:
            rel_refs[rel["objectId"]].append(rel["id"])
    for profile in profiles:
        profile["relationshipRefs"] = rel_refs.get(profile["id"], [])

    templates = [
        {"id": "template_candle_oak_roomer", "name": "栎木街后排出租屋住户", "commonResidence": "housing_oak_back", "streetId": "candle_oak", "economicTier": "底层/低收入", "ordinaryInventory": ["零钱", "钥匙", "工作用品"], "routineActivities": ["取水", "购买面包", "邻里交谈"], "possibleRelationTypes": ["租客", "邻居", "债务关系"], "forbiddenAutoFacts": ["关键犯罪", "秘密血缘", "主线任务"]},
        {"id": "template_wind_organ_apartment", "name": "风琴巷三户公寓居民", "commonResidence": "housing_organ_court", "streetId": "candle_organ", "economicTier": "底层/小职员", "ordinaryInventory": ["衣物", "餐具", "工作用品"], "routineActivities": ["共用厨房", "洗衣", "邻里交谈"], "possibleRelationTypes": ["邻居", "房东租客"], "forbiddenAutoFacts": ["关键犯罪", "秘密血缘", "主线任务"]},
        {"id": "template_old_port_sailor", "name": "老港海员短租屋住客", "commonResidence": "housing_harbor_boarding", "streetId": "harbor_sailor", "economicTier": "流动工人", "ordinaryInventory": ["海员包", "绳具", "零钱"], "routineActivities": ["码头工作", "酒馆休息", "打听船期"], "possibleRelationTypes": ["同船", "雇主雇工", "债务关系"], "forbiddenAutoFacts": ["关键走私线", "主线任务"]},
        {"id": "template_iron_bay_worker", "name": "铁湾煤轨工棚工人", "commonResidence": "housing_iron_boarding", "streetId": "iron_machine", "economicTier": "工人", "ordinaryInventory": ["工作手套", "餐票", "工具"], "routineActivities": ["铁路货场工作", "工棚休息", "工人交谈"], "possibleRelationTypes": ["工友", "工会联系"], "forbiddenAutoFacts": ["关键犯罪", "主线任务"]},
        {"id": "template_south_well_lodger", "name": "南井合租院居民", "commonResidence": "housing_slope_courtyard", "streetId": "slope_southwell", "economicTier": "贫民/临时工", "ordinaryInventory": ["水桶", "零钱", "家用物品"], "routineActivities": ["取水", "市场采购", "院内交谈"], "possibleRelationTypes": ["邻居", "家庭", "债务关系"], "forbiddenAutoFacts": ["关键犯罪", "秘密血缘", "主线任务"]},
        {"id": "template_bell_tower_clerk", "name": "金钟职员公寓住户", "commonResidence": "housing_bell_clerk", "streetId": "bell_main", "economicTier": "小职员", "ordinaryInventory": ["公文包", "账册", "通勤衣物"], "routineActivities": ["通勤", "公告阅读", "咖啡馆休息"], "possibleRelationTypes": ["同事", "房东租客"], "forbiddenAutoFacts": ["关键金融内幕", "主线任务"]},
        {"id": "template_saint_bridge_student", "name": "圣桥学生宿舍学生", "commonResidence": "housing_bridge_students", "streetId": "bridge_rise", "economicTier": "学生/低收入", "ordinaryInventory": ["课本", "文具", "换洗衣物"], "routineActivities": ["上课", "洗衣", "学生社交"], "possibleRelationTypes": ["同学", "导师学生"], "forbiddenAutoFacts": ["关键政治密谋", "主线任务"]},
        {"id": "template_white_cliff_servant", "name": "白崖佣人小屋住户", "commonResidence": "housing_cliff_servants", "streetId": "cliff_seaview", "economicTier": "仆役/服务业", "ordinaryInventory": ["制服", "钥匙串", "个人零钱"], "routineActivities": ["豪宅工作", "采购", "仆役交谈"], "possibleRelationTypes": ["雇主雇工", "同事"], "forbiddenAutoFacts": ["关键贵族秘密", "主线任务"]},
        {"id": "template_daily_vendor", "name": "街区日常摊贩或店员", "commonResidence": None, "streetId": None, "economicTier": "小商贩/服务业", "ordinaryInventory": ["零钱", "货品", "记账纸条"], "routineActivities": ["开店", "补货", "收摊"], "possibleRelationTypes": ["顾客", "邻居", "供应商"], "forbiddenAutoFacts": ["关键线索", "主线任务"]},
    ]

    ATLAS_DIR.mkdir(parents=True, exist_ok=True)
    overview = {
        "schemaVersion": 2,
        "atlasId": "gray-harbor-character-atlas",
        "source": {"scenarioId": manifest["scenarioId"], "sourceVersion": manifest.get("sourceVersion"), "sourceDocument": manifest.get("sourceDocument"), "catalogFile": "v4.2-catalog.json"},
        "catalogCharacterCount": len(catalog_chars),
        "totalCharacterRecords": len(profiles),
        "runtimeCharacterCount": len(runtime_character_ids),
        "supplementalRuntimeCharacterCount": sum(
            1 for profile in profiles
            if profile["canonProfile"]["canonLayer"] == "runtime_supplement"
        ),
        "playerCharacterId": "protagonist",
        "backgroundTemplateCount": len(templates),
        "files": ["character-profiles.json", "character-states.json", "character-inventories.json", "relationship-atlas.json", "character-routines.json", "background-character-templates.json", "character-abilities.json"],
        "abilityCatalogVersion": 1,
        "abilityCatalogCount": len(ABILITY_BY_ID),
        "policies": {"canonStatuses": ["canon", "inferred", "unknown", "template"], "affinityScale": "-100..100", "aiRoutine": True, "plotOverrides": True, "runtimeCodeChanged": True},
    }
    write_json(ATLAS_DIR / "character-overview.json", overview)
    write_json(ATLAS_DIR / "character-profiles.json", {"schemaVersion": 2, "characters": profiles})
    write_json(ATLAS_DIR / "character-states.json", {"schemaVersion": 2, "worldTime": manifest.get("initialCalendar"), "states": list(state_by_pid.values())})
    write_json(ATLAS_DIR / "character-inventories.json", {
        "schemaVersion": 3,
        "itemAtlasRef": f"../{item_atlas_file}",
        "inventories": list(inventory_by_pid.values()),
    })
    write_json(ATLAS_DIR / "relationship-atlas.json", {"schemaVersion": 2, "scale": "-100..100", "axes": ["closeness", "respect", "fear", "dependence", "suspicion", "debt"], "relationships": relationships})
    write_json(ATLAS_DIR / "character-routines.json", {"schemaVersion": 2, "routines": list(routine_by_pid.values())})
    write_json(ATLAS_DIR / "background-character-templates.json", {"schemaVersion": 2, "templates": templates})
    write_json(ATLAS_DIR / "character-abilities.json", {"schemaVersion": 1, "catalog": ability_catalog_payload()})

    overview_md = f"""# 灰港人物图册总览\n\n- V4.2 Canon 人物：**{len(catalog_chars)}**\n- 运行时人物资料：**{len(profiles)}**（另含玩家 1 名、开局补充人物 2 名）\n- 背景人物模板：**{len(templates)}**\n- 能力词表：**{len(ABILITY_BY_ID)}** 种\n- 玩家角色：`protagonist`（艾拉·帕克）\n- 战役初始时间：海历621年10月17日23:00\n- 地点来源：`../atlas/location-atlas.json`\n\n## 资料边界\n\n本目录是人物资料层。`canon`、`inferred`、`unknown`、`template` 四种状态必须区分。人物住处、物品、状态、能力和行动候选都不能覆盖原始剧本事实。能力只是带来源的上下文标签，不直接决定行动成败。\n\n`character-inventories.json` 只保存人物容器和物品实例引用；物品的名称、数量、状态、价值和功能只能从 `../items.json` 与 `../items-atlas/` 查询，人物图册不复制这些权威数据。\n\n日常行动由 AI 提出候选，程序只校验地点、时间、权限、移动耗时、状态和剧情边界。剧情覆盖优先于日常候选，但仍需事件确认。\n\n## 文件索引\n\n| 文件 | 内容 |\n|---|---|\n| `character-profiles.json/md` | 全部人物基础档案、能力、语言风格、来源、住处和地点引用 |\n| `character-states.json/md` | 基线、战役开始状态和每日状态日志结构 |\n| `character-inventories.json/md` | 每个人物独立背包、容器和权威物品实例引用 |\n| `relationship-atlas.json/md` | 有向关系、六轴、-100..100好感度和发展钩子 |\n| `character-routines.json/md` | AI驱动行动候选与剧情覆盖接口 |\n| `background-character-templates.json/md` | 可后续实例化的背景人物模板 |\n| `character-abilities.json` | 53 种能力的稳定 ID、领域和定义 |\n"""
    (ATLAS_DIR / "character-overview.md").write_text(overview_md, encoding="utf-8")

    profiles_md = "# 人物基础档案\n\n共收录 **{}** 条人物记录。每名人物的 Canon 源摘录、能力候选、语言风格、运行时补充、住处和可见性边界均保留在 JSON 中。\n\n".format(len(profiles)) + "\n\n".join(profile_md(p) for p in profiles) + "\n"
    (ATLAS_DIR / "character-profiles.md").write_text(profiles_md, encoding="utf-8")

    states_md = "# 人物精神与身体状态\n\n所有人物都拥有 `baseline`、`current` 和可追加的 `dailyStateLog`。空值表示尚未确认，不表示状态不存在。身体状态中的 `externalInjuries` 只记录可见外伤候选；内伤字段和规则尚未设计。\n\n| 人物 | 当前精神 | 当前身体 | 来源 |\n|---|---|---|---|\n"
    for state in state_by_pid.values():
        states_md += f"| `{state['characterId']}` | {state['current']['mental']['mood']} / 压力：{state['current']['mental']['stress']} | {state['current']['physical']['health']} / 疲劳：{state['current']['physical']['fatigue']} | `{state['current']['sourceStatus']}` |\n"
    states_md += "\n## 每日状态更新边界\n\nAI 可以提出状态变化候选，但必须经过事件校验；状态变化需要世界时间和来源事件，不能由叙述文本直接写入。\n"
    (ATLAS_DIR / "character-states.md").write_text(states_md, encoding="utf-8")

    inv_md = "# 人物背包与物品引用\n\n人物图册只保存容器归属和物品实例引用。名称、数量、状态、价值及功能须查询 `../items.json` 与 `../{}`；人物归属只由容器的 `ownerCharacterId` 推导，没有容器所有者的物品不列入任何人物背包。\n\n".format(item_atlas_file)
    for inv in inventory_by_pid.values():
        inv_md += f"## {inv['characterId']}\n\n- 容器：{len(inv['containers'])}\n- 物品实例引用：{len(inv['itemRefs'])}\n- 空背包说明：{inv['emptyReason'] or '有已知物品或容器'}\n"
        for item_ref in inv["itemRefs"]:
            inv_md += f"- 实例 `{item_ref['instanceId']}`（定义 `{item_ref['definitionId']}`，容器 `{item_ref['containerId']}`；来源 `{item_ref['source']['file']}`）\n"
        inv_md += "\n"
    (ATLAS_DIR / "character-inventories.md").write_text(inv_md, encoding="utf-8")

    rel_md = "# 人物关系图册\n\n关系为有向记录。六轴和好感度都是历史投影，不能单独授予 NPC 行为权限。\n\n- 关系数量：**{}**\n- 好感度尺度：`-100..100`\n- 六轴：亲近、尊重、恐惧、依赖、怀疑、债务\n\n| 主体 | 对象 | 类型 | Canon状态 | 好感度 |\n|---|---|---|---|---:|\n".format(len(relationships))
    for rel in relationships:
        rel_md += f"| `{rel['subjectId']}` | `{rel['objectId']}` | {rel['relationType']} | `{rel['canonStatus']}` | {rel['affinity']['value'] if rel['affinity']['value'] is not None else '未定'} |\n"
    rel_md += "\n关系变化必须来自共同经历、兑现或违约、真实帮助、利益冲突、保护、背叛、公开羞辱或债务变化等具体事件。\n"
    (ATLAS_DIR / "relationship-atlas.md").write_text(rel_md, encoding="utf-8")

    routine_md = "# AI驱动人物日常轨迹\n\n本文件记录行动倾向和候选地点，不是固定日程。移动时间必须引用地点图册，剧情覆盖优先于日常候选。\n\n| 人物 | 日常文本 | 候选活动数 | 剧情覆盖槽 |\n|---|---|---:|---:|\n"
    for routine in routine_by_pid.values():
        text = routine["routineNarrative"].replace("\n", " ")[:100] or "未记录"
        routine_md += f"| `{routine['characterId']}` | {text} | {len(routine['candidateActivities'])} | {len(routine['plotOverrideSlots'])} |\n"
    routine_md += "\n## 校验边界\n\nAI 只能提出候选；程序校验地点存在、访问权限、时间窗口、身体和精神状态、移动耗时、剧情覆盖和故事影响等级。普通日常不能自动创建主线、关键道具或重大后果。\n"
    (ATLAS_DIR / "character-routines.md").write_text(routine_md, encoding="utf-8")

    ability_md = "# 人物能力词表\n\n能力是带来源的候选标签，不是自动判定公式。\n\n| ID | 名称 | 领域 | 说明 |\n|---|---|---|---|\n"
    for ability in ability_catalog_payload():
        ability_md += f"| `{ability['abilityId']}` | {ability['name']} | {ability['domain']} | {ability['description']} |\n"
    (ATLAS_DIR / "character-abilities.md").write_text(ability_md + "\n", encoding="utf-8")

    template_md = "# 背景人物模板\n\n这些模板用于后续 AI 或编剧实例化无名居民、临时工、路人和群体成员，不代表具体人物已经存在。\n\n"
    for template in templates:
        template_md += f"## `{template['id']}`｜{template['name']}\n\n- 经济层级：{template['economicTier']}\n- 街道：{template.get('streetId') or '实例化时决定'}\n- 普通物品：{'、'.join(template['ordinaryInventory'])}\n- 日常活动：{'、'.join(template['routineActivities'])}\n- 可能关系：{'、'.join(template['possibleRelationTypes'])}\n- 禁止自动生成：{'、'.join(template['forbiddenAutoFacts'])}\n\n"
    (ATLAS_DIR / "background-character-templates.md").write_text(template_md, encoding="utf-8")

    readme = """# 灰港人物图册\n\n本目录整理 V4.2 人物、玩家角色、能力、语言风格、关系、背包、每日状态、住处和 AI 日常行动候选。\n\n## 来源和边界\n\n- `v4.2-catalog.json` 和 V4.2 原稿是 Canon 来源。\n- `../atlas/location-atlas.json` 是地点、街道、结构和移动耗时的唯一引用来源。\n- `character-inventories.json` 只保存人物容器归属和物品实例/定义引用；`../items.json` 与 `../items-atlas/` 是物品字段的唯一来源。\n- 本目录不修改运行时剧本包，不把推定资料自动写入世界状态。\n- 能力和语言风格是带来源的上下文资料；`inferred` 不能冒充 `canon`。\n- 玩家角色的能力和语言风格由玩家定义，生成器不替玩家定型。\n\n## 重新生成\n\n```text\npython scripts/build_gray_harbor_characters.py\n```\n\n生成器会重新读取当前人物、物品、容器、关系、日程和地点图册，输出本目录的 JSON 与 Markdown。\n"""
    (ATLAS_DIR / "README.md").write_text(readme, encoding="utf-8")

    # Lightweight integrity checks before reporting success.
    assert len(catalog_chars) == 139
    assert len(profiles) == 142
    assert 20 <= len(ABILITY_BY_ID) <= 60
    assert len({p["id"] for p in profiles}) == len(profiles)
    assert all(p["runtimeCharacterId"] for p in profiles)
    assert len({p["runtimeCharacterId"] for p in profiles}) == len(profiles)
    assert {p["runtimeCharacterId"] for p in profiles} == set(runtime_character_ids)
    assert all(
        sum(1 for container in inventory_by_pid[p["id"]]["containers"] if container["kind"] == "inventory") == 1
        for p in profiles
    )
    assert all("abilities" in p and "languageStyle" in p for p in profiles)
    assert all(
        value["abilityId"] in ABILITY_BY_ID
        for profile in profiles
        for value in profile["abilities"]
    )
    assert all(
        value.get("level") in {"working", "competent", "advanced", "expert", None}
        and value.get("sourceStatus") in {"canon", "inferred", "unknown", "player_defined"}
        for profile in profiles
        for value in profile["abilities"]
    )
    assert all(
        set(profile["languageStyle"]) >= {
            "formality", "politeness", "directness", "verbosity", "pacing",
            "sentenceStyle", "addressTerms", "catchphrases", "pressureShift",
            "taboos", "sourceStatus", "sourceRefs", "notes",
        }
        for profile in profiles
    )
    assert all(s["characterId"] in {p["id"] for p in profiles} for s in state_by_pid.values())
    assert all(r["subjectId"] in {p["id"] for p in profiles} and r["objectId"] in {p["id"] for p in profiles} for r in relationships)
    assert all(r["affinity"]["value"] is None or -100 <= r["affinity"]["value"] <= 100 for r in relationships)
    assert all(res["locationId"] in location_ids and res["structureId"] in location_ids and all(st in street_ids for st in res["streetIds"]) for p in profiles for res in p["residences"])
    item_ids = {item["id"] for item in items}
    definition_ids = {item["definitionId"] for item in items}
    container_ids = {container["id"] for container in containers}
    assert all(
        set(item_ref) == {"instanceId", "definitionId", "containerId", "source"}
        and item_ref["instanceId"] in item_ids
        and item_ref["definitionId"] in definition_ids
        and item_ref["containerId"] in container_ids
        and item_ref["source"] == {"status": "canon", "file": "items.json"}
        for inventory in inventory_by_pid.values()
        for item_ref in inventory["itemRefs"]
    )
    print(f"generated {len(profiles)} character records, {len(relationships)} relationships, {len(templates)} templates")


if __name__ == "__main__":
    main()
