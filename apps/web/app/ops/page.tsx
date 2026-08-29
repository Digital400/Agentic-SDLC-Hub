import { Activity, AlertTriangle, Bot, ClipboardCheck } from "lucide-react";

import { PageHeader } from "@/components/layout/page-header";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { formatStageLabel } from "@/lib/format";
import { mockProjects, mockReviews } from "@/lib/mock-data";

const STAGE_KEYS = [
  "requirement_intake",
  "problem_discovery",
  "solution_discovery",
  "hld",
  "story_crafting",
  "lld",
  "implementation",
  "testing",
  "infrastructure",
  "release",
  "maintenance",
];

// Mock — a real ops dashboard would compute this from how long each
// project has actually sat at a stage (WorkflowNode.updated_at deltas).
const MOCK_AVG_DAYS_IN_STAGE: Record<string, number> = {
  requirement_intake: 1,
  problem_discovery: 3,
  solution_discovery: 4,
  hld: 6,
  story_crafting: 2,
  lld: 5,
  implementation: 8,
  testing: 4,
  infrastructure: 2,
  release: 1,
  maintenance: 0,
};

export default function OpsPage() {
  const activeProjects = mockProjects.filter((p) => p.status === "ACTIVE");
  const stageCounts = STAGE_KEYS.map((key) => ({
    key,
    count: activeProjects.filter((p) => p.currentStage === key).length,
    avgDays: MOCK_AVG_DAYS_IN_STAGE[key] ?? 0,
  })).filter((s) => s.count > 0);

  const pendingReviews = mockReviews.filter((r) => r.status === "PENDING").length;

  const stats = [
    { label: "Active projects", value: activeProjects.length, icon: Activity },
    { label: "Pending approvals", value: pendingReviews, icon: ClipboardCheck },
    { label: "Agent runs (7d)", value: 46, icon: Bot },
    { label: "Stages with a bottleneck", value: stageCounts.filter((s) => s.avgDays >= 5).length, icon: AlertTriangle },
  ];

  return (
    <div>
      <PageHeader title="Ops Dashboard" description="Cross-project visibility: bottlenecks, approvals, agent activity." />

      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        {stats.map((stat) => (
          <Card key={stat.label}>
            <CardContent className="flex items-center justify-between p-4">
              <div>
                <p className="text-xs text-muted-foreground">{stat.label}</p>
                <p className="mt-1 text-2xl font-semibold">{stat.value}</p>
              </div>
              <stat.icon className="h-8 w-8 text-muted-foreground/50" strokeWidth={1.5} />
            </CardContent>
          </Card>
        ))}
      </div>

      <Card className="mt-6">
        <CardHeader>
          <CardTitle className="text-base">Where active projects are stuck</CardTitle>
        </CardHeader>
        <CardContent className="p-0">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Stage</TableHead>
                <TableHead>Projects currently here</TableHead>
                <TableHead>Avg. days in stage</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {stageCounts.map((stage) => (
                <TableRow key={stage.key}>
                  <TableCell className="font-medium">{formatStageLabel(stage.key)}</TableCell>
                  <TableCell>{stage.count}</TableCell>
                  <TableCell>
                    <div className="flex items-center gap-2">
                      <div className="h-1.5 w-24 overflow-hidden rounded-full bg-muted">
                        <div
                          className={stage.avgDays >= 5 ? "h-full bg-amber-500" : "h-full bg-primary"}
                          style={{ width: `${Math.min(stage.avgDays * 10, 100)}%` }}
                        />
                      </div>
                      <span className="text-xs text-muted-foreground">{stage.avgDays}d</span>
                    </div>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </CardContent>
      </Card>
    </div>
  );
}
