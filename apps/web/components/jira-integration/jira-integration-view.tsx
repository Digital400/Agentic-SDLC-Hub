"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { ExternalLink, Loader2, RefreshCw, Send, Unplug } from "lucide-react";

import { IntegrationStatusBadge } from "@/components/status-badge";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import { api, ApiError } from "@/lib/api";
import {
  toJiraConnectionItem,
  toJiraProjectLinkItem,
  toJiraPushPreview,
  toJiraPushResultEntry,
} from "@/lib/mappers";
import type { JiraConnectionItem, JiraPushListItem, JiraPushPreviewItem, JiraPushResultEntry, JiraSourceType } from "@/lib/types";

const SECTIONS: { key: keyof Pick<JiraPushPreviewItem, "epics" | "stories" | "implementationTasks" | "testingBugs">; label: string }[] = [
  { key: "epics", label: "Epics" },
  { key: "stories", label: "Stories" },
  { key: "implementationTasks", label: "Implementation Tasks (→ Sub-tasks)" },
  { key: "testingBugs", label: "Testing Bugs" },
];

/** A unique key for one preview item, used for the (unchecked-by-default)
 * selection state — requirement "do not auto-create without human
 * confirmation" means nothing here starts pre-selected. */
function itemKey(sourceType: JiraSourceType, sourceKey: string): string {
  return `${sourceType}:${sourceKey}`;
}

export function JiraIntegrationView({
  connection: initialConnection,
  projects,
  currentUserId,
}: {
  connection: JiraConnectionItem | null;
  projects: { id: string; name: string }[];
  currentUserId: string | null;
}) {
  const [connection, setConnection] = useState(initialConnection);
  const [baseUrl, setBaseUrl] = useState("");
  const [email, setEmail] = useState("");
  const [apiToken, setApiToken] = useState("");
  const [connecting, setConnecting] = useState(false);
  const [connectError, setConnectError] = useState<string | null>(null);

  const [projectId, setProjectId] = useState(projects[0]?.id ?? "");
  const [jiraProjectLink, setJiraProjectLink] = useState<{ id: string; jiraProjectKey: string; jiraProjectName: string | null } | null>(null);
  const [projectLoading, setProjectLoading] = useState(false);
  const [jiraProjectKey, setJiraProjectKey] = useState("");
  const [savingProject, setSavingProject] = useState(false);
  const [saveProjectError, setSaveProjectError] = useState<string | null>(null);

  const [preview, setPreview] = useState<JiraPushPreviewItem | null>(null);
  const [previewLoading, setPreviewLoading] = useState(false);
  const [previewError, setPreviewError] = useState<string | null>(null);
  const [selected, setSelected] = useState<Record<string, boolean>>({});

  const [pushing, setPushing] = useState(false);
  const [pushResults, setPushResults] = useState<JiraPushResultEntry[] | null>(null);
  const [syncing, setSyncing] = useState(false);

  const isConnected = connection?.status === "CONNECTED";

  useEffect(() => {
    if (!projectId) return;
    let cancelled = false;
    setProjectLoading(true);
    setJiraProjectLink(null);
    setPreview(null);
    setPushResults(null);
    api.projects
      .jiraProject(projectId)
      .then((link) => {
        if (cancelled) return;
        setJiraProjectLink(link ? toJiraProjectLinkItem(link) : null);
      })
      .catch(() => {
        if (!cancelled) setJiraProjectLink(null);
      })
      .finally(() => {
        if (!cancelled) setProjectLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [projectId]);

  async function handleConnect() {
    if (currentUserId === null) {
      setConnectError("No users exist yet to attribute this connection to.");
      return;
    }
    setConnecting(true);
    setConnectError(null);
    try {
      const result = await api.jira.connect({ base_url: baseUrl, email, api_token: apiToken, connected_by_id: currentUserId });
      setConnection(toJiraConnectionItem(result));
      setApiToken(""); // never keep the API token in memory/state longer than the request that sent it
    } catch (err) {
      setConnectError(err instanceof ApiError ? err.message : "Failed to connect to Jira.");
    } finally {
      setConnecting(false);
    }
  }

  async function handleDisconnect() {
    if (!connection) return;
    try {
      const result = await api.jira.disconnect(connection.id);
      setConnection(toJiraConnectionItem(result));
      setJiraProjectLink(null);
    } catch (err) {
      setConnectError(err instanceof ApiError ? err.message : "Failed to disconnect.");
    }
  }

  async function handleSaveProject() {
    if (!connection) return;
    setSavingProject(true);
    setSaveProjectError(null);
    try {
      const result = await api.jira.saveProject({ project_id: projectId, connection_id: connection.id, jira_project_key: jiraProjectKey });
      setJiraProjectLink(toJiraProjectLinkItem(result));
      setJiraProjectKey("");
    } catch (err) {
      setSaveProjectError(err instanceof ApiError ? err.message : "Failed to save this Jira project — check the key and try again.");
    } finally {
      setSavingProject(false);
    }
  }

  async function handleLoadPreview() {
    setPreviewLoading(true);
    setPreviewError(null);
    setPushResults(null);
    try {
      const result = await api.jira.pushPreview(projectId);
      setPreview(toJiraPushPreview(result));
      setSelected({}); // every checkbox starts unchecked — no auto-select
    } catch (err) {
      setPreviewError(err instanceof ApiError ? err.message : "Failed to load the push preview.");
    } finally {
      setPreviewLoading(false);
    }
  }

  async function handlePush() {
    if (!preview || currentUserId === null) return;
    const selections = Object.keys(selected)
      .filter((k) => selected[k])
      .map((k) => {
        const [sourceType, ...rest] = k.split(":");
        return { source_type: sourceType as JiraSourceType, source_key: rest.join(":") };
      });
    if (selections.length === 0) return;

    setPushing(true);
    try {
      const response = await api.jira.push({ project_id: projectId, triggered_by_user_id: currentUserId, selections });
      setPushResults(response.results.map(toJiraPushResultEntry));
      await handleLoadPreview(); // refresh so newly-created items show as already-linked
    } catch (err) {
      setPreviewError(err instanceof ApiError ? err.message : "Failed to push to Jira.");
    } finally {
      setPushing(false);
    }
  }

  async function handleSyncStatus() {
    setSyncing(true);
    try {
      await api.jira.syncStatus(projectId);
      await handleLoadPreview();
    } catch (err) {
      setPreviewError(err instanceof ApiError ? err.message : "Failed to sync status from Jira.");
    } finally {
      setSyncing(false);
    }
  }

  const selectedCount = Object.values(selected).filter(Boolean).length;

  function renderItem(item: JiraPushListItem) {
    const key = itemKey(item.sourceType, item.sourceKey);
    if (item.alreadyLinked) {
      return (
        <div key={key} className="flex items-center justify-between gap-2 rounded border border-border bg-muted/30 p-2 text-xs">
          <div>
            <p className="font-medium">{item.label}</p>
            <p className="text-muted-foreground">{item.jiraIssueType}</p>
          </div>
          <a href={item.alreadyLinked.jiraIssueUrl} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 text-primary hover:underline">
            <ExternalLink className="h-3 w-3" />
            {item.alreadyLinked.jiraIssueKey}
            {item.alreadyLinked.jiraStatus ? <Badge variant="outline">{item.alreadyLinked.jiraStatus}</Badge> : null}
          </a>
        </div>
      );
    }
    return (
      <div key={key} className="flex items-start gap-2 rounded border border-border p-2 text-xs">
        <Checkbox
          checked={!!selected[key]}
          disabled={item.validationErrors.length > 0}
          onChange={(e) => setSelected((s) => ({ ...s, [key]: e.target.checked }))}
          className="mt-0.5"
        />
        <div className="flex-1">
          <p className="font-medium">
            {item.label} <span className="text-muted-foreground">({item.jiraIssueType})</span>
          </p>
          {item.parentSourceKey ? <p className="text-muted-foreground">Parent: {item.parentSourceKey}</p> : null}
          {item.validationErrors.length > 0 ? (
            <ul className="mt-1 list-inside list-disc text-destructive">
              {item.validationErrors.map((e) => (
                <li key={e}>{e}</li>
              ))}
            </ul>
          ) : null}
        </div>
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-4">
      <Card>
        <CardHeader className="flex-row items-center justify-between space-y-0">
          <div>
            <CardTitle className="text-base">Connection</CardTitle>
            <CardDescription>
              A Jira Cloud API token (id.atlassian.com → Security → API tokens). Encrypted at rest and never shown
              again after saving.
            </CardDescription>
          </div>
          {connection ? <IntegrationStatusBadge status={connection.status} /> : null}
        </CardHeader>
        <CardContent className="flex flex-col gap-3">
          {isConnected ? (
            <div className="flex items-center justify-between gap-2 rounded-md border border-border bg-muted/40 p-3 text-xs">
              <div>
                <p className="font-medium">{connection?.email}</p>
                <p className="text-muted-foreground">
                  {connection?.baseUrl} · Token {connection?.tokenHint}
                </p>
              </div>
              <Button variant="outline" size="sm" onClick={handleDisconnect}>
                <Unplug className="h-3.5 w-3.5" />
                Disconnect
              </Button>
            </div>
          ) : (
            <div className="flex flex-wrap items-end gap-2">
              <div>
                <label className="mb-1 block text-xs text-muted-foreground">Base URL</label>
                <Input value={baseUrl} onChange={(e) => setBaseUrl(e.target.value)} placeholder="https://yourcompany.atlassian.net" className="w-64" />
              </div>
              <div>
                <label className="mb-1 block text-xs text-muted-foreground">Account email</label>
                <Input value={email} onChange={(e) => setEmail(e.target.value)} placeholder="you@example.com" className="w-52" />
              </div>
              <div>
                <label className="mb-1 block text-xs text-muted-foreground">API token</label>
                <Input type="password" value={apiToken} onChange={(e) => setApiToken(e.target.value)} className="w-52" autoComplete="off" />
              </div>
              <Button size="sm" onClick={handleConnect} disabled={connecting || !baseUrl || !email || !apiToken}>
                {connecting ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : null}
                {connecting ? "Connecting…" : "Connect"}
              </Button>
            </div>
          )}
          {connectError ? <p className="text-xs text-destructive">{connectError}</p> : null}
        </CardContent>
      </Card>

      {isConnected ? (
        <Card>
          <CardHeader>
            <CardTitle className="text-base">Project configuration</CardTitle>
            <CardDescription>Which project this Jira project belongs to.</CardDescription>
          </CardHeader>
          <CardContent className="flex flex-col gap-3">
            <Select value={projectId} onChange={(e) => setProjectId(e.target.value)} className="max-w-xs">
              {projects.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.name}
                </option>
              ))}
            </Select>

            {projectLoading ? (
              <p className="text-xs text-muted-foreground">Loading…</p>
            ) : jiraProjectLink ? (
              <div className="rounded-md border border-border p-3 text-xs">
                <p className="font-medium">
                  {jiraProjectLink.jiraProjectKey} — {jiraProjectLink.jiraProjectName ?? "(name not cached)"}
                </p>
              </div>
            ) : (
              <div className="flex flex-wrap items-end gap-2">
                <div>
                  <label className="mb-1 block text-xs text-muted-foreground">Jira project key</label>
                  <Input value={jiraProjectKey} onChange={(e) => setJiraProjectKey(e.target.value.toUpperCase())} placeholder="PROJ" className="w-40" />
                </div>
                <Button size="sm" onClick={handleSaveProject} disabled={savingProject || !jiraProjectKey}>
                  {savingProject ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : null}
                  {savingProject ? "Saving…" : "Save Jira project"}
                </Button>
              </div>
            )}
            {saveProjectError ? <p className="text-xs text-destructive">{saveProjectError}</p> : null}
          </CardContent>
        </Card>
      ) : null}

      {jiraProjectLink ? (
        <Card>
          <CardHeader className="flex-row items-center justify-between space-y-0">
            <div>
              <CardTitle className="text-base">Story export preview</CardTitle>
              <CardDescription>
                Nothing is pushed to Jira until you select items below and confirm — see the checkboxes, all
                unchecked by default.
              </CardDescription>
            </div>
            <div className="flex gap-2">
              <Button variant="outline" size="sm" onClick={handleSyncStatus} disabled={syncing || !preview}>
                <RefreshCw className="h-3.5 w-3.5" />
                {syncing ? "Syncing…" : "Refresh status from Jira"}
              </Button>
              <Button size="sm" onClick={handleLoadPreview} disabled={previewLoading}>
                {previewLoading ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : null}
                {previewLoading ? "Loading…" : "Load preview"}
              </Button>
            </div>
          </CardHeader>
          <CardContent className="flex flex-col gap-4">
            {previewError ? <p className="text-xs text-destructive">{previewError}</p> : null}

            {preview?.overallErrors.length ? (
              <div className="rounded-md border border-destructive/40 bg-destructive/5 p-2 text-xs text-destructive">
                {preview.overallErrors.map((e) => (
                  <p key={e}>{e}</p>
                ))}
              </div>
            ) : null}

            {preview ? (
              <>
                {SECTIONS.map(({ key, label }) => {
                  const items = preview[key];
                  if (items.length === 0) return null;
                  return (
                    <div key={key}>
                      <h3 className="mb-2 text-xs font-medium uppercase tracking-wide text-muted-foreground">
                        {label} ({items.length})
                      </h3>
                      <div className="flex flex-col gap-1.5">{items.map(renderItem)}</div>
                    </div>
                  );
                })}

                <div className="flex items-center gap-2 border-t border-border pt-3">
                  <Button size="sm" onClick={handlePush} disabled={pushing || selectedCount === 0}>
                    {pushing ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Send className="h-3.5 w-3.5" />}
                    {pushing ? "Pushing…" : `Push ${selectedCount} selected to Jira`}
                  </Button>
                </div>

                {pushResults ? (
                  <div>
                    <h3 className="mb-2 text-xs font-medium uppercase tracking-wide text-muted-foreground">Results</h3>
                    <div className="flex flex-col gap-1">
                      {pushResults.map((r) => (
                        <div key={itemKey(r.sourceType, r.sourceKey)} className="flex items-center justify-between gap-2 text-xs">
                          <span>{r.sourceKey}</span>
                          <div className="flex items-center gap-2">
                            <Badge variant={r.status === "created" ? "success" : r.status === "skipped_duplicate" ? "outline" : "destructive"}>
                              {r.status}
                            </Badge>
                            {r.jiraIssueUrl ? (
                              <Link href={r.jiraIssueUrl} target="_blank" className="inline-flex items-center gap-1 text-primary hover:underline">
                                <ExternalLink className="h-3 w-3" />
                                {r.jiraIssueKey}
                              </Link>
                            ) : null}
                          </div>
                        </div>
                      ))}
                    </div>
                  </div>
                ) : null}
              </>
            ) : null}
          </CardContent>
        </Card>
      ) : null}
    </div>
  );
}
