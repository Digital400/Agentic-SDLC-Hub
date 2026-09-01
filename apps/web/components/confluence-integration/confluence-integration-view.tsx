"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { ExternalLink, Loader2, Send, Unplug } from "lucide-react";

import { IntegrationStatusBadge } from "@/components/status-badge";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import { api, ApiError } from "@/lib/api";
import {
  toConfluenceConnectionItem,
  toConfluencePublishPreview,
  toConfluencePublishResultEntry,
} from "@/lib/mappers";
import type { ConfluenceConnectionItem, ConfluencePublishListItem, ConfluencePublishPreviewItem, ConfluencePublishResultEntry, ConfluenceSpaceLinkItem } from "@/lib/types";

export function ConfluenceIntegrationView({
  connection: initialConnection,
  projects,
  currentUserId,
}: {
  connection: ConfluenceConnectionItem | null;
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
  const [spaceLink, setSpaceLink] = useState<ConfluenceSpaceLinkItem | null>(null);
  const [projectLoading, setProjectLoading] = useState(false);
  const [spaceKey, setSpaceKey] = useState("");
  const [savingSpace, setSavingSpace] = useState(false);
  const [saveSpaceError, setSaveSpaceError] = useState<string | null>(null);

  const [preview, setPreview] = useState<ConfluencePublishPreviewItem | null>(null);
  const [previewLoading, setPreviewLoading] = useState(false);
  const [previewError, setPreviewError] = useState<string | null>(null);
  const [selected, setSelected] = useState<Record<string, boolean>>({});

  const [publishing, setPublishing] = useState(false);
  const [publishResults, setPublishResults] = useState<ConfluencePublishResultEntry[] | null>(null);

  const isConnected = connection?.status === "CONNECTED";

  useEffect(() => {
    if (!projectId) return;
    let cancelled = false;
    setProjectLoading(true);
    setSpaceLink(null);
    setPreview(null);
    setPublishResults(null);
    api.projects
      .confluenceSpace(projectId)
      .then((link) => {
        if (cancelled) return;
        setSpaceLink(
          link
            ? {
                id: link.id,
                projectId: link.project_id,
                connectionId: link.connection_id,
                spaceKey: link.space_key,
                spaceName: link.space_name,
                rootPageId: link.root_page_id,
                rootPageUrl: link.root_page_url,
              }
            : null
        );
      })
      .catch(() => {
        if (!cancelled) setSpaceLink(null);
      })
      .finally(() => {
        if (!cancelled) setProjectLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [projectId]);

  // Load the publish preview automatically once a space is configured —
  // this is a read; it never publishes anything by itself.
  useEffect(() => {
    if (!spaceLink) return;
    handleLoadPreview();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [spaceLink?.id]);

  async function handleConnect() {
    if (currentUserId === null) {
      setConnectError("No users exist yet to attribute this connection to.");
      return;
    }
    setConnecting(true);
    setConnectError(null);
    try {
      const result = await api.confluence.connect({ base_url: baseUrl, email, api_token: apiToken, connected_by_id: currentUserId });
      setConnection(toConfluenceConnectionItem(result));
      setApiToken(""); // never keep the API token in memory/state longer than the request that sent it
    } catch (err) {
      setConnectError(err instanceof ApiError ? err.message : "Failed to connect to Confluence.");
    } finally {
      setConnecting(false);
    }
  }

  async function handleDisconnect() {
    if (!connection) return;
    try {
      const result = await api.confluence.disconnect(connection.id);
      setConnection(toConfluenceConnectionItem(result));
      setSpaceLink(null);
    } catch (err) {
      setConnectError(err instanceof ApiError ? err.message : "Failed to disconnect.");
    }
  }

  async function handleSaveSpace() {
    if (!connection) return;
    setSavingSpace(true);
    setSaveSpaceError(null);
    try {
      const result = await api.confluence.saveSpace({ project_id: projectId, connection_id: connection.id, space_key: spaceKey });
      setSpaceLink({
        id: result.id,
        projectId: result.project_id,
        connectionId: result.connection_id,
        spaceKey: result.space_key,
        spaceName: result.space_name,
        rootPageId: result.root_page_id,
        rootPageUrl: result.root_page_url,
      });
      setSpaceKey("");
    } catch (err) {
      setSaveSpaceError(err instanceof ApiError ? err.message : "Failed to save this Confluence space — check the key and try again.");
    } finally {
      setSavingSpace(false);
    }
  }

  async function handleLoadPreview() {
    setPreviewLoading(true);
    setPreviewError(null);
    setPublishResults(null);
    try {
      const result = await api.confluence.publishPreview(projectId);
      setPreview(toConfluencePublishPreview(result));
      setSelected({}); // every checkbox starts unchecked — no auto-select
    } catch (err) {
      setPreviewError(err instanceof ApiError ? err.message : "Failed to load the publish preview.");
    } finally {
      setPreviewLoading(false);
    }
  }

  async function handlePublish() {
    if (!preview || currentUserId === null) return;
    const artifactTypes = Object.keys(selected).filter((k) => selected[k]);
    if (artifactTypes.length === 0) return;

    setPublishing(true);
    try {
      const response = await api.confluence.publish({ project_id: projectId, triggered_by_user_id: currentUserId, artifact_types: artifactTypes });
      setPublishResults(response.results.map(toConfluencePublishResultEntry));
      await handleLoadPreview(); // refresh so newly-published items show as already-published
    } catch (err) {
      setPreviewError(err instanceof ApiError ? err.message : "Failed to publish to Confluence.");
    } finally {
      setPublishing(false);
    }
  }

  const selectedCount = Object.values(selected).filter(Boolean).length;

  function renderItem(item: ConfluencePublishListItem) {
    const isPublishable = item.validationErrors.length === 0;
    return (
      <div key={item.artifactType} className="flex items-start gap-2 rounded border border-border p-2 text-xs">
        {isPublishable ? (
          <Checkbox
            checked={!!selected[item.artifactType]}
            onChange={(e) => setSelected((s) => ({ ...s, [item.artifactType]: e.target.checked }))}
            className="mt-0.5"
          />
        ) : (
          <div className="mt-0.5 h-4 w-4 shrink-0" />
        )}
        <div className="flex-1">
          <div className="flex items-center gap-2">
            <p className="font-medium">{item.label}</p>
            {item.artifactStatus ? <Badge variant="outline">{item.artifactStatus}</Badge> : null}
            {item.updateAvailable ? <Badge variant="success">Update available</Badge> : null}
          </div>
          {item.validationErrors.length > 0 ? (
            <ul className="mt-1 list-inside list-disc text-destructive">
              {item.validationErrors.map((e) => (
                <li key={e}>{e}</li>
              ))}
            </ul>
          ) : item.contentPreview ? (
            <p className="mt-1 line-clamp-2 text-muted-foreground">{item.contentPreview}</p>
          ) : null}
          {item.alreadyPublished ? (
            <a
              href={item.alreadyPublished.confluencePageUrl}
              target="_blank"
              rel="noreferrer"
              className="mt-1 inline-flex items-center gap-1 text-primary hover:underline"
            >
              <ExternalLink className="h-3 w-3" />
              {item.alreadyPublished.confluencePageTitle} (v{item.alreadyPublished.confluencePageVersion})
            </a>
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
              A Confluence Cloud API token (id.atlassian.com → Security → API tokens). Encrypted at rest and never
              shown again after saving.
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
            <CardTitle className="text-base">Space configuration</CardTitle>
            <CardDescription>
              Which project this Confluence space belongs to. Saving creates a root page — every published artifact
              becomes a child page under it.
            </CardDescription>
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
            ) : spaceLink ? (
              <div className="rounded-md border border-border p-3 text-xs">
                <p className="font-medium">
                  {spaceLink.spaceKey} — {spaceLink.spaceName ?? "(name not cached)"}
                </p>
                <a href={spaceLink.rootPageUrl} target="_blank" rel="noreferrer" className="mt-1 inline-flex items-center gap-1 text-primary hover:underline">
                  <ExternalLink className="h-3 w-3" />
                  Root page
                </a>
              </div>
            ) : (
              <div className="flex flex-wrap items-end gap-2">
                <div>
                  <label className="mb-1 block text-xs text-muted-foreground">Confluence space key</label>
                  <Input value={spaceKey} onChange={(e) => setSpaceKey(e.target.value.toUpperCase())} placeholder="ENG" className="w-40" />
                </div>
                <Button size="sm" onClick={handleSaveSpace} disabled={savingSpace || !spaceKey}>
                  {savingSpace ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : null}
                  {savingSpace ? "Saving…" : "Save Confluence space"}
                </Button>
              </div>
            )}
            {saveSpaceError ? <p className="text-xs text-destructive">{saveSpaceError}</p> : null}
          </CardContent>
        </Card>
      ) : null}

      {spaceLink ? (
        <Card>
          <CardHeader className="flex-row items-center justify-between space-y-0">
            <div>
              <CardTitle className="text-base">Publish preview</CardTitle>
              <CardDescription>
                Only APPROVED artifacts can be published; nothing is published until you select items below and
                confirm — see the checkboxes, all unchecked by default.
              </CardDescription>
            </div>
            <Button size="sm" onClick={handleLoadPreview} disabled={previewLoading}>
              {previewLoading ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : null}
              {previewLoading ? "Loading…" : "Refresh preview"}
            </Button>
          </CardHeader>
          <CardContent className="flex flex-col gap-4">
            {previewError ? <p className="text-xs text-destructive">{previewError}</p> : null}

            {preview ? (
              <>
                <div className="flex flex-col gap-1.5">{preview.items.map(renderItem)}</div>

                <div className="flex items-center gap-2 border-t border-border pt-3">
                  <Button size="sm" onClick={handlePublish} disabled={publishing || selectedCount === 0}>
                    {publishing ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Send className="h-3.5 w-3.5" />}
                    {publishing ? "Publishing…" : `Publish ${selectedCount} selected to Confluence`}
                  </Button>
                </div>

                {publishResults ? (
                  <div>
                    <h3 className="mb-2 text-xs font-medium uppercase tracking-wide text-muted-foreground">Results</h3>
                    <div className="flex flex-col gap-1">
                      {publishResults.map((r) => (
                        <div key={r.artifactType} className="flex items-center justify-between gap-2 text-xs">
                          <span>{r.artifactType}</span>
                          <div className="flex items-center gap-2">
                            <Badge variant={r.status === "published" || r.status === "updated" ? "success" : r.status === "skipped_invalid" ? "outline" : "destructive"}>
                              {r.status}
                            </Badge>
                            {r.confluencePageUrl ? (
                              <Link href={r.confluencePageUrl} target="_blank" className="inline-flex items-center gap-1 text-primary hover:underline">
                                <ExternalLink className="h-3 w-3" />
                                Open page
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
