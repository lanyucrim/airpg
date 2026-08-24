from __future__ import annotations

from trpg_server.core.state import Event, SceneIssueState, Projection
from trpg_server.core.projection_handlers import projection_handlers


@projection_handlers.register("scene.started")
def apply_scene_started(state: Projection, event: Event) -> None:
    if event.schema_version not in {1, 2}:
        raise ValueError(f"unsupported scene.started schema version: {event.schema_version}")
    payload = event.payload
    state.scene_id = payload["sceneId"]
    state.location_id = payload["locationId"]
    state.scene_phase = payload.get("phase", "exploration")
    state.scene_title = payload.get("title", "")
    state.scene_objective = payload.get("objective", "")
    state.scene_opening_text = payload.get("openingText", "")
    state.scene_present_character_ids = tuple(payload.get("presentCharacterIds", []))
    guidance = payload.get("narrativeGuidance") or {}
    state.scene_narrative_premise = guidance.get("premise", "")
    state.scene_narrative_anchors = tuple(guidance.get("hardAnchors", []))
    state.scene_flexible_approaches = tuple(guidance.get("flexibleApproaches", []))
    state.scene_stop_before = tuple(guidance.get("stopBefore", []))
    state.max_major_beats_per_turn = payload.get("maxMajorBeatsPerTurn", 1)


@projection_handlers.register("scene.beat_advanced")
def apply_scene_beat_advanced(state: Projection, event: Event) -> None:
    state.scene_beat += event.payload.get("beats", 1)


@projection_handlers.register("scene.issue_opened")
def apply_scene_issue_opened(state: Projection, event: Event) -> None:
    payload = event.payload
    issue = SceneIssueState(
        issue_id=payload["issueId"],
        title=payload["title"],
        status="open",
        source_event_id=payload["sourceEventId"],
        created_at=event.world_time,
        ends_at=payload.get("endsAt"),
    )
    state.scene_issues[issue.issue_id] = issue


@projection_handlers.register("scene.issue_resolved")
def apply_scene_issue_resolved(state: Projection, event: Event) -> None:
    issue = state.scene_issues.get(event.payload["issueId"])
    if issue is not None:
        state.scene_issues[issue.issue_id] = SceneIssueState(
            issue.issue_id,
            issue.title,
            "resolved",
            issue.source_event_id,
            issue.created_at,
            issue.ends_at,
        )


@projection_handlers.register("scene.location_changed")
def apply_scene_location_changed(state: Projection, event: Event) -> None:
    state.location_id = event.payload["toLocationId"]


@projection_handlers.register("world.reported")
def apply_world_reported(state: Projection, event: Event) -> None:
    state.world_reports.append(dict(event.payload))
