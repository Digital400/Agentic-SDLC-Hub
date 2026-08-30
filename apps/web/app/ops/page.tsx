import {
  AlertTriangle,
  Ban,
  Bot,
  CheckCircle2,
  Clock,
  Coins,
  Hash,
  PenLine,
  ThumbsDown,
  ThumbsUp,
  XCircle,
} from "lucide-react";

import { PageHeader } from "@/components/layout/page-header";
import { StatCard } from "@/components/dashboard/stat-card";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { OpsCostChartPlaceholder } from "@/components/ops/ops-cost-chart-placeholder";
import { OpsFailureList } from "@/components/ops/ops-failure-list";
import { OpsRunTable } from "@/components/ops/ops-run-table";
import { OpsStagePerformanceTable } from "@/components/ops/ops-stage-performance";
import { formatCost, formatDuration, formatPercent } from "@/lib/format";
import { api } from "@/lib/api";
import { toOpsSummary } from "@/lib/mappers";

export default async function OpsPage() {
  const summary = toOpsSummary(await api.ops.summary());

  return (
    <div>
      <PageHeader
        title="AI Ops Dashboard"
        description="Company-wide agent activity and the human approval gate — for management and tech leads."
      />

      {/* 1-4: run volume + duration */}
      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        <StatCard label="Total agent runs" value={summary.totalRuns} icon={Bot} />
        <StatCard label="Successful runs" value={summary.successfulRuns} icon={CheckCircle2} />
        <StatCard label="Failed runs" value={summary.failedRuns} icon={XCircle} tone={summary.failedRuns > 0 ? "warning" : "default"} />
        <StatCard label="Avg. run duration" value={formatDuration(summary.avgDurationSeconds)} icon={Clock} />
      </div>

      {/* 5-8: cost + the human approval gate */}
      <div className="mt-4 grid grid-cols-2 gap-4 lg:grid-cols-4">
        <StatCard label="Token usage" value={summary.totalTokens.toLocaleString()} icon={Hash} />
        <StatCard label="Estimated cost" value={formatCost(summary.totalCost)} icon={Coins} />
        <StatCard label="Approval rate" value={formatPercent(summary.approvalRate)} icon={ThumbsUp} />
        <StatCard label="Rejection rate" value={formatPercent(summary.rejectionRate)} icon={ThumbsDown} />
      </div>

      {/* 9-10: not-yet-tracked human editing behavior + blocked work */}
      <div className="mt-4 grid grid-cols-2 gap-4 lg:grid-cols-4">
        <StatCard label="Human change rate" value="Not tracked yet" icon={PenLine} />
        <StatCard
          label="Blocked workflows"
          value={summary.blockedWorkflowCount}
          icon={summary.blockedWorkflowCount > 0 ? AlertTriangle : Ban}
          tone={summary.blockedWorkflowCount > 0 ? "warning" : "default"}
        />
      </div>
      <p className="mt-2 text-xs text-muted-foreground" title={summary.humanChangeRateNote}>
        Human change rate: {summary.humanChangeRateNote}
      </p>

      <div className="mt-6 grid grid-cols-1 gap-4 xl:grid-cols-3">
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

      <div className="mt-4 grid grid-cols-1 gap-4 xl:grid-cols-3">
        <Card>
          <CardHeader>
            <CardTitle className="text-base">Cost over time</CardTitle>
            <CardDescription>Roadmap item — see the card for why.</CardDescription>
          </CardHeader>
          <CardContent>
            <OpsCostChartPlaceholder totalCost={summary.totalCost} />
          </CardContent>
        </Card>

        <Card className="xl:col-span-2">
          <CardHeader>
            <CardTitle className="text-base">Stage performance</CardTitle>
            <CardDescription>Run volume, success rate, and average duration by SDLC stage.</CardDescription>
          </CardHeader>
          <CardContent>
            <OpsStagePerformanceTable stages={summary.stagePerformance} />
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
