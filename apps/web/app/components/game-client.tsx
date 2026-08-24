"use client";

import { FormEvent, KeyboardEvent, useEffect, useMemo, useState } from "react";
import type { MapLocation, PublicMap } from "../map/types";

const CAMPAIGN_ID = "cmp_gray_harbor";
const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://127.0.0.1:8000";

type InventoryItem = {
  itemId: string;
  definitionId: string;
  name: string;
  description: string;
  category: string;
  isPlotItem: boolean;
  quantity: number;
  stackable: boolean;
  unitWeightGrams: number | null;
  valueCrown: number | null;
  condition: string | null;
  durability: { current: number; max: number } | null;
  containerId: string | null;
  locationId: string | null;
  properties: Record<string, unknown>;
  totalWeightGrams: number | null;
  totalValueCrown: number | null;
};

type EquippedItem = {
  slotId: string;
  itemId: string;
  mode: "held" | "worn";
  equippedAt: number;
};

type ExternalInjury = {
  injuryId: string;
  bodyPart: string;
  severity: string;
  status: string;
  functionalEffects?: Record<string, boolean>;
};

type Clue = {
  clueId: string;
  title: string;
  description: string;
};

type CampaignState = {
  campaignId: string;
  name: string;
  stateVersion: number;
  worldTimeLabel: string;
  weather?: {
    dateKey: string;
    season: string;
    seasonName: string;
    climateId: string;
    climateName: string;
    climateSourceStatus: "canon" | "inferred" | "default";
    condition: string;
    conditionName: string;
    lowTemperatureC: number;
    highTemperatureC: number;
    summary: string;
  } | null;
  locationPath?: string[];
  currentLocationDisplayName?: string | null;
  currentStructureName?: string | null;
  scene: {
    locationId?: string | null;
    name: string;
    locationPath?: string[];
    currentLocationDisplayName?: string | null;
    currentStructureName?: string | null;
    phase: string;
    beat: number;
    title?: string;
    openingText?: string;
    openIssues?: { issueId: string; title: string; status: string; endsAt: number | null }[];
    exits?: {
      toLocationId: string;
      name: string;
      label: string;
      travelMinutes: number;
      baseTravelMinutes?: number;
      weatherDelayMinutes?: number;
      estimatedTravelMinutes?: number;
      weatherCondition?: string | null;
      weatherConditionName?: string | null;
    }[];
  };
  map: PublicMap;
  player: {
    characterId: string;
    name: string;
    health: { current: number; maximum: number };
    focus: { current: number; maximum: number };
    inventory: InventoryItem[];
    equipment: EquippedItem[];
    externalInjuries: ExternalInjury[];
    profile?: { role?: string; birthplace?: string; publicDescription?: string; playerDefinedFields?: string[] };
  };
  relationships: Record<string, { favor: number; debt: number; trust: number; suspicion: number }>;
  clues: Clue[];
  organizations: { organizationId: string; name: string; type: string; publicDescription: string }[];
  activeClocks: { clockId: string; name: string; deadline: number; remainingMinutes: number; status: string }[];
  obligations: { obligationId: string; title: string; kind: string; status: string; dueClockId: string | null }[];
  worldReports?: { candidateId: string; title: string; summary: string; worldTime: number }[];
  availableActions?: {
    interactionId: string;
    kind: "inspect" | "ask";
    label: string;
    suggestedPrompt: string;
  }[];
  observedAffordances?: {
    opportunityId: string;
    locationId: string;
    actionKind: string;
    resourceKind: string;
    storyImpactCeiling: string;
  }[];
  catalogOpportunities?: {
    affordanceId: string;
    locationId: string;
    actionKinds: string[];
    resourceCategories: string[];
    storyImpactCeiling: string;
  }[];
};

type Reason = {
  code: string;
  label: string;
  direction: "positive" | "negative" | "neutral";
  value?: number | null;
  source_event_id?: string | null;
};

type TurnResponse = {
  turn_id: string;
  status: "committed" | "rejected";
  outcome: string;
  state_version: number;
  narrative: string;
  reasons: Reason[];
  visible_changes: string[];
  state: CampaignState;
  replayed: boolean;
  trace: TurnTrace;
};

type TurnTrace = {
  command_id: string;
  player_message_id: string;
  narrator_message_id: string;
  event_ids: string[];
  state_version_before: number;
  state_version_after: number;
  memory_retrieval: {
    trace_id: string;
    route: "episodic_memory" | "current_state_required";
    candidate_count: number;
    selected_ids: string[];
    rejected_count: number;
  } | null;
};

type TurnMessage = {
  message_id: string;
  message_kind: "player_input" | "narration" | "system";
  content: string;
  authority: "utterance_only" | "narration_only" | "system_record";
};

type TurnEvent = {
  event_id: string;
  sequence: number;
  event_type: string;
  sources: { message_id: string; source_kind: string }[];
};

type TurnDetail = {
  turn_id: string;
  status: string;
  command: {
    action_type: string;
    claimed_outcome: string | null;
    authority: "player" | "system" | "world";
    resolution_required: boolean;
    source_message_ids: string[];
    parser_source: "local" | "model" | "model_fallback";
    parser_model: string | null;
    parser_failure_code: string | null;
  };
  messages: TurnMessage[];
  events: TurnEvent[];
  intent_attempts: {
    attempt_id: string;
    status: "local" | "model_accepted" | "model_fallback";
    provider_name: string | null;
    model_name: string | null;
    failure_code: string | null;
    prompt_tokens: number | null;
    completion_tokens: number | null;
    total_tokens: number | null;
    latency_ms: number | null;
  }[];
  npc_decision_attempts: {
    attempt_id: string;
    status: "local" | "model_accepted" | "model_fallback";
    provider_name: string | null;
    model_name: string | null;
    failure_code: string | null;
    prompt_tokens: number | null;
    completion_tokens: number | null;
    total_tokens: number | null;
    latency_ms: number | null;
  }[];
  narration_attempts: {
    attempt_id: string;
    status: "local" | "model_accepted" | "model_fallback";
    provider_name: string | null;
    model_name: string | null;
    failure_code: string | null;
    prompt_tokens: number | null;
    completion_tokens: number | null;
    total_tokens: number | null;
    latency_ms: number | null;
  }[];
  retrieval_traces: {
    trace_id: string;
    purpose: "npc_decision" | "debug";
    perspective: { kind: "player" | "npc"; id: string };
    query: {
      schema_version: number;
      information_need: "historical" | "current";
      entity_ids: string[];
      event_types: string[];
      time_mode: string;
      limit: number;
      character_budget: number;
    };
    candidate_ids: string[];
    rejected: { memory_id: string; reason: string }[];
    selected_ids: string[];
    used_characters: number;
    route: "episodic_memory" | "current_state_required";
  }[];
  scene_memory_summaries: {
    summary_id: string;
    scene_id: string;
    segment_index: number;
    location_id: string | null;
    schema_version: number;
    sequence_range: [number, number];
    world_time_range: [number, number];
    generator: string;
    generator_version: number;
    status: "rolling" | "closed";
    resolved_count: number;
    unresolved_count: number;
    source_count: number;
  }[];
  routine_attempts: {
    attempt_id: string;
    status: string;
    provider_name: string | null;
    model_name: string | null;
    failure_code: string | null;
    rejected: { candidateId?: string; reason: string }[];
  }[];
  trace: TurnTrace;
};

function normalizeTurnDetail(value: Partial<TurnDetail>): TurnDetail {
  return {
    ...(value as TurnDetail),
    intent_attempts: value.intent_attempts ?? [],
    npc_decision_attempts: value.npc_decision_attempts ?? [],
    narration_attempts: value.narration_attempts ?? [],
    retrieval_traces: value.retrieval_traces ?? [],
    scene_memory_summaries: value.scene_memory_summaries ?? [],
    routine_attempts: value.routine_attempts ?? [],
  };
}

function normalizeCampaignState(value: Partial<CampaignState>): CampaignState {
  const scene = value.scene ?? {
    name: "当前地点",
    phase: "exploration",
    beat: 0,
  };
  const rawMap = value.map ?? {
    currentLocationId: scene.locationId ?? null,
    locations: [],
  };
  const locationsById = new Map<string, MapLocation>();
  for (const location of rawMap.locations ?? []) {
    const previous = locationsById.get(location.locationId);
    if (!previous) {
      locationsById.set(location.locationId, location);
      continue;
    }
    const exits = new Map(previous.exits.map((exit) => [exit.exitId, exit]));
    for (const exit of location.exits ?? []) exits.set(exit.exitId, exit);
    const characters = new Map(
      previous.visibleCharacters.map((character) => [character.characterId, character]),
    );
    for (const character of location.visibleCharacters ?? []) {
      characters.set(character.characterId, character);
    }
    locationsById.set(location.locationId, {
      ...previous,
      ...location,
      exits: [...exits.values()],
      visibleCharacters: [...characters.values()],
    });
  }
  return {
    ...(value as CampaignState),
    scene,
    map: {
      ...rawMap,
      locations: [...locationsById.values()],
    },
  };
}

function currentLocationDisplayName(state: CampaignState): string {
  const candidates = [
    state.map.currentLocationDisplayName,
    state.scene.currentLocationDisplayName,
    state.currentLocationDisplayName,
    state.map.locationPath?.join("·"),
    state.scene.locationPath?.join("·"),
    state.locationPath?.join("·"),
    state.map.displayLocationName,
    state.map.currentLocationName,
    state.scene.name,
  ];
  return candidates.find(
    (candidate): candidate is string => Boolean(candidate?.trim()),
  ) ?? "当前地点";
}

const fallbackOpeningNarrative =
  "秋雨刚停。哈维·科尔把一张纸放到白鹭屋的桌上，只说：七天。";

function itemMeta(item: InventoryItem) {
  if (item.containerId?.includes("equipment")) return "已装备";
  if (item.quantity > 1) return `× ${item.quantity}`;
  return item.condition && item.condition !== "intact" ? item.condition : item.category;
}

function routinePrompt(resourceKind: string, actionKind: string) {
  if (resourceKind === "food") return "我想找一些食物";
  if (resourceKind === "drink") return "我想找点喝的";
  if (actionKind === "work") return "我想找点临时工作";
  if (actionKind === "social") return "我想和附近的人聊聊";
  if (actionKind === "rest") return "我想在这里休息一会儿";
  return "我观察一下周围有什么";
}

function reasonValue(reason: Reason) {
  if (reason.value == null) return "事实";
  if (reason.direction === "positive") return `影响 ${Math.abs(reason.value)}`;
  if (reason.direction === "negative") return `阻力 ${Math.abs(reason.value)}`;
  return `${reason.value}`;
}

function narrativeParagraphs(text: string) {
  return text.split(/\n\s*\n/).map((paragraph) => paragraph.trim()).filter(Boolean);
}

type LocationExit = NonNullable<CampaignState["scene"]["exits"]>[number];

function LocationNavigation({
  currentLocationName,
  exits,
  disabled,
  onTravel,
  className = "",
}: {
  currentLocationName: string;
  exits: LocationExit[];
  disabled: boolean;
  onTravel: (locationName: string) => void;
  className?: string;
}) {
  return (
    <section className={`location-navigation ${className}`.trim()} aria-label="地点移动">
      <div className="section-title">
        <h3>地点移动</h3>
        <span>{exits.length}</span>
      </div>
      <p className="location-navigation-current">当前位置：{currentLocationName}</p>
      {exits.length ? (
        <div className="location-exit-list">
          {exits.map((exit, index) => {
            const baseMinutes = exit.baseTravelMinutes ?? exit.travelMinutes;
            const weatherDelayMinutes = exit.weatherDelayMinutes ?? 0;
            const estimatedMinutes = exit.estimatedTravelMinutes
              ?? baseMinutes + weatherDelayMinutes;
            const weatherLabel = exit.weatherConditionName ?? "天气";
            return (
              <button
                type="button"
                className="location-exit"
                key={`${exit.toLocationId}-${index}`}
                disabled={disabled}
                onClick={() => onTravel(exit.name)}
                title={`前往${exit.name}`}
              >
                <span className="location-exit-arrow" aria-hidden="true">→</span>
                <span className="location-exit-copy">
                  <strong>{exit.name}</strong>
                  <small>{exit.label}</small>
                  <small className="location-exit-time">
                    基础 {baseMinutes} 分钟 + {weatherLabel} {weatherDelayMinutes} 分钟 = 预计 {estimatedMinutes} 分钟
                  </small>
                </span>
              </button>
            );
          })}
        </div>
      ) : <p className="empty-copy">这里没有已确认的可见出口。</p>}
    </section>
  );
}

export default function GameClient() {
  const [state, setState] = useState<CampaignState | null>(null);
  const [lastTurn, setLastTurn] = useState<TurnResponse | null>(null);
  const [turnDetail, setTurnDetail] = useState<TurnDetail | null>(null);
  const [developerMode] = useState(
    () => typeof window !== "undefined"
      && new URLSearchParams(window.location.search).get("mode") === "developer",
  );
  const [action, setAction] = useState("");
  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function loadState() {
    const response = await fetch(`${API_BASE}/api/v1/campaigns/${CAMPAIGN_ID}/state`, {
      cache: "no-store",
    });
    if (!response.ok) throw new Error("无法读取当前世界状态");
    const nextState = normalizeCampaignState(
      (await response.json()) as Partial<CampaignState>,
    );
    setState(nextState);
  }

  useEffect(() => {
    const controller = new AbortController();
    fetch(`${API_BASE}/api/v1/campaigns/${CAMPAIGN_ID}/state`, {
      cache: "no-store",
      signal: controller.signal,
    })
      .then((response) => {
        if (!response.ok) throw new Error("无法读取当前世界状态");
        return response.json() as Promise<Partial<CampaignState>>;
      })
      .then((nextState) => setState(normalizeCampaignState(nextState)))
      .catch((cause) => {
        if (cause instanceof DOMException && cause.name === "AbortError") return;
        setError("规则服务未连接。请先启动 Python 后端。");
      })
      .finally(() => setLoading(false));
    return () => controller.abort();
  }, []);

  async function submitText(text: string) {
    if (!text || !state || submitting) return;

    setSubmitting(true);
    setError(null);
    try {
      const response = await fetch(`${API_BASE}/api/v1/campaigns/${CAMPAIGN_ID}/turns`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          idempotency_key: crypto.randomUUID(),
          expected_state_version: state.stateVersion,
          actor_id: state.player.characterId,
          text,
        }),
      });
      if (response.status === 409) {
        await loadState();
        throw new Error("世界状态刚刚发生变化，已经为你同步，请重新提交行动。");
      }
      if (!response.ok) throw new Error("这次行动没有成功提交");
      const result = (await response.json()) as TurnResponse;
      setLastTurn(result);
      setState(normalizeCampaignState(result.state));
      setAction("");
      const detailResponse = await fetch(
        `${API_BASE}/api/v1/campaigns/${CAMPAIGN_ID}/turns/${result.turn_id}`,
        { cache: "no-store" },
      );
      setTurnDetail(
        detailResponse.ok
          ? normalizeTurnDetail((await detailResponse.json()) as Partial<TurnDetail>)
          : null,
      );
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "发生了未知错误");
    } finally {
      setSubmitting(false);
    }
  }

  async function submitAction(event?: FormEvent) {
    event?.preventDefault();
    await submitText(action.trim());
  }

  async function resetCampaign() {
    if (submitting) return;
    setSubmitting(true);
    setError(null);
    try {
      const response = await fetch(`${API_BASE}/api/v1/gray-harbor/reset`, {
        method: "POST",
      });
      if (!response.ok) throw new Error("无法重置灰港开局");
      const result = (await response.json()) as { state: Partial<CampaignState> };
      setState(normalizeCampaignState(result.state));
      setLastTurn(null);
      setTurnDetail(null);
      setAction("");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "发生了未知错误");
    } finally {
      setSubmitting(false);
    }
  }

  function handleKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      void submitAction();
    }
  }

  const reasons = useMemo<Reason[]>(() => {
    if (lastTurn?.reasons.length) return lastTurn.reasons;
    if (!state) return [];
    const initial: Reason[] = [];
    if (state.activeClocks.length) {
      const days = Math.ceil(state.activeClocks[0].remainingMinutes / 1440);
      initial.push({ code: "active_deadline", label: `${state.activeClocks[0].name}仍在推进`, direction: "negative", value: days });
    }
    if (state.obligations.length) {
      initial.push({ code: "active_obligation", label: `${state.obligations[0].title}是已记录的权威状态`, direction: "neutral" });
    }
    initial.push({ code: "authoritative_world", label: "物品与历史只以事件日志为准", direction: "neutral" });
    return initial;
  }, [lastTurn, state]);

  if (loading) {
    return <main className="system-message">正在重放世界事件……</main>;
  }

  if (!state) {
    return (
      <main className="system-message error-message">
        <p>{error ?? "无法进入这个世界。"}</p>
        <button type="button" onClick={() => location.reload()}>重新连接</button>
      </main>
    );
  }

  const healthWidth = `${(state.player.health.current / state.player.health.maximum) * 100}%`;
  const focusWidth = `${(state.player.focus.current / state.player.focus.maximum) * 100}%`;
  return (
    <main className="game-shell">
      <header className="topbar">
        <div className="brand">
          <span className="brand-mark" aria-hidden="true">灰</span>
          <div>
            <p className="eyebrow">AUTHORITATIVE STORY ENGINE</p>
            <h1>{state.name}</h1>
            <p className="current-location-label">
              当前位置：{currentLocationDisplayName(state)}
            </p>
          </div>
        </div>
        <div className="header-actions">
          <div className="turn-status"><span className="status-dot" />第 {state.stateVersion} 版世界 · {submitting ? "判定中" : "等待行动"}</div>
          <button className="reset-button" type="button" onClick={resetCampaign} disabled={submitting}>重置开局</button>
        </div>
      </header>

      <section className="workspace">
        <aside className="character-panel" aria-label="角色状态">
          <div className="portrait" aria-hidden="true"><span>鹭</span></div>
          <div className="character-heading">
            <p className="eyebrow">PLAYER CHARACTER</p>
            <h2>{state.player.name}</h2>
            <p>
              {state.player.profile?.role ?? "白鹭屋的年轻女人"}
              {state.player.profile?.birthplace ? ` · 出生于${state.player.profile.birthplace}` : ""}
            </p>
          </div>

          <div className="vitals">
            <div className="vital-row"><span>体力</span><strong>{state.player.health.current} / {state.player.health.maximum}</strong></div>
            <div className="meter"><span style={{ width: healthWidth }} /></div>
            <div className="vital-row focus-row"><span>专注</span><strong>{state.player.focus.current} / {state.player.focus.maximum}</strong></div>
            <div className="meter focus"><span style={{ width: focusWidth }} /></div>
          </div>

          <LocationNavigation
            currentLocationName={currentLocationDisplayName(state)}
            exits={state.scene.exits ?? []}
            disabled={submitting}
            onTravel={(locationName) => void submitText(`我前往${locationName}。`)}
          />

          <div className="panel-section">
            <div className="section-title"><h3>随身物品</h3><span>{state.player.inventory.length} / 8</span></div>
            <ul className="inventory-list">
              {state.player.inventory.map((item) => (
                <li key={item.itemId}>
                  <span className="item-glyph" aria-hidden="true">◇</span>
                    <span className="item-name-block">
                    <strong>{item.name}</strong>
                    <small>{itemMeta(item)}</small>
                  </span>
                </li>
              ))}
            </ul>
          </div>

          <div className="panel-section">
            <div className="section-title"><h3>当前装备</h3><span>{state.player.equipment.length}</span></div>
            {state.player.equipment.length ? (
              <ul className="inventory-list">
                {state.player.equipment.map((equipment) => {
                  const item = state.player.inventory.find((candidate) => candidate.itemId === equipment.itemId);
                  return (
                    <li key={`${equipment.slotId}-${equipment.itemId}`}>
                      <span className="item-glyph" aria-hidden="true">◈</span>
                      <span className="item-name-block">
                        <strong>{item?.name ?? equipment.itemId}</strong>
                        <small>{equipment.slotId} · {equipment.mode === "held" ? "持有" : "穿戴"}</small>
                      </span>
                    </li>
                  );
                })}
              </ul>
            ) : <p className="empty-state">暂无已装备物品</p>}
          </div>

          {state.player.externalInjuries.length ? (
            <div className="panel-section injury-section">
              <div className="section-title"><h3>外伤</h3><span>{state.player.externalInjuries.length}</span></div>
              <ul className="inventory-list">
                {state.player.externalInjuries.map((injury) => (
                  <li key={injury.injuryId}>
                    <span className="item-glyph" aria-hidden="true">!</span>
                    <span className="item-name-block">
                      <strong>{injury.bodyPart}</strong>
                      <small>{injury.severity} · {injury.status}</small>
                    </span>
                  </li>
                ))}
              </ul>
            </div>
          ) : null}

          <div className="world-time">
            <span>世界时间</span>
            <strong>{state.worldTimeLabel}</strong>
            <small>
              {state.weather
                ? `${state.weather.seasonName} · ${state.weather.conditionName} · ${state.weather.lowTemperatureC}–${state.weather.highTemperatureC}°C`
                : "天气尚未确定"}
            </small>
          </div>
        </aside>

        <section className="story-panel" aria-label="当前故事">
          <div className="scene-heading">
            <div><p className="eyebrow">CURRENT SCENE</p><h2>{currentLocationDisplayName(state)}</h2></div>
            <div className="scene-badge">探索 · 交涉</div>
          </div>
          <LocationNavigation
            className="mobile-location-navigation"
            currentLocationName={currentLocationDisplayName(state)}
            exits={state.scene.exits ?? []}
            disabled={submitting}
            onTravel={(locationName) => void submitText(`我前往${locationName}。`)}
          />
          {state.catalogOpportunities?.length ? (
            <section className="opportunity-strip" aria-label="当前地点可以尝试">
              <div className="section-title"><h3>当前地点可以尝试</h3><span>日常行动</span></div>
              <div className="opportunity-list">
                {state.catalogOpportunities.flatMap((opportunity) => opportunity.actionKinds.slice(0, 5).map((actionKind) => (
                  <button
                    type="button"
                    key={`${opportunity.affordanceId}-${actionKind}`}
                    disabled={submitting}
                    onClick={() => void submitText(routinePrompt(
                      opportunity.resourceCategories.some((value) => /食|餐|面包|市场|商户/.test(value)) ? "food" : "",
                      actionKind,
                    ))}
                  >
                    {actionKind === "search" ? "搜索" : actionKind === "commerce" ? "买卖" : actionKind === "social" ? "闲聊" : actionKind === "work" ? "找工作" : actionKind === "rest" ? "休息" : "观察"}
                  </button>
                ))) }
              </div>
            </section>
          ) : null}

          <div className="narrative" aria-live="polite">
            <p className="scene-note">{state.activeClocks[0] ? `${state.activeClocks[0].name}：还剩 ${Math.ceil(state.activeClocks[0].remainingMinutes / 1440)} 天。` : "当前没有公开期限。"}</p>
            <article className="story-beat">
              {narrativeParagraphs(
                lastTurn?.narrative ?? state.scene.openingText ?? fallbackOpeningNarrative,
              ).map((paragraph, index) => <p key={`${index}-${paragraph.slice(0, 24)}`}>{paragraph}</p>)}
            </article>
            {lastTurn?.visible_changes.length ? (
              <div className="visible-changes">
                {lastTurn.visible_changes.map((change) => <span key={change}>◆ {change}</span>)}
              </div>
            ) : null}
            <div className={`control-returned ${lastTurn?.status === "rejected" ? "rejected" : ""}`}>
              <span>◆</span>
              {lastTurn?.status === "rejected" ? "行动没有发生，世界状态未被篡改。" : "叙事已暂停，控制权回到你手中。"}
            </div>
          </div>

          <form className="action-box" onSubmit={submitAction}>
            <label htmlFor="player-action">你打算怎么做？</label>
            {state.availableActions?.length ? (
              <div className="suggested-actions" aria-label="当前可尝试的调查行动">
                {state.availableActions.map((availableAction) => (
                  <button
                    type="button"
                    key={availableAction.interactionId}
                    onClick={() => setAction(availableAction.suggestedPrompt)}
                    disabled={submitting}
                  >
                    <span>{availableAction.kind === "inspect" ? "查" : "问"}</span>
                    {availableAction.label}
                  </button>
                ))}
              </div>
            ) : null}
            <textarea
              id="player-action"
              name="player-action"
              rows={3}
              value={action}
              onChange={(event) => setAction(event.target.value)}
              onKeyDown={handleKeyDown}
              disabled={submitting}
              placeholder="你可以自由描述行动，也可以先选择上方的调查方向。"
            />
            {error ? <p className="inline-error" role="alert">{error}</p> : null}
            <div className="action-footer">
              <p><kbd>Enter</kbd> 提交 · <kbd>Shift</kbd> + <kbd>Enter</kbd> 换行</p>
              <button type="submit" disabled={!action.trim() || submitting}>
                {submitting ? "正在判定…" : "采取行动"} <span aria-hidden="true">→</span>
              </button>
            </div>
          </form>
        </section>

        <aside className="context-panel" aria-label="场景线索与影响">
          <div className="context-section">
            <p className="eyebrow">WHY THIS HAPPENED</p>
            <h2>判定依据</h2>
            <p className="context-copy">系统会引用真实历史、关系和规则，而不是把玩家的描述直接写进世界。</p>
            <ul className="influence-list">
              {reasons.map((reason) => (
                <li key={`${reason.code}-${reason.label}`} title={reason.source_event_id ? `来源事件：${reason.source_event_id}` : undefined}>
                  <span className={`influence-mark ${reason.direction}`} />
                  <span>{reason.label}</span>
                  <strong>{reasonValue(reason)}</strong>
                </li>
              ))}
            </ul>
          </div>

          <div className="context-section thread-section">
            <div className="section-title"><h3>活跃线索</h3><span>{state.clues.length}</span></div>
            {state.clues.length ? state.clues.map((clue, index) => (
              <div className="thread-card" key={clue.clueId}>
                <span className="thread-index">{String(index + 1).padStart(2, "0")}</span>
                <div><strong>{clue.title}</strong><p>{clue.description}</p></div>
              </div>
            )) : <p className="empty-copy">尚未发现可追踪的线索。</p>}
          </div>

          {state.scene.openIssues?.length ? (
            <div className="context-section thread-section">
              <div className="section-title"><h3>当前未解决问题</h3><span>{state.scene.openIssues.length}</span></div>
              {state.scene.openIssues.map((issue) => (
                <div className="thread-card" key={issue.issueId}>
                  <span className="thread-index">!</span>
                  <div><strong>{issue.title}</strong><p>问题仍在世界中等待处理，不会因为没有行动而自动消失。</p></div>
                </div>
              ))}
            </div>
          ) : null}

          {state.worldReports?.length ? (
            <div className="context-section thread-section">
              <div className="section-title"><h3>城市周报</h3><span>{state.worldReports.length}</span></div>
              {state.worldReports.slice(-3).reverse().map((report) => (
                <div className="thread-card" key={report.candidateId}>
                  <span className="thread-index">·</span>
                  <div><strong>{report.title}</strong><p>{report.summary}</p></div>
                </div>
              ))}
            </div>
          ) : null}

          {developerMode && lastTurn ? (
            <div className="context-section trace-section">
              <p className="eyebrow">DEVELOPMENT TRACE</p>
              <h2>本回合来源链</h2>
              <p className="context-copy">这条链路用于证明玩家原话没有直接变成世界事实。</p>
              <ol className="trace-flow">
                <li>
                  <span>01</span>
                  <div><strong>玩家原始输入</strong><code>{lastTurn.trace.player_message_id}</code><small>只记录“玩家说过什么”</small></div>
                </li>
                <li>
                  <span>02</span>
                  <div>
                    <strong>结构化动作</strong>
                    <code>{turnDetail?.command.action_type ?? "读取中"}</code>
                    <small>
                      决定权：{turnDetail?.command.authority ?? "—"} · 解析：
                      {turnDetail?.command.parser_source === "model"
                        ? `模型 ${turnDetail.command.parser_model ?? ""}`
                        : turnDetail?.command.parser_source === "model_fallback"
                          ? "模型失败后安全降级"
                          : "本地规则"}
                    </small>
                  </div>
                </li>
                <li>
                  <span>03</span>
                  <div><strong>确认事件</strong><code>{lastTurn.trace.event_ids.length} 条</code><small>只有确认事件可以更新状态</small></div>
                </li>
                <li>
                  <span>04</span>
                  <div><strong>叙述输出</strong><code>{lastTurn.trace.narrator_message_id}</code><small>叙述不能反向修改事件</small></div>
                </li>
              </ol>
              <div className="version-shift">
                <span>世界版本</span>
                <strong>{lastTurn.trace.state_version_before} → {lastTurn.trace.state_version_after}</strong>
              </div>
              {turnDetail?.intent_attempts[0] ? (
                <div className="version-shift">
                  <span>解析审计</span>
                  <strong title={turnDetail.intent_attempts[0].failure_code ?? undefined}>
                    {turnDetail.intent_attempts[0].status === "model_accepted"
                      ? `模型 · ${turnDetail.intent_attempts[0].model_name ?? "未命名"}${
                          turnDetail.intent_attempts[0].total_tokens !== null
                            ? ` · ${turnDetail.intent_attempts[0].total_tokens} tokens`
                            : ""
                        }${
                          turnDetail.intent_attempts[0].latency_ms !== null
                            ? ` · ${turnDetail.intent_attempts[0].latency_ms} ms`
                            : ""
                        }`
                      : turnDetail.intent_attempts[0].status === "model_fallback"
                        ? "安全降级"
                        : "本地规则"}
                  </strong>
                </div>
              ) : null}
              {turnDetail?.narration_attempts[0] ? (
                <div className="version-shift">
                  <span>叙述审计</span>
                  <strong title={turnDetail.narration_attempts[0].failure_code ?? undefined}>
                    {turnDetail.narration_attempts[0].status === "model_accepted"
                      ? `模型润色 · ${turnDetail.narration_attempts[0].model_name ?? "未命名"}${
                          turnDetail.narration_attempts[0].total_tokens !== null
                            ? ` · ${turnDetail.narration_attempts[0].total_tokens} tokens`
                            : ""
                        }${
                          turnDetail.narration_attempts[0].latency_ms !== null
                            ? ` · ${turnDetail.narration_attempts[0].latency_ms} ms`
                            : ""
                        }`
                      : turnDetail.narration_attempts[0].status === "model_fallback"
                        ? "模型叙述被拒绝，采用规则原文"
                        : "规则叙述"}
                  </strong>
                </div>
              ) : null}
              {turnDetail?.npc_decision_attempts[0] ? (
                <div className="version-shift">
                  <span>NPC 决策审计</span>
                  <strong title={turnDetail.npc_decision_attempts[0].failure_code ?? undefined}>
                    {turnDetail.npc_decision_attempts[0].status === "model_accepted"
                      ? `上下文决策 · ${turnDetail.npc_decision_attempts[0].model_name ?? "未命名"}${
                          turnDetail.npc_decision_attempts[0].total_tokens !== null
                            ? ` · ${turnDetail.npc_decision_attempts[0].total_tokens} tokens`
                            : ""
                        }${
                          turnDetail.npc_decision_attempts[0].latency_ms !== null
                            ? ` · ${turnDetail.npc_decision_attempts[0].latency_ms} ms`
                            : ""
                        }`
                      : turnDetail.npc_decision_attempts[0].status === "model_fallback"
                        ? "AI 决策未通过，采用安全保守结果"
                        : "本地保守决策"}
                  </strong>
                </div>
              ) : null}
              {turnDetail?.routine_attempts[0] ? (
                <div className="version-shift">
                  <span>日常候选审计</span>
                  <strong title={turnDetail.routine_attempts[0].failure_code ?? undefined}>
                    {turnDetail.routine_attempts[0].status === "model_accepted"
                      ? `候选已通过 · ${turnDetail.routine_attempts[0].model_name ?? "未命名"}`
                      : turnDetail.routine_attempts[0].status === "model_fallback"
                        ? "模型失败，安全降级"
                        : "本地日常边界"}
                  </strong>
                </div>
              ) : null}
              {turnDetail?.retrieval_traces[0] ? (
                <>
                  <div className="version-shift">
                    <span>长期记忆检索</span>
                    <strong>
                      候选 {turnDetail.retrieval_traces[0].candidate_ids.length} · 采用 {turnDetail.retrieval_traces[0].selected_ids.length} · 排除 {turnDetail.retrieval_traces[0].rejected.length}
                    </strong>
                  </div>
                  <details className="event-details">
                    <summary>查看记忆筛选轨迹</summary>
                    <ul>
                      {turnDetail.retrieval_traces[0].selected_ids.map((memoryId) => (
                        <li key={`selected-${memoryId}`}>
                          <span>采用</span>
                          <code>{memoryId}</code>
                          <small>已通过实体、时间与角色视角检查</small>
                        </li>
                      ))}
                      {turnDetail.retrieval_traces[0].rejected.map((memory) => (
                        <li key={`rejected-${memory.memory_id}`}>
                          <span>排除</span>
                          <code>{memory.memory_id}</code>
                          <small>{memory.reason}</small>
                        </li>
                      ))}
                    </ul>
                  </details>
                </>
              ) : null}
              {turnDetail?.scene_memory_summaries?.[0] ? (
                <div className="version-shift">
                  <span>场景记忆摘要</span>
                  <strong title={turnDetail.scene_memory_summaries[0].summary_id}>
                    片段 {turnDetail.scene_memory_summaries[0].segment_index + 1} · {turnDetail.scene_memory_summaries[0].source_count} 条来源 · {turnDetail.scene_memory_summaries[0].status === "closed" ? "已封存" : "持续更新"}
                  </strong>
                </div>
              ) : null}
              {turnDetail?.events.length ? (
                <details className="event-details">
                  <summary>查看确认事件</summary>
                  <ul>
                    {turnDetail.events.map((event) => (
                      <li key={event.event_id}>
                        <span>#{event.sequence}</span>
                        <code>{event.event_type}</code>
                        <small>{event.sources.length ? "已关联原始输入" : "无消息来源"}</small>
                      </li>
                    ))}
                  </ul>
                </details>
              ) : (
                <p className="no-events">本次没有确认事件，世界版本保持不变。</p>
              )}
            </div>
          ) : null}

          <div className="pacing-card">
            <div><span>场景节奏</span><strong>{state.scene.beat} 个节拍已推进</strong></div>
            <span className="pacing-icon" aria-hidden="true">Ⅰ</span>
          </div>
        </aside>
      </section>
    </main>
  );
}
