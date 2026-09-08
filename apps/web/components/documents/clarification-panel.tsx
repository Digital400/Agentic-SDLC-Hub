"use client";

import { useState } from "react";
import { Loader2, MessageSquareReply } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Textarea } from "@/components/ui/textarea";
import { ApiError } from "@/lib/api";
import { runAgentAndApply } from "@/lib/run-agent";

export interface ClarificationAppliedOutcome {
  artifactStatus: string;
  workflowNodeStatus: string;
}

/**
 * Shown instead of (above) the normal content view whenever an artifact's
 * current version is an agent's clarification request (see
 * markdown-sections.ts's isClarificationRequest) — the agent's own
 * questions are already visible in the content pane below this panel;
 * this just adds the one thing that was previously missing: a way to
 * answer them without a direct API call.
 *
 * Answers are submitted as one freeform block under a single
 * `clarification_answers` input_context key — build_prioritized_context
 * (see apps/api/app/services/ai_generation.py) folds every input_context
 * entry into the model's P0 instruction regardless of key name, so a
 * single consolidated answer works exactly as well as one key per
 * question, without needing to parse the agent's bullet list back apart.
 * If the re-run still needs clarification (a real, sometimes multi-round
 * outcome — an agent may need a second pass once the first round's
 * answers raise new questions), this same panel simply reappears for the
 * next round.
 *
 * `freeformInputKeys` (e.g. "stakeholder_request" for Requirement Intake —
 * see ArtifactDocument.freeformInputKeys) must be re-submitted here too:
 * GraphEngineService.resolve_required_inputs requires every one of the
 * stage's freeform inputs on *every* run, including this retry, and a
 * clarification round is a brand new agent run — it doesn't inherit the
 * original run's input_context server-side. Skipping these fields would
 * fail with "required input '<key>' was not provided in input_context"
 * even though the user already answered them once, further up the page.
 */
export function ClarificationPanel({
  projectId,
  workflowNodeId,
  freeformInputKeys,
  triggeredByUserId,
  onApplied,
}: {
  projectId: string;
  workflowNodeId: string;
  freeformInputKeys: string[];
  triggeredByUserId: string | null;
  onApplied: (outcome: ClarificationAppliedOutcome) => void;
}) {
  const [answer, setAnswer] = useState("");
  const [freeformValues, setFreeformValues] = useState<Record<string, string>>({});
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const missingFreeformKeys = freeformInputKeys.filter((key) => !freeformValues[key]?.trim());
  const canSubmit = answer.trim().length > 0 && missingFreeformKeys.length === 0;

  async function handleSubmit() {
    if (triggeredByUserId === null) {
      setError("No users exist yet to attribute this run to.");
      return;
    }
    if (!canSubmit) return;
    setSubmitting(true);
    setError(null);
    try {
      const result = await runAgentAndApply({
        projectId,
        workflowNodeId,
        action: "draft",
        triggeredByUserId,
        inputContext: {
          ...Object.fromEntries(freeformInputKeys.map((key) => [key, freeformValues[key].trim()])),
          clarification_answers: answer.trim(),
        },
      });
      if (result.run.status !== "COMPLETED" || result.saved === null) {
        setError(result.run.error_message ?? "The run did not complete.");
        return;
      }
      setAnswer("");
      onApplied({ artifactStatus: result.saved.artifactStatus, workflowNodeStatus: result.saved.workflowNodeStatus });
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to submit your answer.");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <Card className="border-amber-400/60 bg-amber-50 dark:border-amber-900 dark:bg-amber-950/30">
      <CardHeader>
        <CardTitle className="text-sm">Clarification needed</CardTitle>
        <CardDescription>
          The agent needs more information before it can draft this artifact — its questions are in the content
          pane. Answer them below and it will re-run with your answers.
        </CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-2">
        {freeformInputKeys.map((key) => (
          <div key={key} className="flex flex-col gap-1">
            <label className="text-xs font-medium capitalize">{key.replace(/_/g, " ")}</label>
            <Textarea
              placeholder={`Re-enter ${key.replace(/_/g, " ")}…`}
              value={freeformValues[key] ?? ""}
              onChange={(e) => setFreeformValues((prev) => ({ ...prev, [key]: e.target.value }))}
              rows={3}
              className="text-sm"
            />
          </div>
        ))}
        <Textarea
          placeholder="Answer the questions above, in any order or format…"
          value={answer}
          onChange={(e) => setAnswer(e.target.value)}
          rows={5}
          className="text-sm"
        />
        <Button size="sm" onClick={handleSubmit} disabled={submitting || !canSubmit} className="w-fit">
          {submitting ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <MessageSquareReply className="h-3.5 w-3.5" />}
          {submitting ? "Submitting…" : "Submit answers & regenerate"}
        </Button>
        {error ? <p className="text-xs text-destructive">{error}</p> : null}
      </CardContent>
    </Card>
  );
}
