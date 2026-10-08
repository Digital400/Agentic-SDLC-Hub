"use client";

import { useEffect, useState } from "react";
import { ExternalLink, FileCode2, GitPullRequest, Loader2, RefreshCw, Terminal } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import { api, ApiError, type ApiCodingTool, type ApiInstallSkillsResponse, type ApiSkillPack, type ApiSyncStageResponse } from "@/lib/api";

const TOOL_OPTIONS: { value: ApiCodingTool; label: string; blurb: string }[] = [
  { value: "claude_code", label: "Claude Code", blurb: "Slash command + validation hook + reviewer subagent" },
  { value: "codex", label: "Codex", blurb: "AGENTS.md + custom prompt that loops on the validator" },
  { value: "opencode", label: "OpenCode", blurb: "Command with @-context + reviewer subagent" },
  { value: "cursor", label: "Cursor", blurb: "Chat command + scoped rule" },
];

const STORAGE_KEY = "sdlc-hub:coding-tool";

// Stages with a coding-tool skill pack — kept in sync by hand with
// STAGE_SPECS in apps/api/app/services/coding_tool_skills.py.
export const CODING_TOOL_STAGE_KEYS = new Set([
  "requirement_intake",
  "problem_discovery",
  "solution_discovery",
  "hld",
  "feature_intake",
  "existing_system_context_scan",
  "impact_analysis",
  "mini_solution_discovery",
  "hld_delta",
  "story_crafting",
]);

// "Work in your own coding tool": the developer runs this stage in Claude Code /
// Codex / OpenCode / Cursor using skills the app commits to their repository,
// then syncs the resulting document back here — see
// apps/api/app/services/coding_tool_skills.py.
export function CodingToolPanel({
  projectId,
  stage,
  stageTitle,
  currentUserId,
  onSynced,
}: {
  projectId: string;
  stage: string;
  stageTitle: string;
  currentUserId: string | null;
  onSynced: (result: ApiSyncStageResponse) => void;
}) {
  const [open, setOpen] = useState(false);
  const [tool, setTool] = useState<ApiCodingTool>("claude_code");
  const [pack, setPack] = useState<ApiSkillPack | null>(null);
  const [packError, setPackError] = useState<string | null>(null);
  const [packLoading, setPackLoading] = useState(false);
  const [installing, setInstalling] = useState(false);
  const [installResult, setInstallResult] = useState<ApiInstallSkillsResponse | null>(null);
  const [installError, setInstallError] = useState<string | null>(null);
  const [ref, setRef] = useState("");
  const [syncing, setSyncing] = useState(false);
  const [syncError, setSyncError] = useState<string | null>(null);
  const [syncResult, setSyncResult] = useState<ApiSyncStageResponse | null>(null);

  useEffect(() => {
    try {
      const saved = window.localStorage.getItem(STORAGE_KEY) as ApiCodingTool | null;
      if (saved && TOOL_OPTIONS.some((t) => t.value === saved)) setTool(saved);
    } catch {
      // Remembering the last choice is only a convenience.
    }
  }, []);

  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    setPackLoading(true);
    setPackError(null);
    setInstallResult(null);
    api.codingTools
      .preview(projectId, tool, stage)
      .then((p) => !cancelled && setPack(p))
      .catch((err) => {
        if (!cancelled) {
          setPack(null);
          setPackError(err instanceof ApiError ? err.message : "Failed to load the skill files.");
        }
      })
      .finally(() => !cancelled && setPackLoading(false));
    return () => {
      cancelled = true;
    };
  }, [open, tool, projectId, stage]);

  function chooseTool(value: ApiCodingTool) {
    setTool(value);
    try {
      window.localStorage.setItem(STORAGE_KEY, value);
    } catch {
      // ignore
    }
  }

  async function install() {
    if (currentUserId === null) return;
    setInstalling(true);
    setInstallError(null);
    setInstallResult(null);
    try {
      setInstallResult(await api.codingTools.install(projectId, { tool, stage, triggered_by_user_id: currentUserId }));
    } catch (err) {
      setInstallError(err instanceof ApiError ? err.message : "Failed to add the skills to the repository.");
    } finally {
      setInstalling(false);
    }
  }

  async function sync() {
    if (currentUserId === null) return;
    setSyncing(true);
    setSyncError(null);
    setSyncResult(null);
    try {
      const result = await api.codingTools.sync(projectId, { stage, triggered_by_user_id: currentUserId, ref: ref.trim() || null });
      setSyncResult(result);
      onSynced(result);
    } catch (err) {
      setSyncError(err instanceof ApiError ? err.message : "Failed to sync the document.");
    } finally {
      setSyncing(false);
    }
  }

  const blurb = TOOL_OPTIONS.find((t) => t.value === tool)?.blurb;

  return (
    <div className="mt-3 rounded-md border border-border p-2.5">
      <Button variant="outline" size="sm" className="w-full justify-start" onClick={() => setOpen((v) => !v)} aria-expanded={open}>
        <Terminal className="h-3.5 w-3.5" />
        Work in your own coding tool
      </Button>
      {!open ? (
        <p className="mt-1 text-xs text-muted-foreground">Prefer Claude Code, Codex, OpenCode or Cursor? Run {stageTitle} there and sync the result here.</p>
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
            {blurb ? <p className="mt-1 text-muted-foreground">{blurb}</p> : null}
          </div>

          <section className="flex flex-col gap-1.5">
            <h4 className="font-semibold">1. Add the skills to your repository</h4>
            {packLoading ? (
              <p className="flex items-center gap-2 text-muted-foreground">
                <Loader2 className="h-3 w-3 animate-spin" /> Preparing files…
              </p>
            ) : packError ? (
              <p className="text-destructive">{packError}</p>
            ) : pack ? (
              <>
                <ul className="flex flex-col gap-1">
                  {pack.files.map((f) => (
                    <li key={f.path}>
                      <details className="rounded border border-border px-2 py-1">
                        <summary className="flex cursor-pointer items-center gap-1.5">
                          <FileCode2 className="h-3 w-3 shrink-0" aria-hidden />
                          <code className="break-all">{f.path}</code>
                          {!f.managed ? <span className="rounded bg-muted px-1 text-[10px]">only if missing</span> : null}
                        </summary>
                        <p className="mt-1 text-muted-foreground">{f.purpose}</p>
                        <pre className="mt-1 max-h-48 overflow-auto whitespace-pre-wrap rounded bg-muted/50 p-1.5 text-[11px]">{f.content}</pre>
                      </details>
                    </li>
                  ))}
                </ul>
                <Button size="sm" onClick={install} disabled={installing || currentUserId === null} className="w-fit">
                  {installing ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <GitPullRequest className="h-3.5 w-3.5" />}
                  {installing ? "Opening pull request…" : "Add via pull request"}
                </Button>
                <p className="text-muted-foreground">
                  Creates a new branch and a pull request. Nothing is merged for you, and existing files your team edits are never overwritten.
                </p>
              </>
            ) : null}
            {installError ? <p className="text-destructive">{installError}</p> : null}
            {installResult ? (
              <div className="rounded border border-green-400/60 bg-green-50 p-2 dark:border-green-900 dark:bg-green-950/30">
                <p>{installResult.message}</p>
                {installResult.pull_request_url ? (
                  <a href={installResult.pull_request_url} target="_blank" rel="noreferrer" className="mt-1 inline-flex items-center gap-1 text-primary underline">
                    Open the pull request <ExternalLink className="h-3 w-3" />
                  </a>
                ) : null}
                {installResult.skipped.length > 0 ? (
                  <p className="mt-1 text-muted-foreground">Left unchanged: {installResult.skipped.map((s) => s.path).join(", ")}</p>
                ) : null}
              </div>
            ) : null}
          </section>

          {pack ? (
            <section className="flex flex-col gap-1.5">
              <h4 className="font-semibold">2. Run it in {pack.tool_label}</h4>
              <ol className="list-decimal space-y-1 pl-4">
                {pack.usage.map((u, i) => (
                  <li key={i}>{u}</li>
                ))}
              </ol>
              {pack.notes.map((n, i) => (
                <p key={i} className="text-muted-foreground">
                  Note: {n}
                </p>
              ))}
            </section>
          ) : null}

          <section className="flex flex-col gap-1.5">
            <h4 className="font-semibold">3. Bring the document back here</h4>
            <p className="text-muted-foreground">
              After you push <code>docs/sdlc/…</code>, sync it. It arrives as a draft; review and approval still happen in this app.
            </p>
            <Input value={ref} onChange={(e) => setRef(e.target.value)} placeholder="Branch name (leave empty for the default branch)" className="h-8 text-xs" />
            <Button size="sm" variant="outline" onClick={sync} disabled={syncing || currentUserId === null} className="w-fit">
              {syncing ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <RefreshCw className="h-3.5 w-3.5" />}
              {syncing ? "Syncing…" : "Sync from repository"}
            </Button>
            {syncError ? <p className="text-destructive">{syncError}</p> : null}
            {syncResult ? (
              <p className="rounded border border-green-400/60 bg-green-50 p-2 dark:border-green-900 dark:bg-green-950/30">
                Synced version {syncResult.version_number} from <code>{syncResult.ref}</code>
                {syncResult.generated_by ? ` (written with ${syncResult.generated_by})` : ""}. It is a draft — open it to review and send for approval.
              </p>
            ) : null}
          </section>
        </div>
      )}
    </div>
  );
}
