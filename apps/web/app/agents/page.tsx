import Link from "next/link";
import { Bot } from "lucide-react";

import { PageHeader } from "@/components/layout/page-header";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { api } from "@/lib/api";
import { toAgentDefinitionSummary, toAgentPromptVersion } from "@/lib/mappers";

export default async function AgentsPage() {
  const apiAgents = await api.agentDefinitions.list();
  const agents = apiAgents.map(toAgentDefinitionSummary);

  // One extra request per agent for its active prompt version — a small,
  // fixed-size list (one per SDLC stage), so this is fine without a
  // dedicated "active prompt per agent" batch endpoint.
  const activePrompts = await Promise.all(
    agents.map(async (agent) => {
      try {
        return toAgentPromptVersion(await api.prompts.getByAgentKey(agent.agentKey));
      } catch {
        return null;
      }
    })
  );

  return (
    <div>
      <PageHeader
        title="Prompt Library"
        description="One agent per SDLC stage — drafts, improves, and validates that stage's artifact. Click an agent to view and edit its prompt."
      />

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-3">
        {agents.map((agent, i) => {
          const activePrompt = activePrompts[i];
          return (
            <Link key={agent.id} href={`/agents/${agent.agentKey}`}>
              <Card className="h-full transition-colors hover:border-primary/50">
                <CardHeader className="flex-row items-start justify-between space-y-0">
                  <div className="flex items-center gap-2">
                    <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-md bg-secondary">
                      <Bot className="h-4 w-4" />
                    </div>
                    <CardTitle className="text-sm">{agent.name}</CardTitle>
                  </div>
                  {activePrompt ? <Badge variant="success">v{activePrompt.version} active</Badge> : null}
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
            </Link>
          );
        })}
      </div>
    </div>
  );
}
