"use client";

import Link from "next/link";
import { usePathname, useSearchParams } from "next/navigation";

import { cn } from "@/lib/utils";

export function WorkspaceTabs({ projectId }: { projectId: string }) {
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const projectParam = searchParams.get("project");

  const tabs = [
    { label: "Overview", href: `/projects/${projectId}`, active: pathname === `/projects/${projectId}` },
    {
      label: "Workflow",
      href: `/projects/${projectId}/workflow`,
      active: pathname === `/projects/${projectId}/workflow`,
    },
    {
      label: "Implementation Plan",
      href: `/projects/${projectId}/implementation-plan`,
      active: pathname === `/projects/${projectId}/implementation-plan`,
    },
    {
      label: "Infrastructure",
      href: `/projects/${projectId}/infrastructure`,
      active: pathname === `/projects/${projectId}/infrastructure`,
    },
    {
      label: "PR Review",
      href: `/projects/${projectId}/pr-review`,
      active: pathname === `/projects/${projectId}/pr-review`,
    },
    {
      label: "Testing",
      href: `/projects/${projectId}/testing`,
      active: pathname === `/projects/${projectId}/testing`,
    },
    {
      label: "Maintenance",
      href: `/projects/${projectId}/maintenance`,
      active: pathname === `/projects/${projectId}/maintenance`,
    },
    { label: "Documents", href: `/documents?project=${projectId}`, active: pathname === "/documents" && projectParam === projectId },
    { label: "Reviews", href: `/reviews?project=${projectId}`, active: pathname === "/reviews" && projectParam === projectId },
  ];

  return (
    <div className="mb-6 flex gap-1 border-b border-border">
      {tabs.map((tab) => (
        <Link
          key={tab.label}
          href={tab.href}
          className={cn(
            "border-b-2 px-3 py-2 text-sm font-medium transition-colors",
            tab.active
              ? "border-primary text-foreground"
              : "border-transparent text-muted-foreground hover:text-foreground"
          )}
        >
          {tab.label}
        </Link>
      ))}
    </div>
  );
}
