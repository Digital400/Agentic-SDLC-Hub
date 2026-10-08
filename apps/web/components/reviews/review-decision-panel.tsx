"use client";

import { useEffect, useRef, useState } from "react";
import { CheckCircle2, Loader2, Plus, RotateCcw, ShieldAlert, Trash2, XCircle } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Select } from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import { cn } from "@/lib/utils";

export type ReviewDecision = "APPROVED" | "NEEDS_CHANGES" | "REJECTED";

/** One structured piece of reviewer feedback — mirrors the backend's
 * ReviewCommentInput. `sectionTitle` null means general, document-wide
 * feedback (linking a comment to a section is "where possible", not
 * required). */
export interface StructuredComment {
  body: string;
  sectionTitle: string | null;
}

interface DecisionConfig {
  decision: ReviewDecision;
  label: string;
  icon: typeof CheckCircle2;
  buttonVariant: "default" | "outline" | "destructive";
  /** Approving without a comment is fine; the other two require one — this
   * mirrors the backend's actual validation (see reviews.py), not just a
   * UI nicety. */
  commentRequired: boolean;
  commentPlaceholder: string;
  confirmLabel: string;
}

const DECISIONS: DecisionConfig[] = [
  {
    decision: "APPROVED",
    label: "Approve",
    icon: CheckCircle2,
    buttonVariant: "default",
    commentRequired: false,
    commentPlaceholder: "Optional note for the record…",
    confirmLabel: "Confirm approval",
  },
  {
    decision: "NEEDS_CHANGES",
    label: "Request changes",
    icon: RotateCcw,
    buttonVariant: "outline",
    commentRequired: true,
    commentPlaceholder: "What needs to change before this can be approved? (required)",
    confirmLabel: "Confirm request",
  },
  {
    decision: "REJECTED",
    label: "Reject",
    icon: XCircle,
    buttonVariant: "destructive",
    commentRequired: true,
    commentPlaceholder: "Why is this being rejected? (required)",
    confirmLabel: "Confirm rejection",
  },
];

const EMPTY_STRUCTURED_COMMENT: StructuredComment = { body: "", sectionTitle: null };

export function ReviewDecisionPanel({
  disabled,
  disabledReason,
  approveDisabled,
  approveDisabledReason,
  sectionTitles,
  submitting,
  onDecide,
  onRequestChanges,
}: {
  /** True once the review is no longer PENDING — no more decisions possible. */
  disabled: boolean;
  disabledReason?: string;
  /** True until the reviewer checklist is fully checked. */
  approveDisabled: boolean;
  approveDisabledReason?: string;
  /** This artifact's current section titles — populates each structured
   * comment's "link to section" dropdown (rule 2). */
  sectionTitles: string[];
  /** True for the round-trip of a decision already in flight — disables
   * every action here (the parent also shows a full-screen lock overlay
   * for the same span) so a slow request can't be double-submitted or
   * look like nothing happened. */
  submitting: boolean;
  onDecide: (decision: Exclude<ReviewDecision, "NEEDS_CHANGES">, comment: string | null) => void;
  /** Rule 1: one or more structured comments explaining what needs to
   * change, each optionally linked to a section (rule 2). */
  onRequestChanges: (comments: StructuredComment[]) => void;
}) {
  const [active, setActive] = useState<ReviewDecision | null>(null);
  const [comment, setComment] = useState("");
  const [structuredComments, setStructuredComments] = useState<StructuredComment[]>([EMPTY_STRUCTURED_COMMENT]);

  // Keep the confirmation box (and its own "Saving…" spinner) open for the
  // whole request instead of vanishing the instant Confirm is clicked —
  // only clear it once the parent reports the request actually finished.
  // Tracks the previous `submitting` value rather than closing on every
  // `false` so this never fires on first mount.
  const wasSubmitting = useRef(false);
  useEffect(() => {
    if (wasSubmitting.current && !submitting) {
      setActive(null);
      setComment("");
      setStructuredComments([EMPTY_STRUCTURED_COMMENT]);
    }
    wasSubmitting.current = submitting;
  }, [submitting]);

  const config = DECISIONS.find((d) => d.decision === active);
  let canConfirm = false;
  if (active === "NEEDS_CHANGES") {
    canConfirm = structuredComments.some((c) => c.body.trim().length > 0);
  } else if (config) {
    canConfirm = !config.commentRequired || comment.trim().length > 0;
  }

  function startDecision(decision: ReviewDecision) {
    setActive(decision);
    setComment("");
    setStructuredComments([EMPTY_STRUCTURED_COMMENT]);
  }

  function updateStructuredComment(index: number, patch: Partial<StructuredComment>) {
    setStructuredComments((prev) => prev.map((c, i) => (i === index ? { ...c, ...patch } : c)));
  }

  function addStructuredComment() {
    setStructuredComments((prev) => [...prev, EMPTY_STRUCTURED_COMMENT]);
  }

  function removeStructuredComment(index: number) {
    setStructuredComments((prev) => (prev.length > 1 ? prev.filter((_, i) => i !== index) : prev));
  }

  function confirm() {
    if (!config || !canConfirm || submitting) return;
    // Closing (and resetting comment state) happens once `submitting`
    // reports the request finished — see the effect above — not here,
    // so the box stays open with its own spinner for the whole round-trip.
    if (config.decision === "NEEDS_CHANGES") {
      onRequestChanges(
        structuredComments
          .map((c) => ({ body: c.body.trim(), sectionTitle: c.sectionTitle }))
          .filter((c) => c.body.length > 0)
      );
    } else {
      onDecide(config.decision, comment.trim() || null);
    }
  }

  return (
    <Card className={cn(active ? "ring-1 ring-primary" : undefined)}>
      <CardHeader>
        <CardTitle className="text-sm">Decision</CardTitle>
        <CardDescription>This is the human approval gate — a decision here changes the artifact status.</CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-3">
        {disabled ? (
          <p className="flex items-center gap-2 rounded-md bg-muted/50 px-3 py-2 text-xs text-muted-foreground">
            <ShieldAlert className="h-4 w-4 shrink-0" />
            {disabledReason ?? "This review has already been decided."}
          </p>
        ) : (
          <>
            <div className="grid grid-cols-1 gap-2 sm:grid-cols-3">
              {DECISIONS.map((d) => {
                const isApprove = d.decision === "APPROVED";
                const itemDisabled = submitting || (isApprove && approveDisabled);
                return (
                  <Button
                    key={d.decision}
                    variant={d.buttonVariant}
                    onClick={() => startDecision(d.decision)}
                    disabled={itemDisabled}
                    title={!submitting && itemDisabled ? approveDisabledReason : undefined}
                  >
                    <d.icon className="h-4 w-4" />
                    {d.label}
                  </Button>
                );
              })}
            </div>
            {approveDisabled ? (
              <p className="text-xs text-muted-foreground">{approveDisabledReason}</p>
            ) : null}
          </>
        )}

        {active && config ? (
          <div className="flex flex-col gap-2 rounded-md border border-border bg-muted/40 p-3">
            <p className="text-sm font-medium">{config.confirmLabel}</p>

            {active === "NEEDS_CHANGES" ? (
              <div className="flex flex-col gap-2">
                {structuredComments.map((c, i) => (
                  <div key={i} className="flex flex-col gap-1 rounded-md border border-border bg-background p-2">
                    <div className="flex items-center gap-1.5">
                      <Select
                        value={c.sectionTitle ?? ""}
                        onChange={(e) => updateStructuredComment(i, { sectionTitle: e.target.value || null })}
                        className="text-xs"
                      >
                        <option value="">General feedback (whole document)</option>
                        {sectionTitles.map((title) => (
                          <option key={title} value={title}>
                            {title}
                          </option>
                        ))}
                      </Select>
                      {structuredComments.length > 1 ? (
                        <Button
                          variant="ghost"
                          size="icon"
                          className="h-7 w-7 shrink-0"
                          onClick={() => removeStructuredComment(i)}
                          aria-label="Remove this comment"
                        >
                          <Trash2 className="h-3.5 w-3.5" />
                        </Button>
                      ) : null}
                    </div>
                    <Textarea
                      value={c.body}
                      onChange={(e) => updateStructuredComment(i, { body: e.target.value })}
                      placeholder="What needs to change here? (required)"
                      rows={2}
                      autoFocus={i === 0}
                    />
                  </div>
                ))}
                <Button variant="outline" size="sm" className="w-fit" onClick={addStructuredComment}>
                  <Plus className="h-3.5 w-3.5" />
                  Add another comment
                </Button>
              </div>
            ) : (
              <Textarea
                value={comment}
                onChange={(e) => setComment(e.target.value)}
                placeholder={config.commentPlaceholder}
                rows={3}
                autoFocus
              />
            )}

            <div className="flex justify-end gap-2">
              <Button variant="ghost" size="sm" onClick={() => setActive(null)} disabled={submitting}>
                Cancel
              </Button>
              <Button variant={config.buttonVariant} size="sm" onClick={confirm} disabled={!canConfirm || submitting}>
                {submitting ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : null}
                {submitting ? "Saving…" : config.confirmLabel}
              </Button>
            </div>
          </div>
        ) : null}
      </CardContent>
    </Card>
  );
}
