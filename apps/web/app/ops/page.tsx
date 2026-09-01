import {
  AlertTriangle,
  Ban,
  Bot,
  CheckCircle2,
  Clock,
  Coins,
  Gauge,
  Hash,
  PenLine,
  ThumbsDown,
  ThumbsUp,
  XCircle,
} from "lucide-react";

import { PageHeader } from "@/components/layout/page-header";
import { StatCard } from "@/components/dashboard/stat-card";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { OpsBlockedWorkflowsList } from "@/components/ops/ops-blocked-workflows";
import { OpsCostByProjectList } from "@/components/ops/ops-cost-by-project";
import { OpsFailureList } from "@/components/ops/ops-failure-list";
import { OpsRagSourcesList } from "@/components/ops/ops-rag-sources";
import { OpsRunTable } from "@/components/ops/ops-run-table";
import { OpsStagePerformanceTable } from "@/components/ops/ops-stage-performance";
import { OpsValidationIssuesList } from "@/components/ops/ops-validation-issues";
import { formatCost, formatDuration, formatPercent } from "@/lib/format";
import { api } from "@/lib/api";
import { toOpsSummary } from "@/lib/mappers";

export default async function OpsPage() {
  const summary = toOpsSummary(await api.ops.summary());

  return (
    <div>
      <PageHeader
        title="AI Ops Dashboard"
        description="Company-wide agent activity, quality, cost, and the human approval gate — for management and tech leads."
      />

      {/* 1, 2, 3, 4, 5: run volume, quality, and token usage */}
      <div className="grid grid-cols-2 gap-4 lg:grid-cols-5">
        <StatCard label="Total agent runs" value={summary.totalRuns} icon={Bot} />
        <StatCard label="Success rate" value={formatPercent(summary.successRate)} icon={CheckCircle2} />
        <StatCard
          label="Failure rate"
          value={formatPercent(summary.failureRate)}
          icon={XCircle}
          tone={(summary.failureRate ?? 0) > 0 ? "warning" : "default"}
        />
        <StatCard label="Avg. quality score" value={formatPercent(summary.avgQualityScore)} icon={Gauge} />
        <StatCard
          label="Avg. token usage"
          value={summary.avgTokensPerRun === null ? "—" : Math.round(summary.avgTokensPerRun).toLocaleString()}
          icon={Hash}
        />
      </div>

      {/* Cost + the human approval gate */}
      <div className="mt-4 grid grid-cols-2 gap-4 lg:grid-cols-4">
        <StatCard label="Total cost" value={formatCost(summary.totalCost)} icon={Coins} />
        <StatCard label="Approval rate" value={formatPercent(summary.approvalRate)} icon={ThumbsUp} />
        <StatCard label="Rejection rate" value={formatPercent(summary.rejectionRate)} icon={ThumbsDown} />
        <StatCard
          label="Blocked workflows"
          value={summary.blockedWorkflowCount}
          icon={summary.blockedWorkflowCount > 0 ? AlertTriangle : Ban}
          tone={summary.blockedWorkflowCount > 0 ? "warning" : "default"}
        />
      </div>
      <p className="mt-2 text-xs text-muted-foreground" title={summary.humanChangeRateNote}>
        <PenLine className="mr-1 inline h-3 w-3" strokeWidth={1.5} />
        Human change rate: {summary.humanChangeRateNote}
      </p>

      {/* 6, 7: cost by project and by stage (stage table's own Cost column) */}
      <div className="mt-6 grid grid-cols-1 gap-4 xl:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle className="text-base">Cost by project</CardTitle>
            <CardDescription>Estimated spend, highest first.</CardDescription>
          </CardHeader>
          <CardContent>
            <OpsCostByProjectList projects={summary.costByProject} />
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="text-base">Stage performance &amp; cost</CardTitle>
            <CardDescription>Run volume, success rate, quality, iterations, and cost by SDLC stage.</CardDescription>
          </CardHeader>
          <CardContent className="overflow-x-auto">
            <OpsStagePerformanceTable stages={summary.stagePerformance} />
          </CardContent>
        </Card>
      </div>

      {/* 10, 11: quality signal and knowledge usage */}
      <div className="mt-4 grid grid-cols-1 gap-4 xl:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle className="text-base">Most common validation issues</CardTitle>
            <CardDescription>What validator agents flag most often, across every draft and revision.</CardDescription>
          </CardHeader>
          <CardContent>
            <OpsValidationIssuesList issues={summary.validationIssueFrequency} />
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="text-base">Most used RAG sources</CardTitle>
            <CardDescription>Knowledge Base sources agent runs actually cited.</CardDescription>
          </CardHeader>
          <CardContent>
            <OpsRagSourcesList sources={summary.ragSourceUsage} />
          </CardContent>
        </Card>
      </div>

      {/* 12: blocked workflows — the one actionable item on this page */}
      <Card className="mt-4">
        <CardHeader>
          <CardTitle className="text-base">Blocked workflows</CardTitle>
          <CardDescription>Every stage currently stuck, with why and where — click through to resolve it.</CardDescription>
        </CardHeader>
        <CardContent>
          <OpsBlockedWorkflowsList workflows={summary.blockedWorkflows} />
        </CardContent>
      </Card>

      <div className="mt-4 grid grid-cols-1 gap-4 xl:grid-cols-3">
        <Card className="xl:col-span-2">
          <CardHeader>
            <CardTitle className="text-base">Recent agent runs</CardTitle>
            <CardDescription>Every run across every project, most recent first.</CardDescription>
          </CardHeader>
          <CardContent className="overflow-x-auto p-0">
            <OpsRunTable runs={summary.recentRuns} />
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="text-base">Recent failures</CardTitle>
            <CardDescription>What is actually breaking, with the error.</CardDescription>
          </CardHeader>
          <CardContent>
            <OpsFailureList failures={summary.recentFailures} />
          </CardContent>
        </Card>
      </div>

      <p className="mt-3 text-xs text-muted-foreground">
        Average run duration across every stage: {formatDuration(summary.avgDurationSeconds)}.
      </p>
    </div>
  );
}
