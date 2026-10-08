"use client";

import { useState } from "react";
import { ExternalLink, GitPullRequest, Loader2, RefreshCw, Terminal } from "lucide-react";

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
 * The per-story counterpart to components/workflow/coding-tool-panel.tsx,
 * for the delivery lane's own generic document stages (Story LLD,
 * Implementation Plan, Test Scenarios). The skill files themselves
 * (command, reviewer, validator) are installed exactly once per repo — see
 * the generic "Add via pull request" step here, same install call the
 * project-level panel uses, just with a story-scoped `stage`. What's
 * different here is genuinely per-story: refreshing this one story's input
 * snapshot, and pulling its finished document back in.
 */
export function StoryCodingToolPanel({
  projectId,
  storyId,
  stage,
  stageTitle,
  currentUserId,
  onSynced,
}: {
  projectId: string;
  storyId: string;
  stage: "story_lld" | "story_implementation_plan" | "story_test_scenarios";
  stageTitle: string;
  currentUserId: string | null;
  /** Called (and awaited) after a successful "Sync from repository" — the
   * parent should refetch and update whatever it shows for this stage
   * (the drafted document, the lane's node statuses) here, not rely on a
   * full page reload. The "Syncing…" spinner stays on until this resolves,
   * so the document on screen is already current by the time it clears. */
  onSynced?: () => void | Promise<void>;
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

  const [ref, setRef] = useState("");
  const [syncing, setSyncing] = useState(false);
  const [syncError, setSyncError] = useState<string | null>(null);
  const [syncMessage, setSyncMessage] = useState<string | null>(null);

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
      const result = await api.codingTools.install(projectId, { tool, stage, triggered_by_user_id: currentUserId });
      setInstallMessage({ text: result.message, prUrl: result.pull_request_url });
    } catch (err) {
      setInstallError(err instanceof ApiError ? err.message : "Failed to add the skills to the repository.");
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
      const result = await api.codingTools.syncStoryInputs(projectId, storyId, { stage, triggered_by_user_id: currentUserId });
      setInputsMessage({ text: result.message, prUrl: result.pull_request_url, notReady: result.not_ready });
    } catch (err) {
      setInputsError(err instanceof ApiError ? err.message : "Failed to sync this story's inputs to the repository.");
    } finally {
      setSyncingInputs(false);
    }
  }

  async function sync() {
    if (currentUserId === null) return;
    setSyncing(true);
    setSyncError(null);
    setSyncMessage(null);
    try {
      const result = await api.codingTools.syncStoryStage(projectId, storyId, { stage, triggered_by_user_id: currentUserId, ref: ref.trim() || null });
      setSyncMessage(
        `Synced version ${result.version_number} from ${result.ref}${result.generated_by ? ` (written with ${result.generated_by})` : ""}.`,
      );
      // Awaited so the spinner only clears once the document shown above
      // has actually been refetched — otherwise the button stops spinning
      // while the refetch is still in flight and the page looks stale.
      await onSynced?.();
    } catch (err) {
      setSyncError(err instanceof ApiError ? err.message : "Failed to sync this document.");
    } finally {
      setSyncing(false);
    }
  }

  return (
    <div className="rounded-md border border-border p-2.5">
      <Button variant="outline" size="sm" className="w-full justify-start" onClick={() => setOpen((v) => !v)} aria-expanded={open}>
        <Terminal className="h-3.5 w-3.5" />
        Work in your own coding tool
      </Button>
      {!open ? (
        <p className="mt-1 text-xs text-muted-foreground">Draft {stageTitle} in Claude Code, Codex, OpenCode or Cursor instead.</p>
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
              Installs a generic <code>/{stage.replace(/_/g, "-")}</code> command that works for any story in this project — skip this if it&apos;s
              already been added for {stageTitle}.
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
            <h4 className="font-semibold">2. Sync this story&rsquo;s inputs</h4>
            <p className="text-muted-foreground">Refreshes what the command reads for this one story — run it now, and again if an upstream document changes.</p>
            <Button size="sm" variant="outline" onClick={syncInputs} disabled={syncingInputs || currentUserId === null} className="w-fit">
              {syncingInputs ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <GitPullRequest className="h-3.5 w-3.5" />}
              {syncingInputs ? "Syncing inputs…" : "Sync story inputs"}
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
            <h4 className="font-semibold">3. Run it, then bring the document back</h4>
            <p className="text-muted-foreground">
              In {TOOL_OPTIONS.find((t) => t.value === tool)?.label}, run the command with this story (its slug or exact title). Once it&rsquo;s
              pushed, sync it here — it arrives as a draft.
            </p>
            <input
              value={ref}
              onChange={(e) => setRef(e.target.value)}
              placeholder="Branch name (leave empty for the default branch)"
              className="h-8 rounded-md border border-input bg-background px-2 text-xs"
            />
            <Button size="sm" variant="outline" onClick={sync} disabled={syncing || currentUserId === null} className="w-fit">
              {syncing ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <RefreshCw className="h-3.5 w-3.5" />}
              {syncing ? "Syncing…" : "Sync from repository"}
            </Button>
            {syncError ? <p className="text-destructive">{syncError}</p> : null}
            {syncMessage ? (
              <p className="rounded border border-green-400/60 bg-green-50 p-2 dark:border-green-900 dark:bg-green-950/30">{syncMessage}</p>
            ) : null}
          </section>
        </div>
      )}
    </div>
  );
}
