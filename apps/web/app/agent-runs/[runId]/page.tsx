import Link from "next/link";
import { notFound } from "next/navigation";
import { BookOpen, Sparkles } from "lucide-react";

import { PageHeader } from "@/components/layout/page-header";
import { AgentRunStatusBadge } from "@/components/status-badge";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { EmptyState } from "@/components/ui/empty-state";
import { formatDate } from "@/lib/format";
import { api, ApiError } from "@/lib/api";
import { toAgentRunDetail } from "@/lib/mappers";

export default async function AgentRunDetailPage({ params }: { params: { runId: string } }) {
  let apiRun;
  try {
    apiRun = await api.agentRuns.get(params.runId);
  } catch (err) {
    if (err instanceof ApiError && err.status === 404) notFound();
    throw err;
  }

  const [project, nodes] = await Promise.all([
    api.projects.get(apiRun.project_id),
    api.projects.workflowNodes(apiRun.project_id),
  ]);
  const node = nodes.find((n) => n.id === apiRun.workflow_node_id);

  const run = toAgentRunDetail(apiRun, project.name, node?.name ?? "Unknown stage");

  return (
    <div>
      <PageHeader
        title={`${run.agentKey} — ${run.action}`}
        description={`${run.projectName} · ${run.workflowStageName}${run.promptVersion ? ` · prompt v${run.promptVersion}` : ""}`}
        actions={<AgentRunStatusBadge status={run.status} />}
      />

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-3">
        <div className="flex flex-col gap-4 xl:col-span-2">
          <Card>
            <CardHeader>
              <CardTitle className="text-sm">Output</CardTitle>
            </CardHeader>
            <CardContent>
              {run.errorMessage ? (
                <p className="text-sm text-destructive">{run.errorMessage}</p>
              ) : run.outputText ? (
                <pre className="max-h-[32rem] overflow-auto whitespace-pre-wrap rounded-md bg-muted/40 p-3 font-mono text-xs">
                  {run.outputText}
                </pre>
              ) : (
                <p className="text-sm text-muted-foreground">This run has no output yet.</p>
              )}
              {run.outputArtifactId ? (
                <Link
                  href={`/documents/${run.outputArtifactId}`}
                  className="mt-3 inline-block text-xs text-muted-foreground hover:text-foreground hover:underline"
                >
                  Open saved artifact →
                </Link>
              ) : null}
            </CardContent>
          </Card>

          {Object.keys(run.inputContext).length > 0 ? (
            <Card>
              <CardHeader>
                <CardTitle className="text-sm">Input context</CardTitle>
              </CardHeader>
              <CardContent className="space-y-2">
                {Object.entries(run.inputContext).map(([key, value]) => (
                  <div key={key} className="text-sm">
                    <span className="font-medium">{key}:</span>{" "}
                    <span className="text-muted-foreground">{String(value)}</span>
                  </div>
                ))}
              </CardContent>
            </Card>
          ) : null}
        </div>

        <div className="flex flex-col gap-4">
          <Card>
            <CardHeader className="flex-row items-center gap-2 space-y-0">
              <BookOpen className="h-4 w-4 text-muted-foreground" />
              <CardTitle className="text-sm">Retrieved knowledge sources</CardTitle>
            </CardHeader>
            <CardContent>
              {run.retrievedSources === null ? (
                <p className="text-xs text-muted-foreground">
                  Retrieval did not run for this attempt (it failed before reaching that step).
                </p>
              ) : run.retrievedSources.length === 0 ? (
                <EmptyState
                  icon={BookOpen}
                  title="No relevant knowledge found"
                  description="This run proceeded using project context alone."
                  className="border-none py-6"
                />
              ) : (
                <ul className="flex flex-col gap-3">
                  {run.retrievedSources.map((source) => (
                    <li key={source.chunkId} className="rounded-md border border-border p-2.5">
                      <div className="mb-1 flex items-center justify-between gap-2">
                        <Link
                          href={`/knowledge-base/${source.sourceId}`}
                          className="truncate text-xs font-medium hover:underline"
                        >
                          {source.sourceTitle}
                        </Link>
                        <Badge variant="outline" className="shrink-0 text-[10px]">
                          {Math.round(source.similarity * 100)}% match
                        </Badge>
                      </div>
                      <p className="line-clamp-3 text-xs text-muted-foreground">{source.snippet}</p>
                    </li>
                  ))}
                </ul>
              )}
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="flex-row items-center gap-2 space-y-0">
              <Sparkles className="h-4 w-4 text-muted-foreground" />
              <CardTitle className="text-sm">Run details</CardTitle>
            </CardHeader>
            <CardContent className="space-y-2 text-xs">
              <div className="flex justify-between">
                <span className="text-muted-foreground">Started</span>
                <span>{run.startedAt ? formatDate(run.startedAt) : "—"}</span>
              </div>
              <div className="flex justify-between">
                <span className="text-muted-foreground">Completed</span>
                <span>{run.completedAt ? formatDate(run.completedAt) : "—"}</span>
              </div>
              {run.tokenUsage ? (
                <div className="flex justify-between">
                  <span className="text-muted-foreground">Tokens</span>
                  <span>
                    {run.tokenUsage.prompt_tokens} in / {run.tokenUsage.completion_tokens} out
                  </span>
                </div>
              ) : null}
              {run.cost !== null ? (
                <div className="flex justify-between">
                  <span className="text-muted-foreground">Cost</span>
                  <span>${run.cost.toFixed(6)}</span>
                </div>
              ) : null}
            </CardContent>
          </Card>
        </div>
      </div>
    </div>
  );
}
