"use client";

import { useState } from "react";
import { ExternalLink, GitPullRequest, Loader2, Terminal } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Select } from "@/components/ui/select";
import { api, ApiError, type ApiCodingTool } from "@/lib/api";

const TOOL_OPTIONS: { value: ApiCodingTool; label: string }[] = [
  { value: "claude_code", label: "Claude Code" },
  { value: "codex", label: "Codex" },
  { value: "opencode", label: "OpenCode" },
  { value: "cursor", label: "Cursor" },
];

const STORAGE_KEY = "sdlc-hub:coding-tool";

/**
 * The Implementation stage's own counterpart to story-coding-tool-panel.tsx.
 * Structurally different from that one: Implementation's output is a real
 * pull request, not a document this app pulls back — so there is no "Sync
 * from repository" step here. Once the developer has a PR, they register it
 * with the "Register pull request" control next to this panel instead.
 */
export function ImplementationCodingToolPanel({
  projectId,
  taskId,
  currentUserId,
}: {
  projectId: string;
  taskId: string;
  currentUserId: string | null;
}) {
  const [open, setOpen] = useState(false);
  const [tool, setTool] = useState<ApiCodingTool>(() => {
    try {
      const saved = window.localStorage.getItem(STORAGE_KEY) as ApiCodingTool | null;
      return saved && TOOL_OPTIONS.some((t) => t.value === saved) ? saved : "claude_code";
    } catch {
      return "claude_code";
    }
  });

  const [installing, setInstalling] = useState(false);
  const [installMessage, setInstallMessage] = useState<{ text: string; prUrl: string | null } | null>(null);
  const [installError, setInstallError] = useState<string | null>(null);

  const [syncingInputs, setSyncingInputs] = useState(false);
  const [inputsMessage, setInputsMessage] = useState<{ text: string; prUrl: string | null; notReady: string[] } | null>(null);
  const [inputsError, setInputsError] = useState<string | null>(null);

  function chooseTool(value: ApiCodingTool) {
    setTool(value);
    try {
      window.localStorage.setItem(STORAGE_KEY, value);
    } catch {
      // convenience only
    }
  }

  async function install() {
    if (currentUserId === null) return;
    setInstalling(true);
    setInstallError(null);
    setInstallMessage(null);
    try {
      const result = await api.codingTools.install(projectId, { tool, stage: "implementation", triggered_by_user_id: currentUserId });
      setInstallMessage({ text: result.message, prUrl: result.pull_request_url });
    } catch (err) {
      setInstallError(err instanceof ApiError ? err.message : "Failed to add the skill to the repository.");
    } finally {
      setInstalling(false);
    }
  }

  async function syncInputs() {
    if (currentUserId === null) return;
    setSyncingInputs(true);
    setInputsError(null);
    setInputsMessage(null);
    try {
      const result = await api.codingTools.syncImplementationTaskInputs(projectId, taskId, { triggered_by_user_id: currentUserId });
      setInputsMessage({ text: result.message, prUrl: result.pull_request_url, notReady: result.not_ready });
    } catch (err) {
      setInputsError(err instanceof ApiError ? err.message : "Failed to sync this task's inputs to the repository.");
    } finally {
      setSyncingInputs(false);
    }
  }

  return (
    <div className="rounded-md border border-border p-2.5">
      <Button variant="outline" size="sm" className="w-full justify-start" onClick={() => setOpen((v) => !v)} aria-expanded={open}>
        <Terminal className="h-3.5 w-3.5" />
        Work in your own coding tool
      </Button>
      {!open ? (
        <p className="mt-1 text-xs text-muted-foreground">Implement this task in Claude Code, Codex, OpenCode or Cursor instead.</p>
      ) : (
        <div className="mt-3 flex flex-col gap-3 text-xs">
          <div>
            <label className="mb-1 block font-medium">Your tool</label>
            <Select value={tool} onChange={(e) => chooseTool(e.target.value as ApiCodingTool)}>
              {TOOL_OPTIONS.map((t) => (
                <option key={t.value} value={t.value}>
                  {t.label}
                </option>
              ))}
            </Select>
          </div>

          <section className="flex flex-col gap-1.5">
            <h4 className="font-semibold">1. Add the skill (once per repository)</h4>
            <p className="text-muted-foreground">
              Installs a generic <code>/implementation</code> command that works for any task in this project — skip this if it&apos;s
              already been added.
            </p>
            <Button size="sm" onClick={install} disabled={installing || currentUserId === null} className="w-fit">
              {installing ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <GitPullRequest className="h-3.5 w-3.5" />}
              {installing ? "Opening pull request…" : "Add via pull request"}
            </Button>
            {installError ? <p className="text-destructive">{installError}</p> : null}
            {installMessage ? (
              <div className="rounded border border-green-400/60 bg-green-50 p-2 dark:border-green-900 dark:bg-green-950/30">
                <p>{installMessage.text}</p>
                {installMessage.prUrl ? (
                  <a href={installMessage.prUrl} target="_blank" rel="noreferrer" className="mt-1 inline-flex items-center gap-1 text-primary underline">
                    Open the pull request <ExternalLink className="h-3 w-3" />
                  </a>
                ) : null}
              </div>
            ) : null}
          </section>

          <section className="flex flex-col gap-1.5">
            <h4 className="font-semibold">2. Sync this task&rsquo;s inputs</h4>
            <p className="text-muted-foreground">
              Refreshes what the command reads for this one task (description, LLD, Implementation Plan, engineering setup) — run it
              now, and again if any of those change.
            </p>
            <Button size="sm" variant="outline" onClick={syncInputs} disabled={syncingInputs || currentUserId === null} className="w-fit">
              {syncingInputs ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <GitPullRequest className="h-3.5 w-3.5" />}
              {syncingInputs ? "Syncing inputs…" : "Sync this task's inputs"}
            </Button>
            {inputsError ? <p className="text-destructive">{inputsError}</p> : null}
            {inputsMessage ? (
              <div
                className={`rounded border p-2 ${
                  inputsMessage.notReady.length > 0
                    ? "border-amber-400/60 bg-amber-50 dark:border-amber-900 dark:bg-amber-950/30"
                    : "border-green-400/60 bg-green-50 dark:border-green-900 dark:bg-green-950/30"
                }`}
              >
                <p>{inputsMessage.text}</p>
                {inputsMessage.notReady.length > 0 ? (
                  <p className="mt-1 font-medium">Not ready yet: {inputsMessage.notReady.join(", ")}.</p>
                ) : null}
                {inputsMessage.prUrl ? (
                  <a href={inputsMessage.prUrl} target="_blank" rel="noreferrer" className="mt-1 inline-flex items-center gap-1 text-primary underline">
                    Open the pull request <ExternalLink className="h-3 w-3" />
                  </a>
                ) : null}
              </div>
            ) : null}
          </section>

          <section className="flex flex-col gap-1.5">
            <h4 className="font-semibold">3. Run it, then register the pull request</h4>
            <p className="text-muted-foreground">
              In {TOOL_OPTIONS.find((t) => t.value === tool)?.label}, run <code>/implementation</code> naming this task (its area or
              title). It implements the change for real, runs tests, and opens a pull request. Enter that PR&apos;s number in the box
              below to register it here.
            </p>
          </section>
        </div>
      )}
    </div>
  );
}
