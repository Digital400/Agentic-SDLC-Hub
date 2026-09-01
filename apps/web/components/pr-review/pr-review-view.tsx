import { GitPullRequest } from "lucide-react";

import { PRReviewPanel } from "@/components/pr-review/pr-review-panel";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { EmptyState } from "@/components/ui/empty-state";
import type { ImplementationTaskItem } from "@/lib/types";

/**
 * "PR Review" tab — one card per ImplementationTask with an ACCEPTED
 * implementation run AND a created pull request (this feature's own
 * gate, stricter than Testing's PR-or-diff either/or — see
 * app/models/pr_review_run.py's class docstring).
 */
export function PRReviewView({
  eligibleTasks,
  currentUserId,
}: {
  eligibleTasks: ImplementationTaskItem[];
  currentUserId: string | null;
}) {
  if (eligibleTasks.length === 0) {
    return (
      <EmptyState
        icon={GitPullRequest}
        title="Nothing ready for PR review yet"
        description="A task appears here once its Implementation Agent run has been accepted and a pull request has been created (Implementation Plan tab)."
      />
    );
  }

  return (
    <div className="flex flex-col gap-4">
      {eligibleTasks.map((task) => (
        <Card key={task.id}>
          <CardHeader className="flex-row items-center justify-between space-y-0">
            <div>
              <CardTitle className="text-sm">{task.title}</CardTitle>
              <p className="mt-1 text-xs text-muted-foreground">{task.description}</p>
            </div>
            <Badge variant="outline">{task.area}</Badge>
          </CardHeader>
          <CardContent>
            <PRReviewPanel taskId={task.id} currentUserId={currentUserId} />
          </CardContent>
        </Card>
      ))}
    </div>
  );
}
