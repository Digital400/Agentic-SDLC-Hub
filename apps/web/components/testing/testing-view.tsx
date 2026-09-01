import { FlaskConical } from "lucide-react";

import { TestRunPanel } from "@/components/testing/test-run-panel";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { EmptyState } from "@/components/ui/empty-state";
import type { ImplementationTaskItem } from "@/lib/types";

/**
 * "Testing" tab — one card per ImplementationTask with an ACCEPTED
 * implementation run (Testing's own gate — see
 * app/models/test_run.py's class docstring for why this isn't the
 * workflow graph's stale `code_change` requirement). Tasks not yet
 * eligible aren't shown here at all — there's nothing to test yet.
 */
export function TestingView({
  eligibleTasks,
  currentUserId,
  reviewers,
}: {
  eligibleTasks: ImplementationTaskItem[];
  currentUserId: string | null;
  reviewers: { id: string; name: string }[];
}) {
  if (eligibleTasks.length === 0) {
    return (
      <EmptyState
        icon={FlaskConical}
        title="Nothing ready for testing yet"
        description="A task appears here once its Implementation Agent run has been accepted (Implementation Plan tab) — with a created PR or an existing code diff."
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
            <TestRunPanel taskId={task.id} currentUserId={currentUserId} reviewers={reviewers} />
          </CardContent>
        </Card>
      ))}
    </div>
  );
}
