import { JiraIntegrationView } from "@/components/jira-integration/jira-integration-view";
import { PageHeader } from "@/components/layout/page-header";
import { api } from "@/lib/api";
import { toJiraConnectionItem, toProject } from "@/lib/mappers";

export default async function JiraIntegrationPage() {
  const [connections, users, projectsResponse] = await Promise.all([
    api.jira.listConnections(),
    api.users.list(),
    api.projects.list(),
  ]);

  const connection = connections.find((c) => c.status === "CONNECTED") ?? connections[0] ?? null;
  const projects = projectsResponse.items.map(toProject);
  const currentUserId = users[0]?.id ?? null;

  return (
    <div>
      <PageHeader
        title="Jira"
        description="Connect a Jira Cloud project and push Epics, Stories, Implementation Tasks, and Testing bugs — preview and confirm before anything is created."
      />
      <JiraIntegrationView
        connection={connection ? toJiraConnectionItem(connection) : null}
        projects={projects.map((p) => ({ id: p.id, name: p.name }))}
        currentUserId={currentUserId}
      />
    </div>
  );
}
