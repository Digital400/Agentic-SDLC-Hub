/**
 * Phase 15: per-SDLC-stage structured action field schemas.
 *
 * Dependency-free (no React) so field definitions and WorkPacket building
 * are unit-testable without rendering anything. Each stage is keyed by
 * its real WorkflowNode.node_key (see workflows/sdlc-workflow.json) —
 * "requirement_intake", "story_crafting", "lld" (Story LLD), "implementation",
 * "testing", "pr_review" — matching Phase 16's own migration order list
 * exactly.
 *
 * A stage with no schema here (HLD, Problem Discovery, Solution Discovery,
 * Implementation Planning, ...) keeps the existing generic
 * freeform-textarea-per-required-input behavior in agent-actions-panel.tsx
 * unchanged — this is an additive, opt-in-per-stage replacement, not a
 * rewrite of the whole input mechanism (same strangler pattern as every
 * other phase this session).
 */

export type FieldType = "text" | "textarea" | "select" | "multiselect";

export interface FieldOption {
  value: string;
  label: string;
}

export interface FieldSchema {
  /** The input_context key this field's value is sent under — unchanged
   * wire contract, so the backend needs zero changes for this phase. */
  key: string;
  label: string;
  type: FieldType;
  options?: FieldOption[];
  required?: boolean;
  placeholder?: string;
}

export const ADDITIONAL_CONTEXT_KEY = "additional_context";
export const ADDITIONAL_CONTEXT_MAX_LENGTH = 500;

export const STAGE_FIELD_SCHEMAS: Record<string, FieldSchema[]> = {
  requirement_intake: [
    { key: "business_objective", label: "Business objective", type: "textarea", required: true },
    { key: "users", label: "Users", type: "textarea", required: true },
    { key: "current_problem", label: "Current problem", type: "textarea", required: true },
    { key: "scope", label: "Scope", type: "textarea" },
    { key: "constraints", label: "Constraints", type: "textarea" },
    { key: "compliance", label: "Compliance", type: "textarea" },
    { key: "success_measures", label: "Success measures", type: "textarea" },
  ],
  story_crafting: [
    {
      key: "slicing_mode", label: "Vertical or horizontal slicing", type: "select", required: true,
      options: [
        { value: "VERTICAL", label: "Vertical (end-to-end user value)" },
        { value: "HORIZONTAL", label: "Horizontal (one story per technical layer)" },
      ],
    },
    {
      key: "story_size_preference", label: "Story size preference", type: "select",
      options: [
        { value: "small", label: "Small" },
        { value: "medium", label: "Medium" },
        { value: "large", label: "Large" },
      ],
    },
    { key: "jira_project", label: "Jira project", type: "text" },
    { key: "dependencies", label: "Dependencies", type: "textarea" },
    {
      key: "required_test_scenario_level", label: "Required test scenario level", type: "select",
      options: [
        { value: "basic", label: "Basic" },
        { value: "standard", label: "Standard" },
        { value: "comprehensive", label: "Comprehensive" },
      ],
    },
  ],
  lld: [
    { key: "selected_story", label: "Selected story", type: "text", required: true },
    { key: "approved_architecture_references", label: "Approved architecture references", type: "textarea" },
    { key: "technical_constraints", label: "Technical constraints", type: "textarea" },
    { key: "repository_module_scope", label: "Repository/module scope", type: "text" },
  ],
  implementation: [
    { key: "approved_story_lld", label: "Approved Story LLD", type: "text", required: true },
    {
      key: "runtime_preference", label: "Runtime preference", type: "select",
      options: [
        { value: "auto", label: "Auto — Recommended" },
        { value: "opencode", label: "OpenCode" },
        { value: "codex", label: "Codex" },
        { value: "claude-code", label: "Claude Code" },
        { value: "antigravity", label: "Antigravity" },
        { value: "local-agent", label: "Connected local agent" },
      ],
    },
    {
      key: "execution_location", label: "Execution location", type: "select",
      options: [
        { value: "company-sandbox", label: "Company sandbox" },
        { value: "developer-machine", label: "My connected development machine" },
      ],
    },
    { key: "maximum_budget", label: "Maximum budget (USD)", type: "text" },
  ],
  testing: [
    { key: "test_types", label: "Test types", type: "multiselect", options: [
      { value: "unit", label: "Unit" },
      { value: "integration", label: "Integration" },
      { value: "e2e", label: "End-to-end" },
      { value: "security", label: "Security" },
    ] },
    { key: "approved_commands", label: "Approved commands", type: "textarea" },
    { key: "required_quality_gates", label: "Required quality gates", type: "textarea" },
  ],
  pr_review: [
    { key: "pr_identity", label: "PR identity", type: "text", required: true },
    { key: "review_focus", label: "Review focus", type: "textarea" },
    {
      key: "severity_threshold", label: "Severity threshold", type: "select",
      options: [
        { value: "low", label: "Low" },
        { value: "medium", label: "Medium" },
        { value: "high", label: "High" },
      ],
    },
    {
      key: "comment_posting_approval", label: "Comment-posting approval", type: "select",
      options: [
        { value: "requires_approval", label: "Requires human approval" },
        { value: "auto_post", label: "Post automatically" },
      ],
    },
  ],
};

export function getStageSchema(nodeKey: string): FieldSchema[] | null {
  return STAGE_FIELD_SCHEMAS[nodeKey] ?? null;
}

export function hasStructuredSchema(nodeKey: string): boolean {
  return nodeKey in STAGE_FIELD_SCHEMAS;
}

/**
 * Builds the same `inputContext: Record<string,string>` shape the
 * existing freeform flow already sends (agent-actions-panel.tsx's
 * `runAgentAndApply(..., inputContext: freeformValues)`) — this phase
 * only changes how values are collected (structured fields instead of
 * one open textarea per required-input key), not the wire contract, so
 * no backend change is needed.
 */
export function buildInputContext(
  schema: FieldSchema[],
  values: Record<string, string>,
  additionalContext: string,
): Record<string, string> {
  const context: Record<string, string> = {};
  for (const field of schema) {
    const value = values[field.key];
    if (value !== undefined && value !== "") {
      context[field.key] = value;
    }
  }
  const trimmedAdditional = additionalContext.slice(0, ADDITIONAL_CONTEXT_MAX_LENGTH);
  if (trimmedAdditional) {
    context[ADDITIONAL_CONTEXT_KEY] = trimmedAdditional;
  }
  return context;
}

export function missingRequiredFields(schema: FieldSchema[], values: Record<string, string>): FieldSchema[] {
  return schema.filter((field) => field.required && !(values[field.key] ?? "").trim());
}
