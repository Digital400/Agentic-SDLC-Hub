import { MessageSquare } from "lucide-react";

import { Avatar } from "@/components/ui/avatar";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { EmptyState } from "@/components/ui/empty-state";
import { Textarea } from "@/components/ui/textarea";
import { formatRelativeTime } from "@/lib/format";
import type { ArtifactCommentItem } from "@/lib/types";

// Read-only preview — posting is a placeholder (no comment backend yet).
export function CommentsPanel({ comments }: { comments: ArtifactCommentItem[] }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-sm">Review comments</CardTitle>
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
          <Textarea placeholder="Commenting isn't available in this preview yet." disabled rows={2} />
          <Button size="sm" className="mt-2 w-full" disabled>
            Post comment
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}
