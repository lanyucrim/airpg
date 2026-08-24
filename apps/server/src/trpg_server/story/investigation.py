from __future__ import annotations

from dataclasses import dataclass

from trpg_server.core.state import InquiryState, InspectionState, Projection
from trpg_server.items.models import ItemInstance


@dataclass(frozen=True, slots=True)
class InteractionDecision:
    allowed: bool
    outcome: str
    reason_code: str
    reason_label: str
    knows_answer: bool = True


def evaluate_inspection(
    state: Projection,
    actor_id: str,
    definition: InspectionState,
) -> InteractionDecision:
    if definition.interaction_id in state.completed_interactions.get(actor_id, set()):
        return InteractionDecision(
            False,
            "already_completed",
            "interaction_already_completed",
            "这项调查已经完成，没有新的信息可重复获得",
        )

    item = state.items.get(definition.target_item_id)
    if item is None:
        return InteractionDecision(
            False,
            "inspection_target_missing",
            "inspection_target_missing",
            "权威物品记录中不存在调查目标",
        )

    if not _item_is_accessible(state, actor_id, item, definition.access_policy):
        return InteractionDecision(
            False,
            "inspection_target_inaccessible",
            "inspection_target_inaccessible",
            "调查目标不在行动者可接触的位置或容器中",
        )

    known = state.knowledge.get(actor_id, set())
    if not set(definition.required_actor_knowledge_fact_ids) <= known:
        return InteractionDecision(
            False,
            "missing_investigation_context",
            "missing_investigation_context",
            "行动者尚未掌握完成这项调查所需的背景事实",
        )

    return InteractionDecision(
        True,
        "inspection_available",
        "inspection_preconditions_met",
        "物品存在、可以接触，且调查前提已经满足",
    )


def evaluate_inquiry(
    state: Projection,
    actor_id: str,
    definition: InquiryState,
) -> InteractionDecision:
    if definition.interaction_id in state.completed_interactions.get(actor_id, set()):
        return InteractionDecision(
            False,
            "already_completed",
            "interaction_already_completed",
            "这个话题已经得到当前可用的回答",
        )

    actor_location = state.character_locations.get(actor_id)
    target_location = state.character_locations.get(definition.target_character_id)
    if actor_location is None or actor_location != target_location:
        return InteractionDecision(
            False,
            "target_not_present",
            "target_not_present",
            "被询问者不在行动者当前地点",
        )

    known = state.knowledge.get(actor_id, set())
    if not set(definition.required_actor_knowledge_fact_ids) <= known:
        return InteractionDecision(
            False,
            "missing_inquiry_context",
            "missing_inquiry_context",
            "行动者尚未掌握提出这个具体问题所需的背景事实",
        )

    npc_known = state.knowledge.get(definition.target_character_id, set())
    answer_knowledge = set(definition.required_npc_knowledge_fact_ids) | set(
        definition.revealed_fact_ids
    )
    knows_answer = answer_knowledge <= npc_known
    return InteractionDecision(
        True,
        "inquiry_available" if knows_answer else "npc_does_not_know",
        "npc_knowledge_available" if knows_answer else "npc_lacks_knowledge",
        "被询问者知道这个话题的可回答内容"
        if knows_answer
        else "被询问者当前并不知道答案",
        knows_answer=knows_answer,
    )


def _item_is_accessible(
    state: Projection,
    actor_id: str,
    item: ItemInstance,
    access_policy: str,
) -> bool:
    container = state.containers.get(item.container_id)
    if access_policy == "actor_owned":
        return container is not None and container.owner_character_id == actor_id
    if access_policy == "location":
        actor_location = state.character_locations.get(actor_id)
        if actor_location is None:
            return False
        return item.location_id == actor_location or (
            container is not None and container.location_id == actor_location
        )
    return False
