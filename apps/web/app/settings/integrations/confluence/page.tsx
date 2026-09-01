import { ConfluenceIntegrationView } from "@/components/confluence-integration/confluence-integration-view";
import { PageHeader } from "@/components/layout/page-header";
import { api } from "@/lib/api";
import { toConfluenceConnectionItem, toProject } from "@/lib/mappers";

export default async function ConfluenceIntegrationPage() {
  const [connections, users, projectsResponse] = await Promise.all([
    api.confluence.listConnections(),
    api.users.list(),
    api.projects.list(),
  ]);

  const connection = connections.find((c) => c.status === "CONNECTED") ?? connections[0] ?? null;
  const projects = projectsResponse.items.map(toProject);
  const currentUserId = users[0]?.id ?? null;

  return (
    <div>
      <PageHeader
        title="Confluence"
        description="Connect a Confluence Cloud space and publish approved artifacts as pages — preview and confirm before anything is published."
      />
      <ConfluenceIntegrationView
        connection={connection ? toConfluenceConnectionItem(connection) : null}
        projects={projects.map((p) => ({ id: p.id, name: p.name }))}
        currentUserId={currentUserId}
      />
    </div>
  );
}
