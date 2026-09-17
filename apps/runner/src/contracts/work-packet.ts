/**
 * TypeScript mirror of apps/api/app/agent_runtime/work_packet.py's
 * WorkPacket (Phase 01) — field names and shapes kept identical so a
 * WorkPacket serialized by the Python side deserializes here without
 * translation. Only the fields a coding runtime actually needs are
 * mirrored (the full contract also carries knowledge_context,
 * upstream_artifacts, etc. — this runner reads those too, generically, via
 * `unknown` passthrough fields it does not interpret, so a future field
 * added upstream never breaks parsing here).
 *
 * SIGNING (this phase's step 1, "Receive a signed WorkPacket"): the
 * signature envelope is a thin wrapper around the packet, not a change to
 * the packet's own shape — see signed-work-packet.ts.
 */

export interface RepositoryReference {
  provider: string;
  owner: string;
  name: string;
  base_branch: string;
  base_commit_sha: string;
}

export interface ScopePolicy {
  allowed_paths: string[];
  denied_paths: string[];
  deny_by_default: boolean;
  max_files_changed?: number | null;
}

export interface ToolPolicy {
  allowed_tools: string[];
  denied_tools: string[];
  deny_by_default: boolean;
  require_dry_run_first: boolean;
}

export interface RequiredCheck {
  name: string;
  description: string;
  severity: "BLOCKING" | "ADVISORY";
  command?: string | null;
}

export interface BudgetPolicy {
  max_cost_usd?: number | null;
  max_wall_clock_seconds?: number | null;
  max_llm_calls?: number | null;
  max_tool_calls?: number | null;
  max_distinct_tools?: number | null;
  max_repair_attempts?: number | null;
  max_context_tokens?: number | null;
  max_output_tokens?: number | null;
}

export interface ApprovalPolicy {
  requires_human_approval: boolean;
  approval_roles: string[];
  escalation_roles: string[];
}

export interface AcceptanceCriterion {
  id: string;
  description: string;
  verification_method?: string | null;
}

export interface WorkObjective {
  goal: string;
  success_definition: string;
  non_goals: string[];
}

export interface WorkPacket {
  schema_version: string;
  packet_id: string;
  task_type: string;

  project_id: string;
  story_id?: string | null;

  objective: WorkObjective;
  acceptance_criteria: AcceptanceCriterion[];

  repository?: RepositoryReference | null;
  upstream_artifacts: unknown[];
  knowledge_context: unknown[];

  scope_policy: ScopePolicy;
  tool_policy: ToolPolicy;
  required_checks: RequiredCheck[];
  budget_policy: BudgetPolicy;
  approval_policy: ApprovalPolicy;

  created_at: string;
  created_by_user_id?: string | null;

  extensions?: Record<string, unknown>;
}

/**
 * The envelope a caller (the Python-side Phase 06 dispatcher, or its own
 * future HTTP bridge) hands to this runner. `signature` is an HMAC-SHA256
 * over the canonical JSON of `packet`, keyed by a shared secret both sides
 * hold (RUNNER_SHARED_SIGNING_SECRET) — see signed-work-packet.ts for
 * verification. This is deliberately NOT a JWT/asymmetric scheme: the
 * signer (this codebase's own API process) and the verifier (this runner)
 * are the same trust domain, so a symmetric shared secret is the correct,
 * simplest mechanism — mirrors Phase 07's own EnvSecretProvider precedent
 * for company-managed secrets rather than introducing a new PKI.
 */
export interface SignedWorkPacket {
  packet: WorkPacket;
  signature: string;
  /** Which ProjectExecutionProfile version (Phase 03) was approved and applied — required, never inferred. */
  execution_profile: ProjectExecutionProfile;
}

/**
 * TypeScript mirror of the fields of
 * apps/api/app/models/project_execution_profile.py's ProjectExecutionProfile
 * a coding runtime actually consumes (step 4, "Apply the approved
 * ProjectExecutionProfile"). Only APPROVED, is_active profiles should ever
 * reach this runner — enforced upstream (Phase 03's execution_profile_gate),
 * not re-derived here; this runner trusts the envelope it was handed.
 */
export interface ProjectExecutionProfile {
  id: string;
  project_id: string;
  version: number;
  status: string;
  working_directories: string[];
  package_manager?: string | null;
  install_command?: string | null;
  lint_command?: string | null;
  format_check_command?: string | null;
  type_check_command?: string | null;
  unit_test_command?: string | null;
  integration_test_command?: string | null;
  build_command?: string | null;
  approved_security_scan_commands: string[];
  allowed_command_patterns: string[];
  denied_command_patterns: string[];
  allowed_paths: string[];
  denied_paths: string[];
  network_policy: { default: "DENY" | "ALLOW"; allowed_domains?: string[]; denied_domains?: string[] };
  environment_variable_names: string[];
  required_coding_skills: string[];
  required_testing_skills: string[];
  data_classification: string;
}
