"use client";

import { useEffect, useState } from "react";
import { Camera, GitBranch, Loader2, Unplug } from "lucide-react";

import { IntegrationStatusBadge } from "@/components/status-badge";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { EmptyState } from "@/components/ui/empty-state";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { api, ApiError } from "@/lib/api";
import { formatRelativeTime } from "@/lib/format";
import {
  toGitHubRepoOption,
  toGithubConnectionItem,
  toGithubRepositoryItem,
  toRepositoryFileContentResult,
  toRepositoryFileIndexItem,
  toRepositorySnapshotItem,
} from "@/lib/mappers";
import type {
  GitHubRepoOption,
  GithubConnectionItem,
  GithubRepositoryItem,
  RepositoryFileContentResult,
  RepositoryFileIndexItem,
  RepositorySnapshotItem,
} from "@/lib/types";

export function GithubIntegrationView({
  connection: initialConnection,
  projects,
  currentUserId,
}: {
  connection: GithubConnectionItem | null;
  projects: { id: string; name: string }[];
  currentUserId: string | null;
}) {
  const [connection, setConnection] = useState(initialConnection);
  const [token, setToken] = useState("");
  const [connecting, setConnecting] = useState(false);
  const [connectError, setConnectError] = useState<string | null>(null);

  const [projectId, setProjectId] = useState(projects[0]?.id ?? "");
  const [repository, setRepository] = useState<GithubRepositoryItem | null>(null);
  const [repoLoading, setRepoLoading] = useState(false);
  const [owner, setOwner] = useState("");
  const [name, setName] = useState("");
  const [savingRepo, setSavingRepo] = useState(false);
  const [saveRepoError, setSaveRepoError] = useState<string | null>(null);

  // The connected token's own repo list — lets a repo be picked instead of
  // typed by hand, so a typo'd owner/name never reaches GitHub as a
  // confusing 404 at save time.
  const [repoOptions, setRepoOptions] = useState<GitHubRepoOption[]>([]);
  const [repoOptionsLoading, setRepoOptionsLoading] = useState(false);
  const [repoOptionsError, setRepoOptionsError] = useState<string | null>(null);
  const [manualEntry, setManualEntry] = useState(false);

  const [branches, setBranches] = useState<string[] | null>(null);
  const [branchesLoading, setBranchesLoading] = useState(false);

  const [snapshots, setSnapshots] = useState<RepositorySnapshotItem[]>([]);
  const [creatingSnapshot, setCreatingSnapshot] = useState(false);
  const [snapshotError, setSnapshotError] = useState<string | null>(null);

  const [files, setFiles] = useState<RepositoryFileIndexItem[]>([]);
  const [selectedPath, setSelectedPath] = useState<string | null>(null);
  const [fileContent, setFileContent] = useState<RepositoryFileContentResult | null>(null);
  const [fileLoading, setFileLoading] = useState(false);

  const isConnected = connection?.status === "CONNECTED";

  // Load this project's saved repo config whenever the selected project changes.
  useEffect(() => {
    if (!projectId) return;
    let cancelled = false;
    setRepoLoading(true);
    setRepository(null);
    setBranches(null);
    setSnapshots([]);
    setFiles([]);
    setSelectedPath(null);
    setFileContent(null);
    api.projects
      .githubRepository(projectId)
      .then((repo) => {
        if (cancelled) return;
        setRepository(repo ? toGithubRepositoryItem(repo) : null);
      })
      .catch(() => {
        if (!cancelled) setRepository(null);
      })
      .finally(() => {
        if (!cancelled) setRepoLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [projectId]);

  // Once a repo is loaded, pull its snapshot history for display.
  useEffect(() => {
    if (!repository) return;
    api.github
      .listSnapshots(repository.id)
      .then((rows) => setSnapshots(rows.map(toRepositorySnapshotItem)))
      .catch(() => setSnapshots([]));
  }, [repository]);

  // Once connected, fetch the token's own repo list so it can be picked
  // from a dropdown instead of typed by hand.
  useEffect(() => {
    if (connection?.status !== "CONNECTED") return;
    let cancelled = false;
    setRepoOptionsLoading(true);
    setRepoOptionsError(null);
    api.github
      .listRepositoryOptions(connection.id)
      .then((rows) => {
        if (cancelled) return;
        setRepoOptions(rows.map(toGitHubRepoOption));
      })
      .catch((err) => {
        if (cancelled) return;
        setRepoOptions([]);
        // Not fatal — the manual owner/name fields below still work.
        setRepoOptionsError(err instanceof ApiError ? err.message : "Failed to load this account's repositories.");
      })
      .finally(() => {
        if (!cancelled) setRepoOptionsLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [connection]);

  async function handleConnect() {
    if (currentUserId === null) {
      setConnectError("No users exist yet to attribute this connection to.");
      return;
    }
    setConnecting(true);
    setConnectError(null);
    try {
      const result = await api.github.connect({ access_token: token, connected_by_id: currentUserId });
      setConnection(toGithubConnectionItem(result));
      setToken(""); // never keep the PAT in memory/state longer than the request that sent it
    } catch (err) {
      setConnectError(err instanceof ApiError ? err.message : "Failed to connect to GitHub.");
    } finally {
      setConnecting(false);
    }
  }

  async function handleDisconnect() {
    if (!connection) return;
    try {
      const result = await api.github.disconnect(connection.id);
      setConnection(toGithubConnectionItem(result));
      setRepository(null);
    } catch (err) {
      setConnectError(err instanceof ApiError ? err.message : "Failed to disconnect.");
    }
  }

  async function handleSaveRepository() {
    if (!connection) return;
    setSavingRepo(true);
    setSaveRepoError(null);
    try {
      const result = await api.github.saveRepository({ project_id: projectId, connection_id: connection.id, owner, name });
      setRepository(toGithubRepositoryItem(result));
      setOwner("");
      setName("");
    } catch (err) {
      setSaveRepoError(err instanceof ApiError ? err.message : "Failed to save this repository — check the owner/name and try again.");
    } finally {
      setSavingRepo(false);
    }
  }

  async function handleLoadBranches() {
    if (!repository) return;
    setBranchesLoading(true);
    try {
      setBranches(await api.github.listBranches(repository.id));
    } catch (err) {
      setBranches([]);
      setSnapshotError(err instanceof ApiError ? err.message : "Failed to load branches.");
    } finally {
      setBranchesLoading(false);
    }
  }

  async function handleCreateSnapshot() {
    if (!repository || currentUserId === null) return;
    setCreatingSnapshot(true);
    setSnapshotError(null);
    try {
      const snapshot = await api.github.createSnapshot(repository.id, { triggered_by_id: currentUserId });
      setSnapshots((prev) => [toRepositorySnapshotItem(snapshot), ...prev]);
      const fileRows = await api.github.listSnapshotFiles(snapshot.id);
      setFiles(fileRows.map(toRepositoryFileIndexItem));
    } catch (err) {
      setSnapshotError(err instanceof ApiError ? err.message : "Failed to create a snapshot.");
    } finally {
      setCreatingSnapshot(false);
    }
  }

  async function handleViewSnapshotFiles(snapshotId: string) {
    setSelectedPath(null);
    setFileContent(null);
    try {
      const fileRows = await api.github.listSnapshotFiles(snapshotId);
      setFiles(fileRows.map(toRepositoryFileIndexItem));
    } catch {
      setFiles([]);
    }
  }

  async function handlePreviewFile(path: string) {
    if (!repository) return;
    setSelectedPath(path);
    setFileLoading(true);
    setFileContent(null);
    try {
      const content = await api.github.readFile(repository.id, path);
      setFileContent(toRepositoryFileContentResult(content));
    } catch (err) {
      setFileContent({
        path, sha: "", size: 0, content: null, truncated: false, isBinary: false,
      });
      setSnapshotError(err instanceof ApiError ? err.message : "Failed to read this file.");
    } finally {
      setFileLoading(false);
    }
  }

  return (
    <div className="flex flex-col gap-4">
      <Card>
        <CardHeader className="flex-row items-center justify-between space-y-0">
          <div>
            <CardTitle className="text-base">Connection</CardTitle>
            <CardDescription>
              A personal access token with read-only repo access. It is encrypted at rest and never shown again
              after saving — see docs/github-setup.md.
            </CardDescription>
          </div>
          {connection ? <IntegrationStatusBadge status={connection.status} /> : null}
        </CardHeader>
        <CardContent className="flex flex-col gap-3">
          {isConnected ? (
            <div className="flex items-center justify-between gap-2 rounded-md border border-border bg-muted/40 p-3 text-xs">
              <div>
                <p className="font-medium">{connection?.githubUsername}</p>
                <p className="text-muted-foreground">Token {connection?.tokenHint}</p>
                {connection?.scopes && connection.scopes.length > 0 ? (
                  <div className="mt-1 flex flex-wrap gap-1">
                    {connection.scopes.map((s) => (
                      <Badge key={s} variant="outline">
                        {s}
                      </Badge>
                    ))}
                  </div>
                ) : null}
              </div>
              <Button variant="outline" size="sm" onClick={handleDisconnect}>
                <Unplug className="h-3.5 w-3.5" />
                Disconnect
              </Button>
            </div>
          ) : (
            <div className="flex flex-wrap items-center gap-2">
              <Input
                type="password"
                placeholder="ghp_..."
                value={token}
                onChange={(e) => setToken(e.target.value)}
                className="max-w-xs"
                autoComplete="off"
              />
              <Button size="sm" onClick={handleConnect} disabled={connecting || !token}>
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
            <CardTitle className="text-base">Repository configuration</CardTitle>
            <CardDescription>Which project this repo belongs to, and which repo to scan.</CardDescription>
          </CardHeader>
          <CardContent className="flex flex-col gap-3">
            <Select value={projectId} onChange={(e) => setProjectId(e.target.value)} className="max-w-xs">
              {projects.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.name}
                </option>
              ))}
            </Select>

            {repoLoading ? (
              <p className="text-xs text-muted-foreground">Loading…</p>
            ) : repository ? (
              <div className="rounded-md border border-border p-3 text-xs">
                <p className="font-medium">
                  {repository.owner}/{repository.name}
                </p>
                <p className="text-muted-foreground">
                  Default branch: {repository.defaultBranch ?? "—"} · {repository.isPrivate ? "Private" : "Public"}
                </p>
                {repository.description ? <p className="mt-1 text-muted-foreground">{repository.description}</p> : null}
              </div>
            ) : (
              <div className="flex flex-col gap-2">
                {!manualEntry && repoOptions.length > 0 ? (
                  <div className="flex flex-wrap items-end gap-2">
                    <div>
                      <label className="mb-1 block text-xs text-muted-foreground">Repository</label>
                      <Select
                        value={owner && name ? `${owner}/${name}` : ""}
                        onChange={(e) => {
                          const option = repoOptions.find((r) => r.fullName === e.target.value);
                          setOwner(option?.owner ?? "");
                          setName(option?.name ?? "");
                        }}
                        className="w-72"
                      >
                        <option value="">Choose a repository…</option>
                        {repoOptions.map((r) => (
                          <option key={r.fullName} value={r.fullName}>
                            {r.fullName} {r.isPrivate ? "(private)" : ""}
                          </option>
                        ))}
                      </Select>
                    </div>
                    <Button size="sm" onClick={handleSaveRepository} disabled={savingRepo || !owner || !name}>
                      {savingRepo ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : null}
                      {savingRepo ? "Saving…" : "Save repository configuration"}
                    </Button>
                    <Button variant="ghost" size="sm" onClick={() => setManualEntry(true)}>
                      Enter owner/name manually instead
                    </Button>
                  </div>
                ) : (
                  <div className="flex flex-wrap items-end gap-2">
                    <div>
                      <label className="mb-1 block text-xs text-muted-foreground">Owner</label>
                      <Input value={owner} onChange={(e) => setOwner(e.target.value)} placeholder="octocat" className="w-40" />
                    </div>
                    <div>
                      <label className="mb-1 block text-xs text-muted-foreground">Repository</label>
                      <Input value={name} onChange={(e) => setName(e.target.value)} placeholder="hello-world" className="w-48" />
                    </div>
                    <Button size="sm" onClick={handleSaveRepository} disabled={savingRepo || !owner || !name}>
                      {savingRepo ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : null}
                      {savingRepo ? "Saving…" : "Save repository configuration"}
                    </Button>
                    {repoOptions.length > 0 ? (
                      <Button variant="ghost" size="sm" onClick={() => setManualEntry(false)}>
                        Choose from list instead
                      </Button>
                    ) : null}
                  </div>
                )}
                {repoOptionsLoading ? <p className="text-xs text-muted-foreground">Loading this account&rsquo;s repositories…</p> : null}
                {repoOptionsError ? <p className="text-xs text-muted-foreground">{repoOptionsError} You can still enter the owner/name manually.</p> : null}
              </div>
            )}
            {saveRepoError ? <p className="text-xs text-destructive">{saveRepoError}</p> : null}
          </CardContent>
        </Card>
      ) : null}

      {repository ? (
        <Card>
          <CardHeader className="flex-row items-center justify-between space-y-0">
            <CardTitle className="text-base">Read-only scan</CardTitle>
            <div className="flex gap-2">
              <Button variant="outline" size="sm" onClick={handleLoadBranches} disabled={branchesLoading}>
                <GitBranch className="h-3.5 w-3.5" />
                {branchesLoading ? "Loading…" : "List branches"}
              </Button>
              <Button size="sm" onClick={handleCreateSnapshot} disabled={creatingSnapshot}>
                {creatingSnapshot ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Camera className="h-3.5 w-3.5" />}
                {creatingSnapshot ? "Scanning…" : "Create Snapshot"}
              </Button>
            </div>
          </CardHeader>
          <CardContent className="flex flex-col gap-4">
            {branches ? (
              <div className="flex flex-wrap gap-1">
                {branches.map((b) => (
                  <Badge key={b} variant="outline">
                    {b}
                  </Badge>
                ))}
              </div>
            ) : null}
            {snapshotError ? <p className="text-xs text-destructive">{snapshotError}</p> : null}

            <div>
              <h3 className="mb-2 text-xs font-medium uppercase tracking-wide text-muted-foreground">Snapshot history</h3>
              {snapshots.length === 0 ? (
                <EmptyState icon={Camera} title="No snapshots yet" description="Create one to scan the repo's current file tree." className="py-6" />
              ) : (
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>Ref</TableHead>
                      <TableHead>Commit</TableHead>
                      <TableHead>Files</TableHead>
                      <TableHead>When</TableHead>
                      <TableHead>Triggered by</TableHead>
                      <TableHead />
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {snapshots.map((s) => (
                      <TableRow key={s.id}>
                        <TableCell>{s.ref}</TableCell>
                        <TableCell className="font-mono text-xs">{s.commitSha.slice(0, 7)}</TableCell>
                        <TableCell>
                          {s.fileCount}
                          {s.truncated ? (
                            <Badge variant="warning" className="ml-1">
                              truncated
                            </Badge>
                          ) : null}
                        </TableCell>
                        <TableCell>{formatRelativeTime(s.createdAt)}</TableCell>
                        <TableCell>{s.triggeredByName ?? "—"}</TableCell>
                        <TableCell>
                          <Button variant="ghost" size="sm" onClick={() => handleViewSnapshotFiles(s.id)}>
                            View files
                          </Button>
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              )}
            </div>

            {files.length > 0 ? (
              <div className="grid grid-cols-1 gap-3 lg:grid-cols-2">
                <div className="max-h-64 overflow-y-auto rounded-md border border-border">
                  {files
                    .filter((f) => f.entryType === "FILE")
                    .map((f) => (
                      <button
                        key={f.id}
                        onClick={() => handlePreviewFile(f.path)}
                        className={`block w-full truncate px-2 py-1 text-left text-xs hover:bg-muted/60 ${
                          selectedPath === f.path ? "bg-muted" : ""
                        }`}
                      >
                        {f.path}
                      </button>
                    ))}
                </div>
                <div className="max-h-64 overflow-y-auto rounded-md border border-border p-2">
                  {fileLoading ? (
                    <p className="text-xs text-muted-foreground">Loading…</p>
                  ) : fileContent ? (
                    fileContent.truncated ? (
                      <p className="text-xs text-muted-foreground">File too large to preview.</p>
                    ) : fileContent.isBinary ? (
                      <p className="text-xs text-muted-foreground">Binary file — no text preview.</p>
                    ) : (
                      <pre className="whitespace-pre-wrap font-mono text-[11px]">{fileContent.content}</pre>
                    )
                  ) : (
                    <p className="text-xs text-muted-foreground">Select a file to preview it (read-only).</p>
                  )}
                </div>
              </div>
            ) : null}
          </CardContent>
        </Card>
      ) : null}
    </div>
  );
}
