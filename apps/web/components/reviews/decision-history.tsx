import { History } from "lucide-react";

import { ReviewStatusBadge } from "@/components/status-badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { EmptyState } from "@/components/ui/empty-state";
import { formatDate } from "@/lib/format";
import type { ReviewDecisionHistoryEntry } from "@/lib/types";

// 8. Decision history — every past, already-decided round for this
// artifact, oldest first, so the review's full paper trail is visible.
export function DecisionHistory({ history }: { history: ReviewDecisionHistoryEntry[] }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-sm">Decision history</CardTitle>
      </CardHeader>
      <CardContent>
        {history.length === 0 ? (
          <EmptyState icon={History} title="No prior rounds" description="This is the first review round for this artifact." className="border-none py-6" />
        ) : (
          <ol className="flex flex-col gap-3 border-l border-border pl-4">
            {history.map((entry) => (
              <li key={entry.id} className="relative">
                <span className="absolute -left-[21px] top-1 h-2.5 w-2.5 rounded-full bg-border" />
                <div className="flex items-center gap-2">
                  <ReviewStatusBadge status={entry.status} />
                  <span className="text-xs text-muted-foreground">
                    v{entry.versionNumber} · {entry.reviewerName} · {formatDate(entry.decidedAt)}
                  </span>
                </div>
                {entry.comment ? <p className="mt-1 text-sm text-muted-foreground">{entry.comment}</p> : null}
              </li>
            ))}
          </ol>
        )}
      </CardContent>
    </Card>
  );
}
