"use client";

import { useState } from "react";
import { ListChecks, Loader2, MessageCircleQuestion, RefreshCw, Send } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { api, ApiError, type ApiAskQuestionResponse, type ApiChangeSummary } from "@/lib/api";

const BOX = "rounded-md border border-border p-2.5";

// --- Regenerate section -------------------------------------------------------------------

/** Rewrites the selected section from scratch, using the stage's original
 * request — see POST /artifacts/{id}/improve-section with mode "regenerate".
 * Only that one section can change, and the previous version stays in History. */
export function RegenerateSectionAction({
  artifactId,
  sectionTitle,
  enabled,
  disabledReason,
  triggeredByUserId,
  onApplied,
}: {
  artifactId: string;
  sectionTitle: string | null;
  enabled: boolean;
  disabledReason: string | null;
  triggeredByUserId: string | null;
  onApplied: (outcome: { artifactStatus: string; workflowNodeStatus: string }) => void;
}) {
  const [open, setOpen] = useState(false);
  const [guidance, setGuidance] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<{ kind: "error" | "info"; text: string } | null>(null);

  async function run() {
    if (triggeredByUserId === null || !sectionTitle) return;
    setBusy(true);
    setMessage(null);
    try {
      const response = await api.artifacts.improveSection(artifactId, {
        section_title: sectionTitle,
        instruction: guidance.trim(),
        triggered_by_user_id: triggeredByUserId,
        mode: "regenerate",
      });
      if (response.needs_clarification) {
        setMessage({ kind: "info", text: response.agent_run.output_text ?? "The agent needs more information before it can regenerate this section." });
        return;
      }
      setGuidance("");
      setOpen(false);
      onApplied({ artifactStatus: response.artifact_status, workflowNodeStatus: response.workflow_node_status });
    } catch (err) {
      setMessage({ kind: "error", text: err instanceof ApiError ? err.message : "Failed to regenerate this section." });
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className={BOX}>
      <Button variant="outline" size="sm" className="w-full justify-start" disabled={!enabled || !sectionTitle} onClick={() => setOpen((v) => !v)}>
        <RefreshCw className="h-3.5 w-3.5" />
        Regenerate section
      </Button>
      {!enabled && disabledReason ? <p className="mt-1 text-xs text-muted-foreground">{disabledReason}</p> : null}
      {open && sectionTitle ? (
        <div className="mt-2 flex flex-col gap-2">
          <p className="text-xs text-muted-foreground">
            Writes a fresh version of <strong>&ldquo;{sectionTitle}&rdquo;</strong> from your original request. Other sections are not touched, and the
            current text stays in History.
          </p>
          <Textarea
            placeholder="Optional guidance, e.g. “focus on security”"
            value={guidance}
            onChange={(e) => setGuidance(e.target.value)}
            rows={2}
            className="text-xs"
          />
          <Button size="sm" onClick={run} disabled={busy}>
            {busy ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <RefreshCw className="h-3.5 w-3.5" />}
            {busy ? "Regenerating…" : "Regenerate"}
          </Button>
          {message ? <p className={`text-xs ${message.kind === "error" ? "text-destructive" : ""}`}>{message.text}</p> : null}
        </div>
      ) : null}
    </div>
  );
}

// --- Summarize changes --------------------------------------------------------------------

const KIND_LABEL = { added: "Added", removed: "Removed", changed: "Changed" } as const;
const KIND_STYLE = {
  added: "bg-green-100 text-green-800 dark:bg-green-950 dark:text-green-300",
  removed: "bg-red-100 text-red-800 dark:bg-red-950 dark:text-red-300",
  changed: "bg-amber-100 text-amber-800 dark:bg-amber-950 dark:text-amber-300",
} as const;

/** Compares the current version with the previous one, section by section. */
export function SummarizeChangesAction({ artifactId, refreshKey }: { artifactId: string; refreshKey: string }) {
  const [open, setOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [summary, setSummary] = useState<ApiChangeSummary | null>(null);
  const [loadedFor, setLoadedFor] = useState<string | null>(null);

  async function load() {
    setLoading(true);
    setError(null);
    try {
      setSummary(await api.artifacts.changes(artifactId));
      setLoadedFor(refreshKey);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to load the changes.");
    } finally {
      setLoading(false);
    }
  }

  function toggle() {
    const next = !open;
    setOpen(next);
    // Re-fetch when first opened, or when the document has a new version since.
    if (next && (summary === null || loadedFor !== refreshKey)) void load();
  }

  return (
    <div className={BOX}>
      <Button variant="outline" size="sm" className="w-full justify-start" onClick={toggle} aria-expanded={open}>
        <ListChecks className="h-3.5 w-3.5" />
        Summarize changes
      </Button>
      {open ? (
        <div className="mt-2 flex flex-col gap-2 text-xs">
          {loading ? (
            <p className="flex items-center gap-2 text-muted-foreground">
              <Loader2 className="h-3 w-3 animate-spin" /> Comparing versions…
            </p>
          ) : error ? (
            <p className="text-destructive">{error}</p>
          ) : summary ? (
            <>
              <p className="font-medium">{summary.headline}</p>
              {summary.change_note ? <p className="text-muted-foreground">Note on this version: {summary.change_note}</p> : null}
              {summary.changes.map((c) => (
                <div key={c.title} className="rounded-md border border-border p-2">
                  <div className="mb-1 flex items-center gap-2">
                    <span className={`rounded px-1.5 py-0.5 text-[10px] font-medium ${KIND_STYLE[c.kind]}`}>{KIND_LABEL[c.kind]}</span>
                    <span className="font-medium">{c.title}</span>
                    <span className="ml-auto text-muted-foreground">
                      {c.added_line_count > 0 ? `+${c.added_line_count}` : ""} {c.removed_line_count > 0 ? `−${c.removed_line_count}` : ""}
                    </span>
                  </div>
                  {c.removed_lines.map((l, i) => (
                    <p key={`r${i}`} className="break-words text-red-700 line-through dark:text-red-400">
                      − {l}
                    </p>
                  ))}
                  {c.added_lines.map((l, i) => (
                    <p key={`a${i}`} className="break-words text-green-700 dark:text-green-400">
                      + {l}
                    </p>
                  ))}
                  {c.added_line_count > c.added_lines.length || c.removed_line_count > c.removed_lines.length ? (
                    <p className="text-muted-foreground">…and more lines (see History for the full versions).</p>
                  ) : null}
                </div>
              ))}
            </>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

// --- Ask questions ------------------------------------------------------------------------

interface Exchange {
  question: string;
  result: ApiAskQuestionResponse | null;
  error: string | null;
}

/** Ask the agent a question about this document. Read-only: it never edits it. */
export function AskQuestionsAction({ artifactId, triggeredByUserId }: { artifactId: string; triggeredByUserId: string | null }) {
  const [open, setOpen] = useState(false);
  const [question, setQuestion] = useState("");
  const [busy, setBusy] = useState(false);
  const [history, setHistory] = useState<Exchange[]>([]);

  async function ask() {
    const q = question.trim();
    if (!q || triggeredByUserId === null) return;
    setBusy(true);
    setQuestion("");
    try {
      const result = await api.artifacts.ask(artifactId, { question: q, triggered_by_user_id: triggeredByUserId });
      setHistory((h) => [...h, { question: q, result, error: null }]);
    } catch (err) {
      setHistory((h) => [...h, { question: q, result: null, error: err instanceof ApiError ? err.message : "Failed to get an answer." }]);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className={BOX}>
      <Button variant="outline" size="sm" className="w-full justify-start" onClick={() => setOpen((v) => !v)} aria-expanded={open}>
        <MessageCircleQuestion className="h-3.5 w-3.5" />
        Ask questions
      </Button>
      {open ? (
        <div className="mt-2 flex flex-col gap-2 text-xs">
          <p className="text-muted-foreground">Ask about this document — for example “why is this a constraint?”. It answers from the document and your original request, and never changes it.</p>
          {history.map((h, i) => (
            <div key={i} className="flex flex-col gap-1">
              <p className="self-end rounded-md bg-primary px-2 py-1 text-primary-foreground">{h.question}</p>
              {h.error ? (
                <p className="text-destructive">{h.error}</p>
              ) : h.result ? (
                <div className="rounded-md border border-border bg-muted/40 p-2">
                  <p className="whitespace-pre-wrap">{h.result.answer}</p>
                  {h.result.sources.length > 0 ? <p className="mt-1 text-muted-foreground">From: {h.result.sources.join(", ")}</p> : null}
                  {h.result.truncated ? <p className="mt-1 text-muted-foreground">The document was long, so only its first part was used.</p> : null}
                </div>
              ) : null}
            </div>
          ))}
          {busy ? (
            <p className="flex items-center gap-2 text-muted-foreground">
              <Loader2 className="h-3 w-3 animate-spin" /> Thinking…
            </p>
          ) : null}
          <div className="flex flex-col gap-1.5">
            <Textarea
              placeholder="Type your question…"
              value={question}
              onChange={(e) => setQuestion(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) void ask();
              }}
              rows={2}
              maxLength={2000}
              className="text-xs"
              aria-label="Your question"
            />
            <Button size="sm" onClick={ask} disabled={busy || !question.trim() || triggeredByUserId === null}>
              <Send className="h-3.5 w-3.5" />
              Ask
            </Button>
          </div>
        </div>
      ) : null}
    </div>
  );
}
