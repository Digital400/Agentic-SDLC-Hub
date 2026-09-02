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
 */
export function ClarificationPanel({
  projectId,
  workflowNodeId,
  triggeredByUserId,
  onApplied,
}: {
  projectId: string;
  workflowNodeId: string;
  triggeredByUserId: string | null;
  onApplied: (outcome: ClarificationAppliedOutcome) => void;
}) {
  const [answer, setAnswer] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleSubmit() {
    if (triggeredByUserId === null) {
      setError("No users exist yet to attribute this run to.");
      return;
    }
    if (!answer.trim()) return;
    setSubmitting(true);
    setError(null);
    try {
      const result = await runAgentAndApply({
        projectId,
        workflowNodeId,
        action: "draft",
        triggeredByUserId,
        inputContext: { clarification_answers: answer.trim() },
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
        <Textarea
          placeholder="Answer the questions above, in any order or format…"
          value={answer}
          onChange={(e) => setAnswer(e.target.value)}
          rows={5}
          className="text-sm"
        />
        <Button size="sm" onClick={handleSubmit} disabled={submitting || !answer.trim()} className="w-fit">
          {submitting ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <MessageSquareReply className="h-3.5 w-3.5" />}
          {submitting ? "Submitting…" : "Submit answers & regenerate"}
        </Button>
        {error ? <p className="text-xs text-destructive">{error}</p> : null}
      </CardContent>
    </Card>
  );
}
