"use client";

import { useState } from "react";
import { CheckCircle2 } from "lucide-react";

import { cn } from "@/lib/utils";
import { formatRelativeTime } from "@/lib/format";
import type { ArtifactSection, ArtifactVersionSummary } from "@/lib/types";

export function SectionNav({
  sections,
  activeSectionId,
  onSelectSection,
  versions,
  currentVersionNumber,
}: {
  sections: ArtifactSection[];
  activeSectionId: string;
  onSelectSection: (id: string) => void;
  versions: ArtifactVersionSummary[];
  currentVersionNumber: number;
}) {
  const [tab, setTab] = useState<"sections" | "history">("sections");

  return (
    <div className="flex h-full flex-col">
      <div className="flex border-b border-border">
        {(["sections", "history"] as const).map((t) => (
          <button
            key={t}
            onClick={() => setTab(t)}
            className={cn(
              "flex-1 border-b-2 px-3 py-2 text-xs font-medium capitalize transition-colors",
              tab === t ? "border-primary text-foreground" : "border-transparent text-muted-foreground hover:text-foreground"
            )}
          >
            {t === "sections" ? "Sections" : "History"}
          </button>
        ))}
      </div>

      {tab === "sections" ? (
        <nav className="flex-1 space-y-0.5 overflow-y-auto p-2">
          {sections.map((section) => (
            <button
              key={section.id}
              onClick={() => onSelectSection(section.id)}
              className={cn(
                "block w-full truncate rounded-md px-2.5 py-1.5 text-left text-sm transition-colors",
                activeSectionId === section.id
                  ? "bg-primary text-primary-foreground"
                  : "text-muted-foreground hover:bg-accent hover:text-accent-foreground"
              )}
            >
              {section.title}
            </button>
          ))}
        </nav>
      ) : (
        <ul className="flex-1 space-y-1 overflow-y-auto p-2">
          {[...versions].reverse().map((version) => (
            <li key={version.id} className="rounded-md px-2.5 py-2 text-xs hover:bg-accent">
              <div className="flex items-center gap-1.5 font-medium">
                v{version.versionNumber}
                {version.versionNumber === currentVersionNumber ? (
                  <CheckCircle2 className="h-3 w-3 text-emerald-600" aria-label="Current version" />
                ) : null}
              </div>
              {version.changeSummary ? <p className="text-muted-foreground">{version.changeSummary}</p> : null}
              <p className="text-muted-foreground">
                {version.createdByName} · {formatRelativeTime(version.createdAt)}
              </p>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
