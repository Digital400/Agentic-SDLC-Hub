"use client";

import { useState } from "react";
import { MessageSquare } from "lucide-react";

import { Avatar } from "@/components/ui/avatar";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { EmptyState } from "@/components/ui/empty-state";
import { Textarea } from "@/components/ui/textarea";
import { formatRelativeTime } from "@/lib/format";
import type { ArtifactCommentItem } from "@/lib/types";

// 4. Comments — discussion attached to this review, separate from the
// decision itself (a decision can also carry its own comment; see
// ReviewDecisionPanel). Genuinely interactive: this is where the backend
// review-comment endpoint has a real client-side counterpart.
export function ReviewComments({
  comments,
  onAddComment,
}: {
  comments: ArtifactCommentItem[];
  onAddComment: (body: string) => void;
}) {
  const [draft, setDraft] = useState("");

  function submit() {
    if (draft.trim().length === 0) return;
    onAddComment(draft.trim());
    setDraft("");
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-sm">Comments</CardTitle>
      </CardHeader>
      <CardContent className="flex flex-col gap-3">
        {comments.length === 0 ? (
          <EmptyState icon={MessageSquare} title="No comments yet" className="border-none py-6" />
        ) : (
          <ul className="flex flex-col gap-3">
            {comments.map((comment) => (
              <li key={comment.id} className="flex gap-2">
                <Avatar name={comment.authorName} className="h-6 w-6 text-[10px]" />
                <div className="min-w-0">
                  <div className="flex items-baseline gap-2">
                    <span className="text-xs font-medium">{comment.authorName}</span>
                    <span className="text-xs text-muted-foreground">{formatRelativeTime(comment.createdAt)}</span>
                  </div>
                  <p className="text-xs text-muted-foreground">{comment.body}</p>
                </div>
              </li>
            ))}
          </ul>
        )}

        <div className="border-t border-border pt-3">
          <Textarea
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            placeholder="Add a comment for the record…"
            rows={2}
          />
          <Button size="sm" className="mt-2 w-full" onClick={submit} disabled={draft.trim().length === 0}>
            Post comment
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}
