import { GithubIntegrationView } from "@/components/github-integration/github-integration-view";
import { PageHeader } from "@/components/layout/page-header";
import { api } from "@/lib/api";
import { toGithubConnectionItem, toProject } from "@/lib/mappers";

export default async function GithubIntegrationPage() {
  const [connections, users, projectsResponse] = await Promise.all([
    api.github.listConnections(),
    api.users.list(),
    api.projects.list(),
  ]);

  const connection = connections.find((c) => c.status === "CONNECTED") ?? connections[0] ?? null;
  const projects = projectsResponse.items.map(toProject);
  const currentUserId = users[0]?.id ?? null;

  return (
    <div>
      <PageHeader
        title="GitHub"
        description="Connect a GitHub account and configure a read-only repository scan — no push, no branch creation."
      />
      <GithubIntegrationView
        connection={connection ? toGithubConnectionItem(connection) : null}
        projects={projects.map((p) => ({ id: p.id, name: p.name }))}
        currentUserId={currentUserId}
      />
    </div>
  );
}
