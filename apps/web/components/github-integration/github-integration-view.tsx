"use client";

import { useEffect, useState } from "react";
import { Camera, GitBranch, Loader2, Plus, Rocket, Star, Trash2, Unplug } from "lucide-react";

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
  connections: initialConnections,
  projects,
  currentUserId,
}: {
  connections: GithubConnectionItem[];
  projects: { id: string; name: string }[];
  currentUserId: string | null;
}) {
  const [connections, setConnections] = useState(initialConnections);
  const [token, setToken] = useState("");
  const [connecting, setConnecting] = useState(false);
  const [connectError, setConnectError] = useState<string | null>(null);
  // Always start visible when no account is connected yet; otherwise a
  // deliberate "Connect another account" click reveals it — connecting a
  // second/third GitHub account is additive, never a replace.
  const [showConnectForm, setShowConnectForm] = useState(initialConnections.length === 0);

  const [projectId, setProjectId] = useState(projects[0]?.id ?? "");
  const [repositories, setRepositories] = useState<GithubRepositoryItem[]>([]);
  const [reposLoading, setReposLoading] = useState(false);
  const [selectedRepositoryId, setSelectedRepositoryId] = useState<string | null>(null);
  const [repoActionError, setRepoActionError] = useState<string | null>(null);
  const [repoActionBusyId, setRepoActionBusyId] = useState<string | null>(null);

  // "Add repository" form — which connected account, then which of that
  // account's own repos.
  const [showAddRepoForm, setShowAddRepoForm] = useState(false);
  const [addRepoConnectionId, setAddRepoConnectionId] = useState("");
  const [owner, setOwner] = useState("");
  const [name, setName] = useState("");
  const [savingRepo, setSavingRepo] = useState(false);
  const [saveRepoError, setSaveRepoError] = useState<string | null>(null);

  const [repoOptions, setRepoOptions] = useState<GitHubRepoOption[]>([]);
  const [repoOptionsLoading, setRepoOptionsLoading] = useState(false);
  const [repoOptionsError, setRepoOptionsError] = useState<string | null>(null);
  const [manualEntry, setManualEntry] = useState(false);

  const [branches, setBranches] = useState<string[] | null>(null);
  const [branchesLoading, setBranchesLoading] = useState(false);

  const [snapshots, setSnapshots] = useState<RepositorySnapshotItem[]>([]);
  const [creatingSnapshot, setCreatingSnapshot] = useState(false);
  const [snapshotError, setSnapshotError] = useState<string | null>(null);

  const [bootstrapping, setBootstrapping] = useState(false);
  const [bootstrapError, setBootstrapError] = useState<string | null>(null);
  const [bootstrapResult, setBootstrapResult] = useState<{ branch: string; fileCount: number } | null>(null);

  const [files, setFiles] = useState<RepositoryFileIndexItem[]>([]);
  const [selectedPath, setSelectedPath] = useState<string | null>(null);
  const [fileContent, setFileContent] = useState<RepositoryFileContentResult | null>(null);
  const [fileLoading, setFileLoading] = useState(false);

  const connectedAccounts = connections.filter((c) => c.status === "CONNECTED");
  const selectedRepository = repositories.find((r) => r.id === selectedRepositoryId) ?? null;

  // Load this project's connected repositories whenever the selected project changes.
  useEffect(() => {
    if (!projectId) return;
    let cancelled = false;
    setReposLoading(true);
    setSelectedRepositoryId(null);
    setBranches(null);
    setSnapshots([]);
    setFiles([]);
    setSelectedPath(null);
    setFileContent(null);
    api.projects
      .githubRepositories(projectId)
      .then((rows) => {
        if (cancelled) return;
        const items = rows.map(toGithubRepositoryItem);
        setRepositories(items);
        setSelectedRepositoryId(items[0]?.id ?? null);
      })
      .catch(() => {
        if (!cancelled) setRepositories([]);
      })
      .finally(() => {
        if (!cancelled) setReposLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [projectId]);

  // Once a repo is selected, pull its snapshot history for display.
  useEffect(() => {
    setBranches(null);
    setFiles([]);
    setSelectedPath(null);
    setFileContent(null);
    if (!selectedRepository) {
      setSnapshots([]);
      return;
    }
    const repositoryId = selectedRepository.id;
    api.github
      .listSnapshots(repositoryId)
      .then((rows) => setSnapshots(rows.map(toRepositorySnapshotItem)))
      .catch(() => setSnapshots([]));
    // eslint-disable-next-line react-hooks/exhaustive-deps -- only the id
    // should retrigger this fetch; `repositories` (and so `selectedRepository`,
    // derived fresh from it each render) also changes on every set/remove-
    // primary action, which must not re-fetch snapshots.
  }, [selectedRepository?.id]);

  // Fetch a chosen account's own repo list so it can be picked from a
  // dropdown instead of typed by hand — refetches whenever the "add
  // repository" form's account selection changes.
  useEffect(() => {
    if (!showAddRepoForm || !addRepoConnectionId) {
      setRepoOptions([]);
      return;
    }
    let cancelled = false;
    setRepoOptionsLoading(true);
    setRepoOptionsError(null);
    api.github
      .listRepositoryOptions(addRepoConnectionId)
      .then((rows) => {
        if (cancelled) return;
        setRepoOptions(rows.map(toGitHubRepoOption));
      })
      .catch((err) => {
        if (cancelled) return;
        setRepoOptions([]);
        setRepoOptionsError(err instanceof ApiError ? err.message : "Failed to load this account's repositories.");
      })
      .finally(() => {
        if (!cancelled) setRepoOptionsLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [showAddRepoForm, addRepoConnectionId]);

  async function handleConnect() {
    if (currentUserId === null) {
      setConnectError("No users exist yet to attribute this connection to.");
      return;
    }
    setConnecting(true);
    setConnectError(null);
    try {
      const result = await api.github.connect({ access_token: token, connected_by_id: currentUserId });
      setConnections((prev) => [toGithubConnectionItem(result), ...prev]);
      setToken(""); // never keep the PAT in memory/state longer than the request that sent it
      setShowConnectForm(false);
    } catch (err) {
      setConnectError(err instanceof ApiError ? err.message : "Failed to connect to GitHub.");
    } finally {
      setConnecting(false);
    }
  }

  async function handleDisconnect(connectionId: string) {
    try {
      const result = await api.github.disconnect(connectionId);
      setConnections((prev) => prev.map((c) => (c.id === connectionId ? toGithubConnectionItem(result) : c)));
    } catch (err) {
      setConnectError(err instanceof ApiError ? err.message : "Failed to disconnect.");
    }
  }

  async function handleSaveRepository() {
    if (!addRepoConnectionId) return;
    setSavingRepo(true);
    setSaveRepoError(null);
    try {
      const result = await api.github.saveRepository({ project_id: projectId, connection_id: addRepoConnectionId, owner, name });
      const item = toGithubRepositoryItem(result);
      setRepositories((prev) => [...prev, item].sort((a, b) => Number(b.isPrimary) - Number(a.isPrimary)));
      setSelectedRepositoryId(item.id);
      setOwner("");
      setName("");
      setShowAddRepoForm(false);
      setAddRepoConnectionId("");
    } catch (err) {
      setSaveRepoError(err instanceof ApiError ? err.message : "Failed to save this repository — check the owner/name and try again.");
    } finally {
      setSavingRepo(false);
    }
  }

  async function handleSetPrimary(repositoryId: string) {
    setRepoActionBusyId(repositoryId);
    setRepoActionError(null);
    try {
      await api.github.setPrimaryRepository(repositoryId);
      setRepositories((prev) => prev.map((r) => ({ ...r, isPrimary: r.id === repositoryId })));
    } catch (err) {
      setRepoActionError(err instanceof ApiError ? err.message : "Failed to set this repository as primary.");
    } finally {
      setRepoActionBusyId(null);
    }
  }

  async function handleRemoveRepository(repositoryId: string) {
    setRepoActionBusyId(repositoryId);
    setRepoActionError(null);
    try {
      await api.github.removeRepository(repositoryId);
      setRepositories((prev) => {
        const removed = prev.find((r) => r.id === repositoryId);
        const rest = prev.filter((r) => r.id !== repositoryId);
        // Mirror the backend's own promotion rule locally so the badge
        // doesn't flicker/disappear until the next full refetch.
        if (removed?.isPrimary && rest.length > 0 && !rest.some((r) => r.isPrimary)) {
          rest[0] = { ...rest[0], isPrimary: true };
        }
        return rest;
      });
      setSelectedRepositoryId((prev) => (prev === repositoryId ? null : prev));
    } catch (err) {
      setRepoActionError(err instanceof ApiError ? err.message : "Failed to remove this repository.");
    } finally {
      setRepoActionBusyId(null);
    }
  }

  async function handleLoadBranches() {
    if (!selectedRepository) return;
    setBranchesLoading(true);
    try {
      setBranches(await api.github.listBranches(selectedRepository.id));
    } catch (err) {
      setBranches([]);
      setSnapshotError(err instanceof ApiError ? err.message : "Failed to load branches.");
    } finally {
      setBranchesLoading(false);
    }
  }

  async function handleCreateSnapshot() {
    if (!selectedRepository || currentUserId === null) return;
    setCreatingSnapshot(true);
    setSnapshotError(null);
    try {
      const snapshot = await api.github.createSnapshot(selectedRepository.id, { triggered_by_id: currentUserId });
      setSnapshots((prev) => [toRepositorySnapshotItem(snapshot), ...prev]);
      const fileRows = await api.github.listSnapshotFiles(snapshot.id);
      setFiles(fileRows.map(toRepositoryFileIndexItem));
    } catch (err) {
      setSnapshotError(err instanceof ApiError ? err.message : "Failed to create a snapshot.");
    } finally {
      setCreatingSnapshot(false);
    }
  }

  async function handleBootstrapRepository() {
    if (!selectedRepository || currentUserId === null) return;
    setBootstrapping(true);
    setBootstrapError(null);
    setBootstrapResult(null);
    try {
      const result = await api.github.bootstrapRepository(projectId, { triggered_by_user_id: currentUserId });
      setBootstrapResult({ branch: result.branch, fileCount: result.commits.length });
      // The repo is no longer empty — clear the stale error and let the
      // human confirm by scanning it for real, same as any other repo.
      setSnapshotError(null);
    } catch (err) {
      setBootstrapError(err instanceof ApiError ? err.message : "Failed to bootstrap this repository.");
    } finally {
      setBootstrapping(false);
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
    if (!selectedRepository) return;
    setSelectedPath(path);
    setFileLoading(true);
    setFileContent(null);
    try {
      const content = await api.github.readFile(selectedRepository.id, path);
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
            <CardTitle className="text-base">Connected accounts</CardTitle>
            <CardDescription>
              One or more personal access tokens with read-only repo access, each encrypted at rest and never shown
              again after saving — see docs/github-setup.md. Connect as many GitHub accounts as your projects need.
            </CardDescription>
          </div>
          {!showConnectForm ? (
            <Button variant="outline" size="sm" onClick={() => setShowConnectForm(true)}>
              <Plus className="h-3.5 w-3.5" />
              Connect another account
            </Button>
          ) : null}
        </CardHeader>
        <CardContent className="flex flex-col gap-3">
          {connections.length === 0 ? (
            <EmptyState icon={Unplug} title="No GitHub accounts connected yet" description="Connect one below to get started." className="py-4" />
          ) : (
            <div className="flex flex-col gap-2">
              {connections.map((c) => (
                <div key={c.id} className="flex items-center justify-between gap-2 rounded-md border border-border bg-muted/40 p-3 text-xs">
                  <div>
                    <div className="flex items-center gap-2">
                      <p className="font-medium">{c.githubUsername ?? "(unknown account)"}</p>
                      <IntegrationStatusBadge status={c.status} />
                    </div>
                    <p className="text-muted-foreground">Token {c.tokenHint}</p>
                    {c.scopes && c.scopes.length > 0 ? (
                      <div className="mt-1 flex flex-wrap gap-1">
                        {c.scopes.map((s) => (
                          <Badge key={s} variant="outline">
                            {s}
                          </Badge>
                        ))}
                      </div>
                    ) : null}
                  </div>
                  {c.status === "CONNECTED" ? (
                    <Button variant="outline" size="sm" onClick={() => handleDisconnect(c.id)}>
                      <Unplug className="h-3.5 w-3.5" />
                      Disconnect
                    </Button>
                  ) : null}
                </div>
              ))}
            </div>
          )}

          {showConnectForm ? (
            <div className="flex flex-wrap items-center gap-2 border-t border-border pt-3">
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
              {connections.length > 0 ? (
                <Button variant="ghost" size="sm" onClick={() => setShowConnectForm(false)}>
                  Cancel
                </Button>
              ) : null}
            </div>
          ) : null}
          {connectError ? <p className="text-xs text-destructive">{connectError}</p> : null}
        </CardContent>
      </Card>

      {connectedAccounts.length > 0 ? (
        <Card>
          <CardHeader>
            <CardTitle className="text-base">Repository configuration</CardTitle>
            <CardDescription>
              A project can connect more than one repository (e.g. a separate frontend/backend/infra repo) — each
              targeting any of the accounts above. Exactly one is &ldquo;Primary&rdquo;: the repo a task uses by
              default when it doesn&apos;t explicitly name a different one.
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

            {reposLoading ? (
              <p className="text-xs text-muted-foreground">Loading…</p>
            ) : (
              <div className="flex flex-col gap-2">
                {repositories.map((repo) => {
                  const account = connections.find((c) => c.id === repo.connectionId);
                  const busy = repoActionBusyId === repo.id;
                  return (
                    <button
                      key={repo.id}
                      onClick={() => setSelectedRepositoryId(repo.id)}
                      className={`flex items-center justify-between gap-2 rounded-md border p-3 text-left text-xs transition-colors ${
                        selectedRepositoryId === repo.id ? "border-primary bg-primary/5" : "border-border"
                      }`}
                    >
                      <div>
                        <div className="flex items-center gap-2">
                          <p className="font-medium">
                            {repo.owner}/{repo.name}
                          </p>
                          {repo.isPrimary ? (
                            <Badge variant="default" className="gap-1">
                              <Star className="h-3 w-3" />
                              Primary
                            </Badge>
                          ) : null}
                        </div>
                        <p className="text-muted-foreground">
                          {account?.githubUsername ?? "unknown account"} · Default branch: {repo.defaultBranch ?? "—"} ·{" "}
                          {repo.isPrivate ? "Private" : "Public"}
                        </p>
                      </div>
                      <div className="flex shrink-0 gap-1" onClick={(e) => e.stopPropagation()}>
                        {!repo.isPrimary ? (
                          <Button variant="outline" size="sm" disabled={busy} onClick={() => handleSetPrimary(repo.id)}>
                            {busy ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : "Set primary"}
                          </Button>
                        ) : null}
                        <Button variant="ghost" size="sm" disabled={busy} onClick={() => handleRemoveRepository(repo.id)}>
                          <Trash2 className="h-3.5 w-3.5" />
                        </Button>
                      </div>
                    </button>
                  );
                })}
                {repositories.length === 0 ? (
                  <EmptyState icon={GitBranch} title="No repositories connected" description="Add one below to start scanning it." className="py-4" />
                ) : null}
              </div>
            )}
            {repoActionError ? <p className="text-xs text-destructive">{repoActionError}</p> : null}

            {!showAddRepoForm ? (
              <Button variant="outline" size="sm" className="self-start" onClick={() => setShowAddRepoForm(true)}>
                <Plus className="h-3.5 w-3.5" />
                Add repository
              </Button>
            ) : (
              <div className="flex flex-col gap-2 border-t border-border pt-3">
                <div>
                  <label className="mb-1 block text-xs text-muted-foreground">GitHub account</label>
                  <Select
                    value={addRepoConnectionId}
                    onChange={(e) => {
                      setAddRepoConnectionId(e.target.value);
                      setOwner("");
                      setName("");
                      setManualEntry(false);
                    }}
                    className="w-64"
                  >
                    <option value="">Choose an account…</option>
                    {connectedAccounts.map((c) => (
                      <option key={c.id} value={c.id}>
                        {c.githubUsername ?? c.tokenHint}
                      </option>
                    ))}
                  </Select>
                </div>

                {addRepoConnectionId ? (
                  !manualEntry && repoOptions.length > 0 ? (
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
                        {savingRepo ? "Saving…" : "Add repository"}
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
                        {savingRepo ? "Saving…" : "Add repository"}
                      </Button>
                      {repoOptions.length > 0 ? (
                        <Button variant="ghost" size="sm" onClick={() => setManualEntry(false)}>
                          Choose from list instead
                        </Button>
                      ) : null}
                    </div>
                  )
                ) : null}
                {repoOptionsLoading ? <p className="text-xs text-muted-foreground">Loading this account&rsquo;s repositories…</p> : null}
                {repoOptionsError ? <p className="text-xs text-muted-foreground">{repoOptionsError} You can still enter the owner/name manually.</p> : null}
                <Button variant="ghost" size="sm" className="self-start" onClick={() => setShowAddRepoForm(false)}>
                  Cancel
                </Button>
              </div>
            )}
            {saveRepoError ? <p className="text-xs text-destructive">{saveRepoError}</p> : null}
          </CardContent>
        </Card>
      ) : null}

      {selectedRepository ? (
        <Card>
          <CardHeader className="flex-row items-center justify-between space-y-0">
            <div>
              <CardTitle className="text-base">Read-only scan</CardTitle>
              <CardDescription>
                {selectedRepository.owner}/{selectedRepository.name}
              </CardDescription>
            </div>
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
            {snapshotError ? (
              <div className="flex flex-col gap-2 rounded-md border border-amber-400/60 bg-amber-50 p-3 dark:border-amber-900 dark:bg-amber-950/30">
                <p className="text-xs text-destructive">{snapshotError}</p>
                {/empty/i.test(snapshotError) ? (
                  <>
                    <p className="text-xs text-muted-foreground">
                      This repository has no commits yet, so it can&apos;t be scanned. Bootstrap it with a real starter
                      project generated from this project&apos;s Technology Stack and approved documents (HLD, Solution
                      Discovery) — the one action that commits directly to {selectedRepository.owner}/
                      {selectedRepository.name}&apos;s default branch, since there&apos;s no history yet to open a PR
                      against.
                    </p>
                    <Button size="sm" className="w-fit" onClick={handleBootstrapRepository} disabled={bootstrapping}>
                      {bootstrapping ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Rocket className="h-3.5 w-3.5" />}
                      {bootstrapping ? "Bootstrapping…" : "Bootstrap repository"}
                    </Button>
                  </>
                ) : null}
              </div>
            ) : null}
            {bootstrapError ? <p className="text-xs text-destructive">{bootstrapError}</p> : null}
            {bootstrapResult ? (
              <p className="text-xs text-emerald-600">
                Bootstrapped {bootstrapResult.fileCount} file{bootstrapResult.fileCount === 1 ? "" : "s"} onto{" "}
                <span className="font-mono">{bootstrapResult.branch}</span>. Click &ldquo;Create Snapshot&rdquo; above to
                scan it.
              </p>
            ) : null}

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
