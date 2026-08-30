"use client";

import { useState } from "react";
import Link from "next/link";
import { Loader2, MessageCircleQuestion, PlayCircle, RefreshCw, Sparkles, ListChecks } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Select } from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import { ApiError } from "@/lib/api";
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

// Real "Run Agent" flow (see lib/run-agent.ts) — the two placeholder
// section-level actions below it stay disabled; only document-level
// draft/improve/validate is wired up. See docs/mvp-plan.md for what's
// still ahead (per-section agent edits, "Ask questions", "Summarize
// changes").
export function AgentActionsPanel({
  activeSectionTitle,
  projectId,
  workflowNodeId,
  agentKey,
  freeformInputKeys,
  triggeredByUserId,
  onApplied,
}: {
  activeSectionTitle: string | null;
  projectId: string;
  workflowNodeId: string;
  agentKey: string;
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

  const sectionActions = [
    { icon: Sparkles, label: "Improve section" },
    { icon: RefreshCw, label: "Regenerate section" },
  ];
  const documentActions = [
    { icon: MessageCircleQuestion, label: "Ask questions" },
    { icon: ListChecks, label: "Summarize changes" },
  ];

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

        {sectionActions.map((action) => (
          <Button key={action.label} variant="outline" size="sm" className="justify-start" disabled>
            <action.icon className="h-3.5 w-3.5" />
            {action.label}
          </Button>
        ))}
        {documentActions.map((action) => (
          <Button key={action.label} variant="outline" size="sm" className="justify-start" disabled>
            <action.icon className="h-3.5 w-3.5" />
            {action.label}
          </Button>
        ))}
        <p className="mt-1 text-xs text-muted-foreground">
          Section-level and Q&amp;A agent actions aren&apos;t available yet — see docs/mvp-plan.md.
        </p>
      </CardContent>
    </Card>
  );
}
