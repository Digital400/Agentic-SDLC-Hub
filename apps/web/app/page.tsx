import { AlertTriangle, Bot, ClipboardCheck, FileText, FolderKanban, Loader2 } from "lucide-react";

import { PageHeader } from "@/components/layout/page-header";
import { AverageStageTime } from "@/components/dashboard/average-stage-time";
import { PendingReviewsTable } from "@/components/dashboard/pending-reviews-table";
import { RecentActivityList, type RecentActivityItem } from "@/components/dashboard/recent-activity-list";
import { StageProgressSummary } from "@/components/dashboard/stage-progress-summary";
import { StatCard } from "@/components/dashboard/stat-card";
import { AgentRunStatusBadge, ArtifactStatusBadge } from "@/components/status-badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { formatRelativeTime } from "@/lib/format";
import { api } from "@/lib/api";
import { toAgentRunSummary, toDocumentArtifact, toProject, toReviewItem, toWorkflowNode } from "@/lib/mappers";

function formatAgentName(agentKey: string): string {
  return agentKey
    .split("-")
    .map((word) => (word === "agent" ? "Agent" : word.charAt(0).toUpperCase() + word.slice(1)))
    .join(" ");
}

export default async function DashboardPage() {
  // No global "list all X across projects" endpoints exist yet for
  // agent-runs/workflow-nodes, so this aggregates per-project — fine at
  // today's scale (a handful of projects); revisit with a dedicated
  // dashboard endpoint if that stops being true.
  const { items: apiProjects } = await api.projects.list();
  const projects = apiProjects.map(toProject);

  const [perProjectNodes, perProjectArtifacts, perProjectRuns, apiReviews] = await Promise.all([
    Promise.all(projects.map((p) => api.projects.workflowNodes(p.id).then((nodes) => nodes.map(toWorkflowNode)))),
    Promise.all(projects.map((p) => api.projects.artifacts(p.id).then((a) => a.map(toDocumentArtifact)))),
    Promise.all(projects.map((p) => api.projects.agentRuns(p.id))),
    api.reviews.listAll(),
  ]);

  const reviews = apiReviews.map(toReviewItem);
  const documents = perProjectArtifacts.flat();
  const blockedWorkflowsCount = perProjectNodes.filter((nodes) => nodes.some((n) => n.status === "BLOCKED")).length;

  const totalProjects = projects.length;
  const projectsInProgress = projects.filter((p) => p.status === "ACTIVE").length;
  const pendingReviewsCount = reviews.filter((r) => r.status === "PENDING").length;

  const agentRuns = projects.flatMap((project, i) => {
    const nodesByAgentKey = new Map(perProjectNodes[i].map((n) => [n.agentKey, n]));
    return perProjectRuns[i].map((run) => {
      const node = nodesByAgentKey.get(run.agent_key);
      return toAgentRunSummary(run, project.name, formatAgentName(run.agent_key), node?.name ?? "Unknown stage");
    });
  });

  const recentAgentRuns: RecentActivityItem[] = [...agentRuns]
    .sort((a, b) => (a.createdAt < b.createdAt ? 1 : -1))
    .slice(0, 5)
    .map((run) => ({
      id: run.id,
      title: run.agentName,
      subtitle: `${run.projectName} · ${run.workflowStageName}`,
      timestamp: formatRelativeTime(run.createdAt),
      href: `/projects/${run.projectId}`,
      badge: <AgentRunStatusBadge status={run.status} />,
    }));

  const recentArtifacts: RecentActivityItem[] = [...documents]
    .sort((a, b) => (a.updatedAt < b.updatedAt ? 1 : -1))
    .slice(0, 5)
    .map((doc) => ({
      id: doc.id,
      title: doc.title,
      subtitle: doc.projectName,
      timestamp: formatRelativeTime(doc.updatedAt),
      href: `/documents/${doc.id}`,
      badge: <ArtifactStatusBadge status={doc.status} />,
    }));

  return (
    <div>
      <PageHeader title="Dashboard" description="Overview across all projects" />

      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        <StatCard label="Total projects" value={totalProjects} icon={FolderKanban} href="/projects" />
        <StatCard label="Projects in progress" value={projectsInProgress} icon={Loader2} href="/projects" />
        <StatCard label="Pending reviews" value={pendingReviewsCount} icon={ClipboardCheck} href="/reviews" />
        <StatCard label="Blocked workflows" value={blockedWorkflowsCount} icon={AlertTriangle} href="/ops" tone="warning" />
      </div>

      <div className="mt-6 grid grid-cols-1 gap-4 xl:grid-cols-3">
        <Card className="xl:col-span-2">
          <CardHeader>
            <CardTitle className="text-base">Pending reviews</CardTitle>
            <CardDescription>Artifacts waiting on a human approval right now.</CardDescription>
          </CardHeader>
          <CardContent className="p-0">
            <PendingReviewsTable reviews={reviews} />
          </CardContent>
        </Card>

        <div className="flex flex-col gap-4">
          <Card>
            <CardHeader>
              <CardTitle className="text-base">Projects by SDLC stage</CardTitle>
              <CardDescription>Where active projects currently sit.</CardDescription>
            </CardHeader>
            <CardContent className="p-0">
              <StageProgressSummary projects={projects} />
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle className="text-base">Average time per stage</CardTitle>
              <CardDescription>Cycle-time tracking — future roadmap item.</CardDescription>
            </CardHeader>
            <CardContent className="p-0">
              <AverageStageTime />
            </CardContent>
          </Card>
        </div>
      </div>

      <div className="mt-4 grid grid-cols-1 gap-4 xl:grid-cols-2">
        <Card>
          <CardHeader className="flex-row items-center gap-2 space-y-0">
            <Bot className="h-4 w-4 text-muted-foreground" />
            <CardTitle className="text-base">Recent agent runs</CardTitle>
          </CardHeader>
          <CardContent className="p-0">
            <RecentActivityList items={recentAgentRuns} emptyMessage="No agent activity yet." />
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="flex-row items-center gap-2 space-y-0">
            <FileText className="h-4 w-4 text-muted-foreground" />
            <CardTitle className="text-base">Recent artifacts</CardTitle>
          </CardHeader>
          <CardContent className="p-0">
            <RecentActivityList items={recentArtifacts} emptyMessage="No artifacts yet." />
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
