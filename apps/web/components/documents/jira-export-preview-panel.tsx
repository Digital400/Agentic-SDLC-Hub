"use client";

import { AlertTriangle, CheckCircle2, ExternalLink, Loader2, X, XCircle } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import type { JiraExportPreview } from "@/lib/types";

// Review-before-push preview for Story Crafting -> Jira (see
// apps/api/app/services/jira_export.py). No real Jira connection exists —
// "Push to Jira" stays disabled regardless of validation state; the API's
// own pushToJiraEnabled flag (always false today) is what actually gates
// it, not a client-side guess, so this can't silently start working if the
// backend rule changes without this component also being updated.
export function JiraExportPreviewPanel({
  preview,
  loading,
  error,
  onClose,
}: {
  preview: JiraExportPreview | null;
  loading: boolean;
  error: string | null;
  onClose: () => void;
}) {
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
      <div className="flex max-h-[85vh] w-full max-w-3xl flex-col rounded-lg border border-border bg-card shadow-xl">
        <div className="flex items-center justify-between border-b border-border p-4">
          <div>
            <h2 className="text-sm font-semibold">Jira export preview</h2>
            <p className="text-xs text-muted-foreground">
              Review the field mapping before anything is pushed — no real Jira connection exists yet.
            </p>
          </div>
          <Button variant="ghost" size="icon" onClick={onClose} aria-label="Close">
            <X className="h-4 w-4" />
          </Button>
        </div>

        <div className="flex-1 overflow-y-auto p-4">
          {loading ? (
            <div className="flex items-center justify-center gap-2 py-12 text-sm text-muted-foreground">
              <Loader2 className="h-4 w-4 animate-spin" />
              Building preview…
            </div>
          ) : error ? (
            <p className="rounded-md border border-destructive/30 bg-destructive/10 p-3 text-sm text-destructive">{error}</p>
          ) : preview ? (
            <div className="flex flex-col gap-4">
              <div className="flex items-center justify-between rounded-md border border-border bg-muted/40 p-3 text-sm">
                <span>
                  <strong>{preview.validStoryCount}</strong> of <strong>{preview.storyCount}</strong> stories map
                  cleanly to Jira
                </span>
                {preview.hasErrors ? (
                  <Badge variant="warning">Needs attention</Badge>
                ) : (
                  <Badge variant="success">All valid</Badge>
                )}
              </div>

              {preview.overallErrors.length > 0 ? (
                <div className="rounded-md border border-destructive/30 bg-destructive/10 p-3">
                  <p className="mb-1.5 flex items-center gap-1.5 text-xs font-medium text-destructive">
                    <AlertTriangle className="h-3.5 w-3.5" />
                    Backlog-level issues
                  </p>
                  <ul className="list-inside list-disc text-xs text-destructive">
                    {preview.overallErrors.map((e) => (
                      <li key={e}>{e}</li>
                    ))}
                  </ul>
                </div>
              ) : null}

              {preview.stories.map((story) => (
                <div key={story.storyTitle} className="rounded-md border border-border p-3">
                  <div className="mb-2 flex items-center justify-between gap-2">
                    <h3 className="text-sm font-medium">{story.storyTitle}</h3>
                    {story.isValid ? (
                      <CheckCircle2 className="h-4 w-4 shrink-0 text-emerald-600" aria-label="Valid" />
                    ) : (
                      <XCircle className="h-4 w-4 shrink-0 text-destructive" aria-label="Has errors" />
                    )}
                  </div>

                  {story.validationErrors.length > 0 ? (
                    <ul className="mb-2 list-inside list-disc text-xs text-destructive">
                      {story.validationErrors.map((e) => (
                        <li key={e}>{e}</li>
                      ))}
                    </ul>
                  ) : null}

                  <dl className="grid grid-cols-2 gap-x-4 gap-y-1.5 text-xs">
                    <div>
                      <dt className="text-muted-foreground">Epic</dt>
                      <dd>{story.mapping.epic ?? "—"}</dd>
                    </div>
                    <div>
                      <dt className="text-muted-foreground">Priority</dt>
                      <dd>{story.mapping.priority ?? "—"}</dd>
                    </div>
                    <div className="col-span-2">
                      <dt className="text-muted-foreground">Labels (from Feature)</dt>
                      <dd className="flex flex-wrap gap-1 pt-0.5">
                        {story.mapping.labels.length === 0
                          ? "—"
                          : story.mapping.labels.map((l) => (
                              <Badge key={l} variant="outline">
                                {l}
                              </Badge>
                            ))}
                      </dd>
                    </div>
                    <div className="col-span-2">
                      <dt className="text-muted-foreground">Jira Story summary (from User Story)</dt>
                      <dd className="pt-0.5">{story.mapping.summary}</dd>
                    </div>
                    <div className="col-span-2">
                      <dt className="text-muted-foreground">Description (from Acceptance Criteria)</dt>
                      <dd className="whitespace-pre-wrap pt-0.5 font-mono text-[11px]">{story.mapping.description}</dd>
                    </div>
                    <div className="col-span-2">
                      <dt className="text-muted-foreground">Linked issues (placeholder — no real Jira keys yet)</dt>
                      <dd className="flex flex-wrap gap-1 pt-0.5">
                        {story.mapping.linkedIssuesPlaceholder.length === 0
                          ? "None"
                          : story.mapping.linkedIssuesPlaceholder.map((title) => (
                              <Badge key={title} variant="outline">
                                {title}
                              </Badge>
                            ))}
                      </dd>
                    </div>
                  </dl>
                </div>
              ))}
            </div>
          ) : null}
        </div>

        <div className="flex items-center justify-between border-t border-border p-4">
          <p className="text-xs text-muted-foreground">Jira isn&apos;t connected yet — see Settings &gt; Integrations.</p>
          <Button disabled title="Not connected yet — see docs/architecture.md's MCP integrations section.">
            <ExternalLink className="h-3.5 w-3.5" />
            Push to Jira
          </Button>
        </div>
      </div>
    </div>
  );
}
