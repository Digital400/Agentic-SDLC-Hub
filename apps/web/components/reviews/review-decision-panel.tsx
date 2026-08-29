"use client";

import { useState } from "react";
import { CheckCircle2, RotateCcw, ShieldAlert, XCircle } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Textarea } from "@/components/ui/textarea";
import { cn } from "@/lib/utils";

export type ReviewDecision = "APPROVED" | "NEEDS_CHANGES" | "REJECTED";

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

export function ReviewDecisionPanel({
  disabled,
  disabledReason,
  approveDisabled,
  approveDisabledReason,
  onDecide,
}: {
  /** True once the review is no longer PENDING — no more decisions possible. */
  disabled: boolean;
  disabledReason?: string;
  /** True until the reviewer checklist is fully checked. */
  approveDisabled: boolean;
  approveDisabledReason?: string;
  onDecide: (decision: ReviewDecision, comment: string | null) => void;
}) {
  const [active, setActive] = useState<ReviewDecision | null>(null);
  const [comment, setComment] = useState("");

  const config = DECISIONS.find((d) => d.decision === active);
  const canConfirm = config ? !config.commentRequired || comment.trim().length > 0 : false;

  function startDecision(decision: ReviewDecision) {
    setActive(decision);
    setComment("");
  }

  function confirm() {
    if (!config || !canConfirm) return;
    onDecide(config.decision, comment.trim() || null);
    setActive(null);
    setComment("");
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
                const itemDisabled = isApprove && approveDisabled;
                return (
                  <Button
                    key={d.decision}
                    variant={d.buttonVariant}
                    onClick={() => startDecision(d.decision)}
                    disabled={itemDisabled}
                    title={itemDisabled ? approveDisabledReason : undefined}
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
            <Textarea
              value={comment}
              onChange={(e) => setComment(e.target.value)}
              placeholder={config.commentPlaceholder}
              rows={3}
              autoFocus
            />
            <div className="flex justify-end gap-2">
              <Button variant="ghost" size="sm" onClick={() => setActive(null)}>
                Cancel
              </Button>
              <Button variant={config.buttonVariant} size="sm" onClick={confirm} disabled={!canConfirm}>
                {config.confirmLabel}
              </Button>
            </div>
          </div>
        ) : null}
      </CardContent>
    </Card>
  );
}
