import { CheckCircle2 } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { formatRelativeTime } from "@/lib/format";
import { cn } from "@/lib/utils";
import type { AgentPromptVersion } from "@/lib/types";

// 3. Prompt version history, 4. Active version badge.
export function PromptVersionHistory({
  versions,
  selectedVersionId,
  onSelect,
  onActivate,
}: {
  versions: AgentPromptVersion[];
  selectedVersionId: string;
  onSelect: (id: string) => void;
  onActivate: (id: string) => void;
}) {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-sm">Version history</CardTitle>
      </CardHeader>
      <CardContent className="flex flex-col gap-2">
        {[...versions].reverse().map((version) => (
          <div
            key={version.id}
            className={cn(
              "flex items-center justify-between gap-2 rounded-md border px-3 py-2 text-sm",
              version.id === selectedVersionId ? "border-primary bg-muted/50" : "border-border"
            )}
          >
            <button onClick={() => onSelect(version.id)} className="flex flex-1 items-center gap-2 text-left">
              <span className="font-medium">v{version.version}</span>
              {version.isActive ? (
                <Badge variant="success" className="gap-1">
                  <CheckCircle2 className="h-3 w-3" />
                  Active
                </Badge>
              ) : null}
              <span className="text-xs text-muted-foreground">{formatRelativeTime(version.updatedAt)}</span>
            </button>
            {!version.isActive ? (
              <Button size="sm" variant="outline" onClick={() => onActivate(version.id)}>
                Activate
              </Button>
            ) : null}
          </div>
        ))}
      </CardContent>
    </Card>
  );
}
