import Link from "next/link";

import { AgentRunStatusBadge } from "@/components/status-badge";
import { EmptyState } from "@/components/ui/empty-state";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { formatCost, formatDuration, formatRelativeTime } from "@/lib/format";
import type { AgentRunStatus, OpsAgentRunRow } from "@/lib/types";
import { Bot } from "lucide-react";

// 2. Agent run table — every run across every project, most recent first.
export function OpsRunTable({ runs }: { runs: OpsAgentRunRow[] }) {
  if (runs.length === 0) {
    return <EmptyState icon={Bot} title="No agent runs yet" description="Runs will show up here as agents are used." />;
  }

  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>Project</TableHead>
          <TableHead>Stage</TableHead>
          <TableHead>Agent</TableHead>
          <TableHead>Action</TableHead>
          <TableHead>Status</TableHead>
          <TableHead>Duration</TableHead>
          <TableHead>Tokens</TableHead>
          <TableHead>Cost</TableHead>
          <TableHead>When</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {runs.map((run) => (
          <TableRow key={run.id}>
            <TableCell className="font-medium">
              <Link href={`/projects/${run.projectId}`} className="hover:underline">
                {run.projectName}
              </Link>
            </TableCell>
            <TableCell className="text-muted-foreground">{run.workflowStageName}</TableCell>
            <TableCell className="text-muted-foreground">{run.agentKey}</TableCell>
            <TableCell className="text-muted-foreground capitalize">{run.action}</TableCell>
            <TableCell>
              <AgentRunStatusBadge status={run.status as AgentRunStatus} />
            </TableCell>
            <TableCell className="text-muted-foreground">{formatDuration(run.durationSeconds)}</TableCell>
            <TableCell className="text-muted-foreground">{run.totalTokens ?? "—"}</TableCell>
            <TableCell className="text-muted-foreground">{formatCost(run.cost)}</TableCell>
            <TableCell className="text-right">
              <Link href={`/agent-runs/${run.id}`} className="text-xs text-muted-foreground hover:underline">
                {formatRelativeTime(run.createdAt)}
              </Link>
            </TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}
