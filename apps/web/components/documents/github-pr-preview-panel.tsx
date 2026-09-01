"use client";

import { useState } from "react";
import { Check, Copy, ExternalLink, Loader2, X } from "lucide-react";

import { Button } from "@/components/ui/button";
import type { GithubPrPreview } from "@/lib/types";

// Review-before-paste preview for Implementation's code_change -> a real
// GitHub PR (see apps/api/app/services/github_export.py). No real GitHub
// connection exists — this is what a human copies into a real PR's
// title/description boxes, mirroring JiraExportPreviewPanel's "preview
// only, no real push" pattern for the same MVP-scope reason.
export function GithubPrPreviewPanel({
  preview,
  loading,
  error,
  onClose,
}: {
  preview: GithubPrPreview | null;
  loading: boolean;
  error: string | null;
  onClose: () => void;
}) {
  const [copied, setCopied] = useState<"title" | "description" | null>(null);

  async function copy(text: string, which: "title" | "description") {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(which);
      window.setTimeout(() => setCopied(null), 1500);
    } catch {
      // Clipboard access can be denied by the browser — the text is still
      // visible on screen to select/copy manually, so this isn't fatal.
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
      <div className="flex max-h-[85vh] w-full max-w-2xl flex-col rounded-lg border border-border bg-card shadow-xl">
        <div className="flex items-center justify-between border-b border-border p-4">
          <div>
            <h2 className="text-sm font-semibold">GitHub PR preview</h2>
            <p className="text-xs text-muted-foreground">
              Copy this into a real GitHub pull request — no real GitHub connection exists yet.
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
              <div>
                <div className="mb-1 flex items-center justify-between">
                  <span className="text-xs font-medium uppercase tracking-wide text-muted-foreground">PR title</span>
                  <Button variant="ghost" size="sm" onClick={() => copy(preview.suggestedTitle, "title")}>
                    {copied === "title" ? <Check className="h-3.5 w-3.5" /> : <Copy className="h-3.5 w-3.5" />}
                    {copied === "title" ? "Copied" : "Copy"}
                  </Button>
                </div>
                <p className="rounded-md border border-border bg-muted/40 p-2 text-sm">{preview.suggestedTitle}</p>
              </div>

              <div>
                <div className="mb-1 flex items-center justify-between">
                  <span className="text-xs font-medium uppercase tracking-wide text-muted-foreground">PR description</span>
                  <Button variant="ghost" size="sm" onClick={() => copy(preview.descriptionMarkdown, "description")}>
                    {copied === "description" ? <Check className="h-3.5 w-3.5" /> : <Copy className="h-3.5 w-3.5" />}
                    {copied === "description" ? "Copied" : "Copy"}
                  </Button>
                </div>
                <pre className="whitespace-pre-wrap rounded-md border border-border bg-muted/40 p-3 font-mono text-xs">
                  {preview.descriptionMarkdown}
                </pre>
              </div>
            </div>
          ) : null}
        </div>

        <div className="flex items-center justify-between border-t border-border p-4">
          <p className="text-xs text-muted-foreground">GitHub isn&apos;t connected yet — see Settings &gt; Integrations.</p>
          <Button disabled title="Not connected yet — no live GitHub API/OAuth wiring exists.">
            <ExternalLink className="h-3.5 w-3.5" />
            Open PR on GitHub
          </Button>
        </div>
      </div>
    </div>
  );
}
