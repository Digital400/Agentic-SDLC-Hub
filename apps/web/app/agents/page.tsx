import { Bot } from "lucide-react";

import { PageHeader } from "@/components/layout/page-header";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { mockAgents } from "@/lib/mock-data";

export default function AgentsPage() {
  return (
    <div>
      <PageHeader
        title="Agents"
        description="One agent per SDLC stage — drafts, improves, and validates that stage's artifact. Humans still approve every gated stage."
      />

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-3">
        {mockAgents.map((agent) => (
          <Card key={agent.id}>
            <CardHeader className="flex-row items-start justify-between space-y-0">
              <div className="flex items-center gap-2">
                <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-md bg-secondary">
                  <Bot className="h-4 w-4" />
                </div>
                <CardTitle className="text-sm">{agent.name}</CardTitle>
              </div>
              <Badge variant={agent.isActive ? "success" : "outline"}>{agent.isActive ? "Active" : "Inactive"}</Badge>
            </CardHeader>
            <CardContent>
              <p className="text-xs text-muted-foreground">{agent.description}</p>
              <dl className="mt-3 flex items-center justify-between border-t border-border pt-3 text-xs">
                <div>
                  <dt className="text-muted-foreground">Model</dt>
                  <dd className="font-mono">{agent.modelName}</dd>
                </div>
                <div className="text-right">
                  <dt className="text-muted-foreground">Total runs</dt>
                  <dd className="font-medium">{agent.totalRuns}</dd>
                </div>
              </dl>
            </CardContent>
          </Card>
        ))}
      </div>
    </div>
  );
}
