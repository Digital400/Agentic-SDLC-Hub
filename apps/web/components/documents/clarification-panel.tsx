"use client";

import { useEffect, useState } from "react";
import { CheckCircle2, ListPlus, Loader2, MessageSquareReply, Pencil } from "lucide-react";

import { LargeTextInput } from "@/components/documents/large-text-input";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Textarea } from "@/components/ui/textarea";
import { ApiError } from "@/lib/api";
import { answerScaffold, clarificationIntro, extractQuestions } from "@/lib/clarification";
import { clearDraft, exceedsInputLimit } from "@/lib/intake-text";
import { runAgentAndApply } from "@/lib/run-agent";
import { usePreviousRunInput } from "@/lib/use-previous-run-input";

export interface ClarificationAppliedOutcome {
  artifactStatus: string;
  workflowNodeStatus: string;
}

/**
 * Shown above the content view whenever an artifact's current version is an
 * agent's clarification request (see markdown-sections.ts's
 * isClarificationRequest).
 *
 * It exists to make the round trip obvious: the agent read what the user
 * already provided but needs more detail, so this panel shows (1) what the user
 * already submitted — read back from the previous run, never a blank "re-enter"
 * box, (2) the agent's questions right next to the answer box, and (3) any
 * answers from earlier rounds. Only the new answers need typing.
 *
 * Answers go in one `clarification_answers` input_context key — see
 * apps/api/app/services/ai_generation.py's build_prioritized_context, which
 * folds every input_context entry into the P0 instruction. Earlier rounds'
 * answers are re-sent with the new ones, because each round is a brand-new
 * agent run that doesn't inherit the previous run's input server-side. Every
 * freeform input (e.g. "stakeholder_request") must also be re-sent, since the
 * graph engine requires it on every run.
 */
export function ClarificationPanel({
  projectId,
  workflowNodeId,
  freeformInputKeys,
  triggeredByUserId,
  clarificationMarkdown,
  onApplied,
}: {
  projectId: string;
  workflowNodeId: string;
  freeformInputKeys: string[];
  triggeredByUserId: string | null;
  /** The agent's clarification text (the artifact's current content). */
  clarificationMarkdown: string;
  onApplied: (outcome: ClarificationAppliedOutcome) => void;
}) {
  const previous = usePreviousRunInput(projectId, workflowNodeId, freeformInputKeys);
  const [answer, setAnswer] = useState("");
  const [freeformValues, setFreeformValues] = useState<Record<string, string>>({});
  const [editingKeys, setEditingKeys] = useState<Record<string, boolean>>({});
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const questions = extractQuestions(clarificationMarkdown);
  const intro = clarificationIntro(clarificationMarkdown);

  // Pre-fill the inputs the user already provided (once loaded).
  useEffect(() => {
    if (previous.loading) return;
    setFreeformValues((prev) => {
      const next = { ...prev };
      for (const key of freeformInputKeys) if (!next[key]?.trim() && previous.values[key]) next[key] = previous.values[key];
      return next;
    });
  }, [previous.loading, previous.values, freeformInputKeys]);

  const missingFreeformKeys = freeformInputKeys.filter((key) => !freeformValues[key]?.trim());
  const canSubmit = answer.trim().length > 0 && missingFreeformKeys.length === 0 && !exceedsInputLimit(freeformValues);

  async function handleSubmit() {
    if (triggeredByUserId === null) {
      setError("No users exist yet to attribute this run to.");
      return;
    }
    if (!canSubmit) return;
    setSubmitting(true);
    setError(null);
    const roundNumber = previous.priorAnswers ? 2 : 1;
    const combinedAnswers = previous.priorAnswers
      ? `${previous.priorAnswers}\n\nFollow-up answers (round ${roundNumber}):\n${answer.trim()}`
      : answer.trim();
    try {
      const result = await runAgentAndApply({
        projectId,
        workflowNodeId,
        action: "draft",
        triggeredByUserId,
        inputContext: {
          ...Object.fromEntries(freeformInputKeys.map((key) => [key, freeformValues[key].trim()])),
          clarification_answers: combinedAnswers,
        },
      });
      if (result.run.status !== "COMPLETED" || result.saved === null) {
        setError(result.run.error_message ?? "The run did not complete.");
        return;
      }
      for (const key of freeformInputKeys) clearDraft(`${projectId}:${workflowNodeId}:${key}`);
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
        <CardTitle className="text-sm">The agent needs a few more details</CardTitle>
        <CardDescription>
          Nothing was lost. The agent read what you provided but couldn&apos;t draft this document without more information. Answer its questions
          below and it will try again using your original request plus your answers.
        </CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-4">
        {/* 1. What the user already provided */}
        {freeformInputKeys.map((key) => {
          const label = key.replace(/_/g, " ");
          const value = freeformValues[key] ?? "";
          const editing = editingKeys[key] || (!previous.loading && value.trim() === "");
          return (
            <section key={key} className="flex flex-col gap-1.5">
              <div className="flex items-center justify-between">
                <h3 className="flex items-center gap-1.5 text-xs font-semibold capitalize">
                  <CheckCircle2 className="h-3.5 w-3.5 text-green-600" aria-hidden />
                  1. Your {label} <span className="font-normal normal-case text-muted-foreground">(already submitted)</span>
                </h3>
                {!editing ? (
                  <Button type="button" variant="ghost" size="sm" className="h-6 px-2 text-xs" onClick={() => setEditingKeys((p) => ({ ...p, [key]: true }))}>
                    <Pencil className="h-3 w-3" /> Edit
                  </Button>
                ) : null}
              </div>
              {previous.loading && value === "" ? (
                <p className="flex items-center gap-2 text-xs text-muted-foreground">
                  <Loader2 className="h-3 w-3 animate-spin" /> Loading what you submitted…
                </p>
              ) : editing ? (
                <>
                  {value.trim() === "" ? (
                    <p className="text-xs text-muted-foreground">We couldn&apos;t find your earlier {label}. Please enter it again.</p>
                  ) : null}
                  <LargeTextInput
                    label={key}
                    storageKey={`${projectId}:${workflowNodeId}:${key}`}
                    value={value}
                    onChange={(v) => setFreeformValues((prev) => ({ ...prev, [key]: v }))}
                    disabled={submitting}
                  />
                </>
              ) : (
                <div className="max-h-40 overflow-y-auto whitespace-pre-wrap rounded-md border border-border bg-background/70 p-2.5 text-xs">{value}</div>
              )}
            </section>
          );
        })}

        {/* 2. The agent's questions */}
        <section className="flex flex-col gap-1.5">
          <h3 className="text-xs font-semibold">2. What the agent is asking</h3>
          {intro ? <p className="text-xs text-muted-foreground">{intro}</p> : null}
          {questions.length > 0 ? (
            <ol className="list-decimal space-y-1 rounded-md border border-border bg-background/70 py-2.5 pl-7 pr-2.5 text-xs">
              {questions.map((q, i) => (
                <li key={i}>{q}</li>
              ))}
            </ol>
          ) : (
            <div className="whitespace-pre-wrap rounded-md border border-border bg-background/70 p-2.5 text-xs">{clarificationMarkdown}</div>
          )}
        </section>

        {/* Earlier rounds */}
        {previous.priorAnswers ? (
          <section className="flex flex-col gap-1.5">
            <h3 className="text-xs font-semibold">Your earlier answers</h3>
            <div className="max-h-32 overflow-y-auto whitespace-pre-wrap rounded-md border border-border bg-background/70 p-2.5 text-xs">
              {previous.priorAnswers}
            </div>
            <p className="text-xs text-muted-foreground">These are sent again with your new answers below.</p>
          </section>
        ) : null}

        {/* 3. The answer box */}
        <section className="flex flex-col gap-1.5">
          <div className="flex items-center justify-between">
            <h3 className="text-xs font-semibold">3. Your answers</h3>
            {questions.length > 0 && answer.trim() === "" ? (
              <Button type="button" variant="ghost" size="sm" className="h-6 px-2 text-xs" onClick={() => setAnswer(answerScaffold(questions))}>
                <ListPlus className="h-3 w-3" /> Answer question by question
              </Button>
            ) : null}
          </div>
          <Textarea
            placeholder="Answer the questions above, in any order or format…"
            value={answer}
            onChange={(e) => setAnswer(e.target.value)}
            rows={6}
            className="bg-background text-sm"
            aria-label="Your answers"
          />
        </section>

        <div className="flex flex-col gap-1.5">
          <Button size="sm" onClick={handleSubmit} disabled={submitting || !canSubmit} className="w-fit">
            {submitting ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <MessageSquareReply className="h-3.5 w-3.5" />}
            {submitting ? "Submitting…" : "Submit answers & regenerate"}
          </Button>
          <p className="text-xs text-muted-foreground">
            The agent will use your original {freeformInputKeys.map((k) => k.replace(/_/g, " ")).join(", ") || "request"}
            {previous.priorAnswers ? ", your earlier answers" : ""} and these answers.
          </p>
          {error ? <p className="text-xs text-destructive">{error}</p> : null}
        </div>
      </CardContent>
    </Card>
  );
}
