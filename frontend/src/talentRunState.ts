export type RunEvent = {
  event_id: string;
  sequence: number;
  type: string;
  run_id: string;
  timestamp: string;
  schema_version: string;
  data: Record<string, unknown>;
};

export type RunStep = {
  stepId: string;
  title: string;
  kind?: string;
  status: "running" | "paused" | "completed" | "cancelled" | "failed";
  completed?: number;
  total?: number;
  summary?: string;
  output?: Record<string, unknown>;
  startedAt?: string;
  durationMs?: number;
};

export type Citation = {
  ref: string;
  candidate?: string;
  document?: string;
  quote: string;
  url?: string;
};

export type Interrupt = {
  interaction_type?: string;
  interaction_id?: string;
  title: string;
  description?: string;
  input_kind?: "text";
  initial_value?: string;
  options: { value: string; label: string }[];
};

export type ConversationTurn = {
  runId: string;
  turnNo: number;
  message: string;
  resolvedRequestText: string;
  revisionAction: string;
  revisionSummary: string;
  status: RunView["status"];
  steps: RunStep[];
  report: string;
  citations: Citation[];
  traceId: string;
  error: string;
};

export type RunView = {
  runId: string;
  conversationId: string;
  turnNo: number;
  lastSequence: number;
  status: "idle" | "running" | "waiting_input" | "completed" | "cancelled" | "failed";
  steps: RunStep[];
  interrupt: Interrupt | null;
  report: string;
  citations: Citation[];
  traceId: string;
  error: string;
  conversationTitle: string;
  revisionAction: string;
  revisionSummary: string;
  conversationTurns: ConversationTurn[];
};

export type RunHistoryDetail = {
  run_id: string;
  conversation_id: string;
  turn_no: number;
  message: string;
  status: RunView["status"];
  report: string;
  citations: Citation[];
  trace_id: string;
  error?: string;
  steps: Array<{
    step_id: string;
    position: number;
    title: string;
    kind?: string;
    status: RunStep["status"];
    completed?: number;
    total?: number;
    summary?: string;
    output?: Record<string, unknown>;
    duration_ms?: number;
  }>;
  conversation_turns: Array<{
    run_id: string;
    turn_no: number;
    message: string;
    status: RunView["status"];
    report: string;
    citations: Citation[];
    trace_id: string;
    error?: string;
    steps: RunHistoryDetail["steps"];
  }>;
};

export const initialRunView: RunView = {
  runId: "",
  conversationId: "",
  turnNo: 0,
  lastSequence: 0,
  status: "idle",
  steps: [],
  interrupt: null,
  report: "",
  citations: [],
  traceId: "",
  error: "",
  conversationTitle: "",
  revisionAction: "",
  revisionSummary: "",
  conversationTurns: [],
};

function restoreSteps(steps: RunHistoryDetail["steps"]): RunStep[] {
  return [...steps]
    .sort((left, right) => left.position - right.position)
    .map((step) => ({
      stepId: step.step_id,
      title: step.title,
      kind: step.kind,
      status: step.status,
      completed: step.completed,
      total: step.total,
      summary: step.summary,
      output: step.output,
      durationMs: step.duration_ms,
    }));
}

export function defaultExpandedRunIds(
  turns: Array<Pick<ConversationTurn, "runId">>,
): string[] {
  return turns.length > 0 ? [turns[turns.length - 1].runId] : [];
}

export function historyDetailToRunView(detail: RunHistoryDetail): RunView {
  return {
    runId: detail.run_id,
    conversationId: detail.conversation_id,
    turnNo: detail.turn_no,
    lastSequence: 0,
    status: detail.status,
    steps: restoreSteps(detail.steps),
    interrupt: null,
    report: detail.report,
    citations: detail.citations,
    traceId: detail.trace_id,
    error: detail.error || "",
    conversationTitle: "",
    revisionAction: "",
    revisionSummary: "",
    conversationTurns: detail.conversation_turns.map((turn) => ({
      runId: turn.run_id,
      turnNo: turn.turn_no,
      message: turn.message,
      resolvedRequestText: "",
      revisionAction: "",
      revisionSummary: "",
      status: turn.status,
      steps: restoreSteps(turn.steps),
      report: turn.report,
      citations: turn.citations,
      traceId: turn.trace_id,
      error: turn.error || "",
    })),
  };
}

function text(value: unknown): string {
  return typeof value === "string" ? value : "";
}

function number(value: unknown): number | undefined {
  return typeof value === "number" ? value : undefined;
}

function object(value: unknown): Record<string, unknown> | undefined {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : undefined;
}

function elapsedMs(startedAt: string | undefined, endedAt: string): number {
  if (!startedAt) return 0;
  const elapsed = Date.parse(endedAt) - Date.parse(startedAt);
  return Number.isFinite(elapsed) && elapsed > 0 ? elapsed : 0;
}

function freezeRunningSteps(
  steps: RunStep[],
  timestamp: string,
  status: "paused" | "cancelled" | "failed",
): RunStep[] {
  return steps.map((step) => {
    if (step.status === "running") {
      return {
        ...step,
        status,
        durationMs: (step.durationMs || 0) + elapsedMs(step.startedAt, timestamp),
        startedAt: undefined,
      };
    }
    if (step.status === "paused" && status !== "paused") {
      return { ...step, status };
    }
    return step;
  });
}

export function getComposerPolicy(status: RunView["status"]): {
  disabled: boolean;
  showCancel: boolean;
} {
  const active = status === "running" || status === "waiting_input";
  return { disabled: active, showCancel: active };
}

export function shouldCloseStreamForEvent(eventType: string): boolean {
  return ["run.completed", "run.failed", "run.cancelled"].includes(eventType);
}

export function formatDuration(durationMs: number | undefined): string {
  if (durationMs === undefined) return "计时中";
  if (durationMs < 1000) return `${durationMs} ms`;
  if (durationMs < 60_000) return `${(durationMs / 1000).toFixed(1)} 秒`;
  const minutes = Math.floor(durationMs / 60_000);
  const seconds = Math.round((durationMs % 60_000) / 1000);
  return `${minutes} 分 ${seconds} 秒`;
}

function updateStep(state: RunView, event: RunEvent): RunStep[] {
  const stepId = text(event.data.step_id);
  const existing = state.steps.find((step) => step.stepId === stepId);
  const completed = event.type === "step.completed";
  const previousDuration = existing?.durationMs || 0;
  const startedAt = existing?.startedAt || event.timestamp;
  const next: RunStep = {
    stepId,
    title: text(event.data.title),
    kind: text(event.data.kind),
    status: completed ? "completed" : "running",
    completed: number(event.data.completed) ?? existing?.completed,
    total: number(event.data.total) ?? existing?.total,
    summary: text(event.data.summary) || existing?.summary,
    output: object(event.data.output)
      ? { ...(existing?.output || {}), ...object(event.data.output) }
      : existing?.output,
    startedAt: completed ? undefined : startedAt,
    durationMs: completed
      ? previousDuration + elapsedMs(startedAt, event.timestamp)
      : existing?.durationMs,
  };
  if (!existing) return [...state.steps, next];
  return state.steps.map((step) => (step.stepId === stepId ? { ...step, ...next } : step));
}

function snapshotCurrentTurn(
  state: RunView,
  status: ConversationTurn["status"],
): ConversationTurn[] {
  return state.conversationTurns.map((turn) =>
    turn.runId === state.runId
      ? {
          ...turn,
          status,
          steps: state.steps,
          report: state.report,
          citations: state.citations,
          traceId: state.traceId,
          error: state.error,
        }
      : turn,
  );
}

export function reduceRunEvent(state: RunView, event: RunEvent): RunView {
  if (event.sequence <= state.lastSequence) return state;
  const base = {
    ...state,
    runId: event.run_id,
    lastSequence: event.sequence,
  };

  if (event.type === "run.started") {
    const turnNo = number(event.data.turn_no) || 0;
    const turn: ConversationTurn = {
      runId: event.run_id,
      turnNo,
      message: text(event.data.message),
      resolvedRequestText: "",
      revisionAction: "",
      revisionSummary: "",
      status: "running",
      steps: [],
      report: "",
      citations: [],
      traceId: "",
      error: "",
    };
    const priorTurns = base.conversationTurns.filter(
      (item) => item.runId !== event.run_id,
    );
    return {
      ...base,
      status: "running",
      conversationId: text(event.data.conversation_id),
      turnNo,
      conversationTurns: [...priorTurns, turn].sort(
        (left, right) => left.turnNo - right.turnNo,
      ),
    };
  }
  if (event.type === "conversation.updated") {
    return { ...base, conversationTitle: text(event.data.title) };
  }
  if (event.type === "requirement.revised") {
    const revisionAction = text(event.data.action);
    const revisionSummary = text(event.data.summary);
    return {
      ...base,
      revisionAction,
      revisionSummary,
      conversationTurns: base.conversationTurns.map((turn) =>
        turn.runId === event.run_id
          ? { ...turn, revisionAction, revisionSummary }
          : turn,
      ),
    };
  }
  if (event.type.startsWith("step.")) {
    return { ...base, status: "running", steps: updateStep(base, event) };
  }
  if (event.type === "hitl.required") {
    return {
      ...base,
      status: "waiting_input",
      steps: freezeRunningSteps(base.steps, event.timestamp, "paused"),
      interrupt: event.data as unknown as Interrupt,
    };
  }
  if (event.type === "hitl.resolved") {
    return {
      ...base,
      status: "running",
      interrupt: null,
      steps: base.steps.map((step) =>
        step.status === "paused"
          ? { ...step, status: "running", startedAt: event.timestamp }
          : step,
      ),
    };
  }
  if (event.type === "report.completed") {
    return {
      ...base,
      report: text(event.data.markdown),
      citations: Array.isArray(event.data.citations)
        ? (event.data.citations as Citation[])
        : [],
      traceId: text(event.data.trace_id),
    };
  }
  if (event.type === "run.completed") {
    const completed = { ...base, status: "completed" as const, interrupt: null };
    return {
      ...completed,
      conversationTurns: snapshotCurrentTurn(completed, "completed"),
    };
  }
  if (event.type === "run.cancelled") {
    const cancelled = {
      ...base,
      status: "cancelled" as const,
      interrupt: null,
      steps: freezeRunningSteps(base.steps, event.timestamp, "cancelled"),
    };
    return {
      ...cancelled,
      conversationTurns: snapshotCurrentTurn(cancelled, "cancelled"),
    };
  }
  if (event.type === "run.failed") {
    const failed = {
      ...base,
      status: "failed" as const,
      interrupt: null,
      steps: freezeRunningSteps(base.steps, event.timestamp, "failed"),
      error: text(event.data.message),
    };
    return {
      ...failed,
      conversationTurns: snapshotCurrentTurn(failed, "failed"),
    };
  }
  return base;
}
