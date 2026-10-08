"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { Loader2, PlayCircle, Sparkles } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Select } from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import { api, ApiError, type ApiProviderOverride } from "@/lib/api";
import { buildQuickPicks, parseQuickPickValue, quickPickValue, useProviderOptions } from "@/lib/use-provider-options";
import { ModelOverrideInput } from "@/components/model-override-input";
import { runAgentAndApply } from "@/lib/run-agent";
import { AskQuestionsAction, RegenerateSectionAction, SummarizeChangesAction } from "@/components/documents/document-assist-actions";
import { LargeTextInput } from "@/components/documents/large-text-input";
import { ValidationVerdictCard } from "@/components/documents/validation-verdict-card";
import { readVerdict, type ValidationVerdict } from "@/lib/validation-verdict";
import { usePreviousRunInput } from "@/lib/use-previous-run-input";
import { clearDraft, describeCondensation, exceedsInputLimit, readCondensation } from "@/lib/intake-text";

type AgentAction = "draft" | "improve" | "validate";

const ACTION_LABEL: Record<AgentAction, string> = {
  draft: "Draft",
  improve: "Improve",
  validate: "Validate",
};

export interface AgentRunOutcome {
  artifactStatus: string;
  workflowNodeStatus: string;
}

// Real "Run Agent" flow (see lib/run-agent.ts) — "Improve section" is also
// real (see app/services/section_improve_agent.py), and so are "Regenerate
// section", "Ask questions" and "Summarize changes" (see
// document-assist-actions.tsx).
export function AgentActionsPanel({
  activeSectionTitle,
  projectId,
  workflowNodeId,
  agentKey,
  artifactId,
  artifactEditable,
  documentHasRealSections,
  freeformInputKeys,
  triggeredByUserId,
  versionKey,
  onApplied,
}: {
  activeSectionTitle: string | null;
  projectId: string;
  workflowNodeId: string;
  agentKey: string;
  artifactId: string;
  /** Only a DRAFT artifact can be improved this way — see
   * app/services/section_improve_agent.py's precondition. */
  artifactEditable: boolean;
  /** False for a headingless document — see markdown-sections.ts's
   * hasRealSections and section_improve_agent.py's matching guard: there's
   * no genuine section to scope an edit to, so "Improve section" is
   * disabled entirely rather than risk regenerating (and losing) the
   * whole document from one instruction. */
  documentHasRealSections: boolean;
  freeformInputKeys: string[];
  triggeredByUserId: string | null;
  /** Changes whenever the document gets a new version, so "Summarize changes" refreshes. */
  versionKey: string;
  onApplied: (outcome: AgentRunOutcome) => void;
}) {
  const [action, setAction] = useState<AgentAction>("draft");
  // Empty string = "project default" (auto-selected provider / Claude Agent
  // SDK if enabled) — the usual case, so this stays unset unless the user
  // deliberately picks a specific backend for this one run.
  const [providerOverride, setProviderOverride] = useState<ApiProviderOverride | "">("");
  const [modelOverride, setModelOverride] = useState("");
  const { options: providerOptions } = useProviderOptions(projectId);
  const [freeformValues, setFreeformValues] = useState<Record<string, string>>({});
  // Pre-fill with what was submitted on the previous run so the box is never
  // misleadingly empty for a stage that has already been run.
  const previousInput = usePreviousRunInput(projectId, workflowNodeId, freeformInputKeys);
  useEffect(() => {
    if (previousInput.loading) return;
    setFreeformValues((prev) => {
      const next = { ...prev };
      for (const key of freeformInputKeys) if (!next[key]?.trim() && previousInput.values[key]) next[key] = previousInput.values[key];
      return next;
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps -- only when the loaded values change
  }, [previousInput.loading, previousInput.values]);
  const [running, setRunning] = useState(false);
  const [result, setResult] = useState<
    { kind: "failed"; message: string } | { kind: "completed"; runId: string; retrievedCount: number; condensedNote: string | null; verdict: ValidationVerdict | null } | null
  >(null);

  const [improvingSection, setImprovingSection] = useState(false);
  const [sectionInstruction, setSectionInstruction] = useState("");
  const [sectionImproveBusy, setSectionImproveBusy] = useState(false);
  const [sectionImproveResult, setSectionImproveResult] = useState<
    { kind: "failed"; message: string } | { kind: "clarification"; message: string } | { kind: "applied" } | null
  >(null);
  // Answer box for a section-level clarification round — see
  // handleSubmitSectionClarification below for why this exists: without
  // it, "Apply" just resubmitted the exact same instruction the agent had
  // already said wasn't enough, with no way to actually answer it.
  const [sectionClarificationAnswer, setSectionClarificationAnswer] = useState("");

  // Switching sections mid-instruction would silently apply to the wrong
  // one — close the panel and clear any stale instruction/result instead.
  useEffect(() => {
    setImprovingSection(false);
    setSectionInstruction("");
    setSectionImproveResult(null);
    setSectionClarificationAnswer("");
  }, [activeSectionTitle]);

  async function handleImproveSection(instructionOverride?: string) {
    if (triggeredByUserId === null) {
      setSectionImproveResult({ kind: "failed", message: "No users exist yet to attribute this run to." });
      return;
    }
    const instructionToSend = (instructionOverride ?? sectionInstruction).trim();
    if (!activeSectionTitle || !instructionToSend) return;
    setSectionImproveBusy(true);
    setSectionImproveResult(null);
    try {
      const response = await api.artifacts.improveSection(artifactId, {
        section_title: activeSectionTitle,
        instruction: instructionToSend,
        triggered_by_user_id: triggeredByUserId,
      });
      if (response.needs_clarification) {
        // Keep the (possibly just-combined) instruction visible so a
        // second clarification round, if needed, keeps compounding from
        // what's actually been asked so far, rather than resetting to
        // whatever was in the box before this call.
        setSectionInstruction(instructionToSend);
        setSectionImproveResult({ kind: "clarification", message: response.agent_run.output_text ?? "The agent needs more information before it can revise this section." });
        return;
      }
      setSectionImproveResult({ kind: "applied" });
      setSectionInstruction("");
      setSectionClarificationAnswer("");
      setImprovingSection(false);
      onApplied({ artifactStatus: response.artifact_status, workflowNodeStatus: response.workflow_node_status });
    } catch (err) {
      setSectionImproveResult({ kind: "failed", message: err instanceof ApiError ? err.message : "Failed to improve this section." });
    } finally {
      setSectionImproveBusy(false);
    }
  }

  // "Improve section" has no separate clarification_answers channel like
  // the document-level Draft/Improve flow does (see ClarificationPanel) —
  // run_section_improve_agent takes one freeform `instruction` string, so
  // answering is just folding the answer into that same instruction and
  // re-running. Without this, clicking "Apply" again sent the identical
  // instruction the agent had already said wasn't enough, producing the
  // exact same clarification request every time — indistinguishable from
  // "the document just doesn't improve."
  async function handleSubmitSectionClarification() {
    if (!sectionClarificationAnswer.trim()) return;
    const combined = `${sectionInstruction.trim()}\n\nAdditional clarification:\n${sectionClarificationAnswer.trim()}`;
    setSectionClarificationAnswer("");
    await handleImproveSection(combined);
  }

  async function handleRun() {
    if (triggeredByUserId === null) {
      setResult({ kind: "failed", message: "No users exist yet to attribute this run to." });
      return;
    }
    setRunning(true);
    setResult(null);
    try {
      const { run, saved } = await runAgentAndApply({
        projectId,
        workflowNodeId,
        action,
        triggeredByUserId,
        inputContext: freeformValues,
        providerOverride: providerOverride || undefined,
        modelOverride: modelOverride.trim() || undefined,
      });

      if (run.status !== "COMPLETED" || saved === null) {
        setResult({ kind: "failed", message: run.error_message ?? "The run did not complete." });
        return;
      }

      for (const key of freeformInputKeys) clearDraft(`${projectId}:${workflowNodeId}:${key}`);
      const condensation = readCondensation(run.token_budget_report);
      setResult({
        kind: "completed",
        runId: run.id,
        retrievedCount: run.retrieved_sources?.length ?? 0,
        condensedNote: condensation ? describeCondensation(condensation) : null,
        verdict: readVerdict(run),
      });
      onApplied({ artifactStatus: saved.artifactStatus, workflowNodeStatus: saved.workflowNodeStatus });
    } catch (err) {
      setResult({ kind: "failed", message: err instanceof ApiError ? err.message : "Failed to run the agent." });
    } finally {
      setRunning(false);
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-sm">Agent actions</CardTitle>
        <CardDescription>
          {activeSectionTitle ? `Section actions target "${activeSectionTitle}".` : "Select a section to act on it directly."}
        </CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-3">
        <div className="rounded-md border border-border p-2.5">
          <p className="mb-2 text-xs font-medium">Run {agentKey || "agent"}</p>

          <Select value={action} onChange={(e) => setAction(e.target.value as AgentAction)} className="mb-2 text-xs">
            {(Object.keys(ACTION_LABEL) as AgentAction[]).map((a) => (
              <option key={a} value={a}>
                {ACTION_LABEL[a]}
              </option>
            ))}
          </Select>

          <Select
            value={providerOverride ? quickPickValue(providerOverride, modelOverride || null) : ""}
            onChange={(e) => {
              if (e.target.value === "") {
                setProviderOverride("");
                setModelOverride("");
                return;
              }
              const { provider, model } = parseQuickPickValue(e.target.value);
              setProviderOverride(provider);
              setModelOverride(model ?? "");
            }}
            className="mb-2 text-xs"
            title="Which LLM/model runs this one call — leave on Default to use the project's configured provider. A known model (e.g. claude-sonnet-5) is its own row; anything else can still be typed in the Model field below."
          >
            <option value="">LLM: Default (project-configured)</option>
            {buildQuickPicks(providerOptions).map((p) => (
              <option key={p.value} value={p.value} disabled={!p.configured} title={p.unavailable_reason ?? undefined}>
                LLM: {p.label}
              </option>
            ))}
          </Select>

          <ModelOverrideInput provider={providerOverride} providerOptions={providerOptions} value={modelOverride} onChange={setModelOverride} />

          {freeformInputKeys.map((key) => (
            <LargeTextInput
              key={key}
              label={key}
              storageKey={`${projectId}:${workflowNodeId}:${key}`}
              value={freeformValues[key] ?? ""}
              onChange={(v) => setFreeformValues((prev) => ({ ...prev, [key]: v }))}
              disabled={running}
            />
          ))}

          <Button size="sm" className="w-full" onClick={handleRun} disabled={running || exceedsInputLimit(freeformValues)}>
            {running ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <PlayCircle className="h-3.5 w-3.5" />}
            {running ? "Running…" : "Run Agent"}
          </Button>

          {result?.kind === "failed" ? (
            <p className="mt-2 text-xs text-destructive">{result.message}</p>
          ) : result?.kind === "completed" ? (
            <p className="mt-2 text-xs text-muted-foreground">
              Run completed{result.retrievedCount > 0 ? ` · used ${result.retrievedCount} knowledge source(s)` : ""} —{" "}
              <Link href={`/agent-runs/${result.runId}`} className="underline">
                view run
              </Link>
            </p>
          ) : null}
          {result?.kind === "completed" && result.verdict ? <ValidationVerdictCard verdict={result.verdict} /> : null}
          {result?.kind === "completed" && result.condensedNote ? (
            <p className="mt-2 rounded-md border border-amber-400/60 bg-amber-50 p-2 text-xs dark:border-amber-900 dark:bg-amber-950/30">
              {result.condensedNote}
            </p>
          ) : null}
        </div>

        <div className="rounded-md border border-border p-2.5">
          <Button
            variant="outline"
            size="sm"
            className="w-full justify-start"
            disabled={!activeSectionTitle || !artifactEditable || !documentHasRealSections}
            onClick={() => setImprovingSection((v) => !v)}
          >
            <Sparkles className="h-3.5 w-3.5" />
            Improve section
          </Button>
          {!artifactEditable ? (
            <p className="mt-1 text-xs text-muted-foreground">Only a DRAFT document can be improved this way.</p>
          ) : !documentHasRealSections ? (
            <p className="mt-1 text-xs text-muted-foreground">
              This document has no named sections yet — use &ldquo;Run Agent&rdquo; (Draft) above instead.
            </p>
          ) : null}

          {improvingSection && activeSectionTitle ? (
            <div className="mt-2 flex flex-col gap-2">
              <Textarea
                placeholder={`What should change in "${activeSectionTitle}"?`}
                value={sectionInstruction}
                onChange={(e) => setSectionInstruction(e.target.value)}
                rows={3}
                className="text-xs"
              />
              <Button size="sm" onClick={() => handleImproveSection()} disabled={sectionImproveBusy || !sectionInstruction.trim()}>
                {sectionImproveBusy ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Sparkles className="h-3.5 w-3.5" />}
                {sectionImproveBusy ? "Improving…" : "Apply"}
              </Button>
              {sectionImproveResult?.kind === "failed" ? (
                <p className="text-xs text-destructive">{sectionImproveResult.message}</p>
              ) : sectionImproveResult?.kind === "clarification" ? (
                <div className="flex flex-col gap-2 rounded-md border border-amber-400/60 bg-amber-50 p-2 dark:border-amber-900 dark:bg-amber-950/30">
                  <p className="text-xs">{sectionImproveResult.message}</p>
                  <Textarea
                    placeholder="Answer the question above…"
                    value={sectionClarificationAnswer}
                    onChange={(e) => setSectionClarificationAnswer(e.target.value)}
                    rows={3}
                    className="text-xs"
                  />
                  <Button
                    size="sm"
                    onClick={handleSubmitSectionClarification}
                    disabled={sectionImproveBusy || !sectionClarificationAnswer.trim()}
                    className="w-fit"
                  >
                    {sectionImproveBusy ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Sparkles className="h-3.5 w-3.5" />}
                    {sectionImproveBusy ? "Submitting…" : "Submit answer & retry"}
                  </Button>
                </div>
              ) : null}
            </div>
          ) : null}
        </div>

        <RegenerateSectionAction
          artifactId={artifactId}
          sectionTitle={activeSectionTitle}
          enabled={artifactEditable && documentHasRealSections}
          disabledReason={
            !artifactEditable
              ? "Only a DRAFT document can be regenerated."
              : !documentHasRealSections
                ? "This document has no named sections — use Run Agent (Draft) above instead."
                : null
          }
          triggeredByUserId={triggeredByUserId}
          onApplied={onApplied}
        />
        <SummarizeChangesAction artifactId={artifactId} refreshKey={versionKey} />
        <AskQuestionsAction artifactId={artifactId} triggeredByUserId={triggeredByUserId} />
      </CardContent>
    </Card>
  );
}
