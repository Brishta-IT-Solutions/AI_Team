export type Stage =
  | "NEW" | "BA_ANALYSIS" | "REQUIREMENTS_APPROVAL" | "READY_FOR_DEV" | "DEVELOPING" | "DEV_REVIEW"
  | "QA" | "FIX_REQUIRED" | "MERGE_APPROVAL" | "MERGING" | "DONE" | "CANCELLED";

export type ExecutionStatus = "IDLE" | "QUEUED" | "RUNNING" | "BLOCKED" | "PAUSED" | "FAILED";

export type Project = {
  id: string; key: string; name: string; status: "DRAFT" | "ACTIVE" | "DISABLED" | "ARCHIVED";
  classification: string; policy_version: number; version: number; description: string; created_at: string;
};

export type TaskCard = {
  id: string; key: string; title: string; priority: "P0" | "P1" | "P2" | "P3"; stage: Stage;
  execution_status: ExecutionStatus; version: number; column: string; status_reason: string | null;
  repair_count: number; created_at: string; updated_at: string;
};

export type Board = { columns: { name: string; items: TaskCard[] }[] };

export type PermittedAction = { command: string; allowed: boolean; authorized: boolean; reasons: string[] };

export type AcceptanceCriterion = { id: string; statement: string; verification: string; mandatory: boolean };
export type Question = { id: string; text: string; blocking: boolean; resolution: string | null };
export type BASpec = {
  goal: string;
  stories: { id: string; as_a: string; i_want: string; so_that: string }[];
  business_rules: { id: string; statement: string }[];
  acceptance_criteria: AcceptanceCriterion[];
  ux_flows: { id: string; name: string; steps: string[] }[];
  edge_cases: string[];
  dependencies: string[];
  questions: Question[];
};

export type TaskView = {
  task: TaskCard & {
    project_id: string; project_key: string; description: string; current_spec_id: string;
    approved_spec_id: string; head_sha: string | null; base_sha: string | null; repair_limit: number;
    branch: string | null;
    owner_id: string;
  };
  dependencies: { id: string; key: string; stage: Stage }[];
  requirements: {
    versions: { id: string; version: number; content_hash: string; source: string; created_by_kind: string;
      provenance: Record<string, unknown>; created_at: string }[];
    current: BASpec | null;
    current_version: number | null;
    approved_spec_id: string | null;
    open_blocking_questions: Question[];
    approval_scope_hash: string | null;
  };
  approvals: { id: string; gate: string; decision: string; scope_hash: string; human_id: string;
    reason: string | null; supersedes_id: string; created_at: string }[];
  permitted_actions: PermittedAction[];
};

export type AuditItem = {
  seq: number; event_id: string; actor_kind: string; actor_id: string; action: string; object_type: string;
  object_id: string | null; outcome: "ALLOWED" | "DENIED"; reason: string | null; correlation_id: string;
  created_at: string;
};

export const STAGE_LABEL: Record<Stage, string> = {
  NEW: "New", BA_ANALYSIS: "BA analysis", REQUIREMENTS_APPROVAL: "Awaiting requirements approval",
  READY_FOR_DEV: "Ready for development", DEVELOPING: "Developing", DEV_REVIEW: "Developer self-check",
  QA: "Independent QA", FIX_REQUIRED: "Fix required", MERGE_APPROVAL: "Awaiting merge approval",
  MERGING: "Merging", DONE: "Done", CANCELLED: "Cancelled",
};

export type Role = "BA" | "DEVELOPER" | "QA" | "JUNIOR";

export type TeamMember = {
  role: Role; member: string; title: string; also?: string;
  state: "working" | "online" | "offline" | "not_configured";
  configured_model: string | null; live_model: string | null; detail: string | null;
  running: number; queued: number;
};

export type RunStatus = "QUEUED" | "RUNNING" | "SUCCEEDED" | "FAILED" | "BLOCKED" | "CANCELLED";

export type RunItem = {
  id: string; role: Role; member: string; status: RunStatus; attempt: number; parent_run_id: string | null;
  milestone: string | null; provider: string | null; model: string | null; worker_id: string | null;
  usage: Record<string, unknown> | null; cost: string | null; cost_quality: string;
  error: { code?: string; message?: string } | null;
  result: {
    review?: "PASSED" | "FAILED"; findings?: string[]; gate?: string; reasons?: string[]; accepted?: boolean;
    files_touched?: string[];
    submission?: { head_sha: string; base_sha: string; files_changed: string[]; summary: string;
      tests: { command_id: string; exit_code: number }[] };
    checks?: { command_id: string; exit_code: number; tail: string }[];
    junior?: { accepted: boolean; reasons?: string[]; task_type?: string; expected_output?: string }[];
  } | null;
  created_at: string; started_at: string | null; ended_at: string | null;
  logs?: { sequence: number; type: string; message: string; at: string }[];
};

export type CriterionResult = "PASS" | "FAIL" | "BLOCKED" | "NOT_RUN";

export type QAData = {
  current_report_id: string | null;
  reports: { id: string; verdict: string; head_sha: string; base_sha: string; current: boolean; created_at: string;
    payload: { criteria_results: { ac_id: string; result: CriterionResult; evidence_refs: string[] }[];
      suites: { name: string; mandatory: boolean; result: CriterionResult }[];
      findings: { ac_id: string | null; severity: string; title: string }[] } }[];
  defects: { id: string; ac_id: string | null; severity: string; status: string; title: string;
    evidence: { reproduction_steps?: string[]; expected?: string; actual?: string }; updated_at: string }[];
};
