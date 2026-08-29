import Link from "next/link";
import type { LucideIcon } from "lucide-react";

import { Card, CardContent } from "@/components/ui/card";
import { cn } from "@/lib/utils";

export interface StatCardProps {
  label: string;
  value: number | string;
  icon: LucideIcon;
  href?: string;
  /** "warning" draws attention (e.g. blocked work) — otherwise neutral. */
  tone?: "default" | "warning";
}

export function StatCard({ label, value, icon: Icon, href, tone = "default" }: StatCardProps) {
  const content = (
    <CardContent className="flex items-center justify-between p-4">
      <div>
        <p className="text-xs text-muted-foreground">{label}</p>
        <p className={cn("mt-1 text-2xl font-semibold", tone === "warning" && Number(value) > 0 && "text-amber-600 dark:text-amber-500")}>
          {value}
        </p>
      </div>
      <Icon
        className={cn("h-8 w-8", tone === "warning" && Number(value) > 0 ? "text-amber-500/60" : "text-muted-foreground/50")}
        strokeWidth={1.5}
      />
    </CardContent>
  );

  if (href) {
    return (
      <Link href={href}>
        <Card className="transition-colors hover:border-primary/50">{content}</Card>
      </Link>
    );
  }

  return <Card>{content}</Card>;
}
