"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { Loader2, MessageCircleQuestion, PlayCircle, RefreshCw, Sparkles, ListChecks } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Select } from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import { api, ApiError } from "@/lib/api";
import { runAgentAndApply } from "@/lib/run-agent";

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
// real (see app/services/section_improve_agent.py); "Regenerate section",
// "Ask questions", and "Summarize changes" stay disabled. See
// docs/mvp-plan.md for what's still ahead there.
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
  onApplied: (outcome: AgentRunOutcome) => void;
}) {
  const [action, setAction] = useState<AgentAction>("draft");
  const [freeformValues, setFreeformValues] = useState<Record<string, string>>({});
  const [running, setRunning] = useState(false);
  const [result, setResult] = useState<
    { kind: "failed"; message: string } | { kind: "completed"; runId: string; retrievedCount: number } | null
  >(null);

  const [improvingSection, setImprovingSection] = useState(false);
  const [sectionInstruction, setSectionInstruction] = useState("");
  const [sectionImproveBusy, setSectionImproveBusy] = useState(false);
  const [sectionImproveResult, setSectionImproveResult] = useState<
    { kind: "failed"; message: string } | { kind: "clarification"; message: string } | { kind: "applied" } | null
  >(null);

  const documentActions = [
    { icon: MessageCircleQuestion, label: "Ask questions" },
    { icon: ListChecks, label: "Summarize changes" },
  ];

  // Switching sections mid-instruction would silently apply to the wrong
  // one — close the panel and clear any stale instruction/result instead.
  useEffect(() => {
    setImprovingSection(false);
    setSectionInstruction("");
    setSectionImproveResult(null);
  }, [activeSectionTitle]);

  async function handleImproveSection() {
    if (triggeredByUserId === null) {
      setSectionImproveResult({ kind: "failed", message: "No users exist yet to attribute this run to." });
      return;
    }
    if (!activeSectionTitle || !sectionInstruction.trim()) return;
    setSectionImproveBusy(true);
    setSectionImproveResult(null);
    try {
      const response = await api.artifacts.improveSection(artifactId, {
        section_title: activeSectionTitle,
        instruction: sectionInstruction.trim(),
        triggered_by_user_id: triggeredByUserId,
      });
      if (response.needs_clarification) {
        setSectionImproveResult({ kind: "clarification", message: response.agent_run.output_text ?? "The agent needs more information before it can revise this section." });
        return;
      }
      setSectionImproveResult({ kind: "applied" });
      setSectionInstruction("");
      setImprovingSection(false);
      onApplied({ artifactStatus: response.artifact_status, workflowNodeStatus: response.workflow_node_status });
    } catch (err) {
      setSectionImproveResult({ kind: "failed", message: err instanceof ApiError ? err.message : "Failed to improve this section." });
    } finally {
      setSectionImproveBusy(false);
    }
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
      });

      if (run.status !== "COMPLETED" || saved === null) {
        setResult({ kind: "failed", message: run.error_message ?? "The run did not complete." });
        return;
      }

      setResult({ kind: "completed", runId: run.id, retrievedCount: run.retrieved_sources?.length ?? 0 });
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

          {freeformInputKeys.map((key) => (
            <Textarea
              key={key}
              placeholder={key.replace(/_/g, " ")}
              value={freeformValues[key] ?? ""}
              onChange={(e) => setFreeformValues((prev) => ({ ...prev, [key]: e.target.value }))}
              rows={2}
              className="mb-2 text-xs"
            />
          ))}

          <Button size="sm" className="w-full" onClick={handleRun} disabled={running}>
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
              <Button size="sm" onClick={handleImproveSection} disabled={sectionImproveBusy || !sectionInstruction.trim()}>
                {sectionImproveBusy ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Sparkles className="h-3.5 w-3.5" />}
                {sectionImproveBusy ? "Improving…" : "Apply"}
              </Button>
              {sectionImproveResult?.kind === "failed" ? (
                <p className="text-xs text-destructive">{sectionImproveResult.message}</p>
              ) : sectionImproveResult?.kind === "clarification" ? (
                <p className="text-xs text-muted-foreground">{sectionImproveResult.message}</p>
              ) : null}
            </div>
          ) : null}
        </div>

        <Button variant="outline" size="sm" className="justify-start" disabled>
          <RefreshCw className="h-3.5 w-3.5" />
          Regenerate section
        </Button>
        {documentActions.map((action) => (
          <Button key={action.label} variant="outline" size="sm" className="justify-start" disabled>
            <action.icon className="h-3.5 w-3.5" />
            {action.label}
          </Button>
        ))}
        <p className="mt-1 text-xs text-muted-foreground">
          &ldquo;Regenerate section&rdquo; and Q&amp;A agent actions aren&apos;t available yet — see docs/mvp-plan.md.
        </p>
      </CardContent>
    </Card>
  );
}
