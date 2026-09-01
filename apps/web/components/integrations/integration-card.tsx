import Link from "next/link";
import { Github, MessageSquare, Slack, Trello, Workflow } from "lucide-react";
import type { LucideIcon } from "lucide-react";

import { IntegrationStatusBadge } from "@/components/status-badge";
import { Button, buttonVariants } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import type { IntegrationItem, IntegrationProvider } from "@/lib/types";

const PROVIDER_ICON: Record<IntegrationProvider, LucideIcon> = {
  JIRA: Trello,
  CONFLUENCE: Workflow,
  GITHUB: Github,
  SLACK: Slack,
  TEAMS: MessageSquare,
  AZURE_DEVOPS: Workflow,
};

const PROVIDER_DESCRIPTION: Record<IntegrationProvider, string> = {
  JIRA: "Push story backlogs and sync issue status.",
  CONFLUENCE: "Publish approved artifacts as pages.",
  GITHUB: "Link commits/PRs to implementation stories.",
  SLACK: "Notify channels on review and release events.",
  TEAMS: "Notify channels on review and release events.",
  AZURE_DEVOPS: "Sync work items and pipeline status.",
};

// One card per planned integration (see docs/architecture.md's MCP
// integrations section). Connect is a real API call — it correctly comes
// back as "not implemented yet" until a real MCP tool exists (see
// apps/api/app/api/routes/integrations.py), so the failure banner is
// accurate, not a fake success.
export function IntegrationCard({
  integration,
  onConnect,
  busy,
}: {
  integration: IntegrationItem;
  onConnect: (id: string) => void;
  busy: boolean;
}) {
  const Icon = PROVIDER_ICON[integration.provider];

  return (
    <Card>
      <CardHeader className="flex-row items-start justify-between space-y-0">
        <div className="flex items-center gap-2">
          <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-md bg-secondary">
            <Icon className="h-4 w-4" />
          </div>
          <CardTitle className="text-sm">{integration.integrationName}</CardTitle>
        </div>
        <IntegrationStatusBadge status={integration.status} />
      </CardHeader>
      <CardContent className="space-y-3">
        <p className="text-xs text-muted-foreground">{PROVIDER_DESCRIPTION[integration.provider]}</p>
        <dl className="text-xs">
          <div className="flex items-center justify-between">
            <dt className="text-muted-foreground">Last sync</dt>
            <dd>{integration.lastSyncedAt ?? "Never"}</dd>
          </div>
          {integration.connectedByName ? (
            <div className="mt-1 flex items-center justify-between">
              <dt className="text-muted-foreground">Connected by</dt>
              <dd>{integration.connectedByName}</dd>
            </div>
          ) : null}
        </dl>
        {integration.provider === "GITHUB" || integration.provider === "JIRA" || integration.provider === "CONFLUENCE" ? (
          // GitHub, Jira, and Confluence are the three real integrations
          // (see app/services/github_integration.py / jira_integration.py /
          // confluence_integration.py) — each needs a real form
          // (credentials + project/space config), not the generic
          // one-click connect every other provider still uses (that one
          // stays a documented 501 until an MCP client exists for them too).
          <Link
            href={
              integration.provider === "GITHUB"
                ? "/settings/integrations/github"
                : integration.provider === "JIRA"
                  ? "/settings/integrations/jira"
                  : "/settings/integrations/confluence"
            }
            className={buttonVariants({ variant: "outline", size: "sm", className: "w-full" })}
          >
            {integration.status === "CONNECTED" ? "Manage connection" : "Configure"}
          </Link>
        ) : (
          <Button
            variant="outline"
            size="sm"
            className="w-full"
            disabled={integration.status === "CONNECTED" || busy}
            onClick={() => onConnect(integration.id)}
          >
            {integration.status === "CONNECTED" ? "Connected" : "Connect"}
          </Button>
        )}
      </CardContent>
    </Card>
  );
}
