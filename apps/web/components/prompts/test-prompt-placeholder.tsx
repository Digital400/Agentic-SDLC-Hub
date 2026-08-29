import { FlaskConical } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Textarea } from "@/components/ui/textarea";

// 5. Test prompt placeholder — no real model calls are wired up yet (see
// docs/mvp-plan.md), so this is honestly disabled rather than faking a
// response, consistent with the Agent Actions panel in the document editor.
export function TestPromptPlaceholder() {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-sm">Test prompt</CardTitle>
        <CardDescription>Run this prompt against sample input to preview its output. Not wired up yet.</CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-2">
        <Textarea placeholder="Sample input for this stage…" disabled rows={3} />
        <Button disabled className="w-full">
          <FlaskConical className="h-3.5 w-3.5" />
          Run test
        </Button>
        <p className="text-xs text-muted-foreground">
          Test runs will be available once AI drafting is wired up — see docs/mvp-plan.md.
        </p>
      </CardContent>
    </Card>
  );
}
