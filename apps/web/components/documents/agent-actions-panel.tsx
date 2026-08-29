import { MessageCircleQuestion, Sparkles, RefreshCw, ListChecks } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";

// Placeholder only — no AI drafting is wired up yet (see docs/mvp-plan.md).
// Buttons are disabled rather than faking an action, consistent with how
// Settings/Knowledge Base handle other not-yet-built features.
export function AgentActionsPanel({ activeSectionTitle }: { activeSectionTitle: string | null }) {
  const sectionActions = [
    { icon: Sparkles, label: "Improve section" },
    { icon: RefreshCw, label: "Regenerate section" },
  ];
  const documentActions = [
    { icon: MessageCircleQuestion, label: "Ask questions" },
    { icon: ListChecks, label: "Summarize changes" },
  ];

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-sm">Agent actions</CardTitle>
        <CardDescription>
          {activeSectionTitle ? `Acting on "${activeSectionTitle}"` : "Select a section to act on it directly."}
        </CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-1.5">
        {sectionActions.map((action) => (
          <Button key={action.label} variant="outline" size="sm" className="justify-start" disabled>
            <action.icon className="h-3.5 w-3.5" />
            {action.label}
          </Button>
        ))}
        {documentActions.map((action) => (
          <Button key={action.label} variant="outline" size="sm" className="justify-start" disabled>
            <action.icon className="h-3.5 w-3.5" />
            {action.label}
          </Button>
        ))}
        <p className="mt-1 text-xs text-muted-foreground">
          Agent actions will be available once AI drafting is wired up — see docs/mvp-plan.md.
        </p>
      </CardContent>
    </Card>
  );
}
