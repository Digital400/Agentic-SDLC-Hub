"use client";

import { useEffect, useState } from "react";
import { CheckCircle2, FileText, Loader2, PlusCircle, Save, ShieldCheck, Trash2 } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Textarea } from "@/components/ui/textarea";
import { api, ApiError, ApiRelease, ApiReleaseBoard, ApiStory } from "@/lib/api";

function releaseStatusVariant(status: ApiRelease["status"]): "gray" | "info" | "success" | "destructive" {
  if (status === "DRAFT") return "gray";
  if (status === "APPROVED") return "info";
  if (status === "RELEASED") return "success";
  return "destructive";
}

export function ReleasePlanningView({
  projectId,
  initialReleases,
  currentUserId,
}: {
  projectId: string;
  initialReleases: ApiRelease[];
  currentUserId: string | null;
}) {
  const [releases, setReleases] = useState(initialReleases);
  const [readyStories, setReadyStories] = useState<ApiStory[]>([]);
  const [selectedReleaseId, setSelectedReleaseId] = useState<string | null>(initialReleases[0]?.id ?? null);
  const [board, setBoard] = useState<ApiReleaseBoard | null>(null);
  const [checklist, setChecklist] = useState<string[]>([]);
  const [loadingBoard, setLoadingBoard] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const [showNewRelease, setShowNewRelease] = useState(initialReleases.length === 0);
  const [newName, setNewName] = useState("");
  const [newVersion, setNewVersion] = useState("");
  const [newTargetDate, setNewTargetDate] = useState("");

  const [notesDraft, setNotesDraft] = useState("");

  const selectedRelease = releases.find((r) => r.id === selectedReleaseId) ?? null;
  const editable = selectedRelease !== null && selectedRelease.status === "DRAFT";

  async function refreshReadyStories() {
    try {
      setReadyStories(await api.releases.releaseReadyStories(projectId));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to load release-ready stories.");
    }
  }

  async function refreshBoard(releaseId: string) {
    setLoadingBoard(true);
    try {
      const result = await api.releases.board(releaseId);
      setBoard(result);
      setNotesDraft(result.release.release_notes);
      setChecklist(await api.releases.approvalChecklist(releaseId));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to load the release board.");
    } finally {
      setLoadingBoard(false);
    }
  }

  useEffect(() => {
    refreshReadyStories();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (selectedReleaseId) refreshBoard(selectedReleaseId);
    else setBoard(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedReleaseId]);

  async function handleCreateRelease() {
    if (currentUserId === null || !newName.trim() || !newVersion.trim()) return;
    setBusy(true);
    setError(null);
    try {
      const release = await api.releases.create({
        project_id: projectId,
        name: newName.trim(),
        version: newVersion.trim(),
        target_date: newTargetDate || undefined,
        created_by_id: currentUserId,
      });
      setReleases((prev) => [release, ...prev]);
      setSelectedReleaseId(release.id);
      setShowNewRelease(false);
      setNewName("");
      setNewVersion("");
      setNewTargetDate("");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to create the release.");
    } finally {
      setBusy(false);
    }
  }

  async function handleAddStory(storyId: string) {
    if (currentUserId === null || selectedRelease === null) return;
    setBusy(true);
    setError(null);
    try {
      await api.releases.addStory(selectedRelease.id, storyId, currentUserId);
      await refreshBoard(selectedRelease.id);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to add this story to the release.");
    } finally {
      setBusy(false);
    }
  }

  async function handleRemoveStory(storyId: string) {
    if (currentUserId === null || selectedRelease === null) return;
    setBusy(true);
    setError(null);
    try {
      await api.releases.removeStory(selectedRelease.id, storyId, currentUserId);
      await refreshBoard(selectedRelease.id);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to remove this story from the release.");
    } finally {
      setBusy(false);
    }
  }

  async function handleGenerateNotes() {
    if (currentUserId === null || selectedRelease === null) return;
    setBusy(true);
    setError(null);
    try {
      const updated = await api.releases.generateNotes(selectedRelease.id, currentUserId);
      setReleases((prev) => prev.map((r) => (r.id === updated.id ? updated : r)));
      setNotesDraft(updated.release_notes);
      setChecklist(await api.releases.approvalChecklist(updated.id));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to generate release notes.");
    } finally {
      setBusy(false);
    }
  }

  async function handleSaveNotes() {
    if (currentUserId === null || selectedRelease === null) return;
    setBusy(true);
    setError(null);
    try {
      const updated = await api.releases.update(selectedRelease.id, { release_notes: notesDraft, updated_by_id: currentUserId });
      setReleases((prev) => prev.map((r) => (r.id === updated.id ? updated : r)));
      setChecklist(await api.releases.approvalChecklist(updated.id));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to save release notes.");
    } finally {
      setBusy(false);
    }
  }

  async function handleApprove() {
    if (currentUserId === null || selectedRelease === null) return;
    setBusy(true);
    setError(null);
    try {
      const updated = await api.releases.approve(selectedRelease.id, currentUserId);
      setReleases((prev) => prev.map((r) => (r.id === updated.id ? updated : r)));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to approve this release — check the checklist below.");
    } finally {
      setBusy(false);
    }
  }

  const selectedStoryIds = new Set(board?.items.map((item) => item.story.id) ?? []);
  const availableStories = readyStories.filter((s) => !selectedStoryIds.has(s.id));

  return (
    <div className="flex flex-col gap-4">
      <Card>
        <CardHeader className="flex flex-row items-center justify-between gap-3">
          <div>
            <CardTitle className="text-base">Release</CardTitle>
            <CardDescription>Choose an existing release to plan, or create a new one.</CardDescription>
          </div>
          <Button size="sm" variant="outline" onClick={() => setShowNewRelease((v) => !v)}>
            <PlusCircle className="mr-1 h-3.5 w-3.5" /> New Release
          </Button>
        </CardHeader>
        <CardContent className="flex flex-col gap-3">
          {releases.length > 0 && (
            <Select value={selectedReleaseId ?? ""} onChange={(e) => setSelectedReleaseId(e.target.value || null)} className="w-72">
              <option value="">Select a release…</option>
              {releases.map((r) => (
                <option key={r.id} value={r.id}>
                  {r.name} — v{r.version} — {r.status}
                </option>
              ))}
            </Select>
          )}

          {showNewRelease && (
            <div className="grid grid-cols-2 gap-3 rounded-md border border-border p-3">
              <div>
                <label className="mb-1 block text-xs text-muted-foreground">Name</label>
                <Input value={newName} onChange={(e) => setNewName(e.target.value)} placeholder="September Release" className="text-xs" />
              </div>
              <div>
                <label className="mb-1 block text-xs text-muted-foreground">Version</label>
                <Input value={newVersion} onChange={(e) => setNewVersion(e.target.value)} placeholder="1.4.0" className="text-xs" />
              </div>
              <div>
                <label className="mb-1 block text-xs text-muted-foreground">Target date</label>
                <Input type="date" value={newTargetDate} onChange={(e) => setNewTargetDate(e.target.value)} className="text-xs" />
              </div>
              <div className="col-span-2 flex justify-end">
                <Button size="sm" onClick={handleCreateRelease} disabled={busy || !newName.trim() || !newVersion.trim() || currentUserId === null}>
                  Create Release
                </Button>
              </div>
            </div>
          )}
        </CardContent>
      </Card>

      {error && <p className="text-sm text-destructive">{error}</p>}

      {selectedRelease && (
        <>
          <Card>
            <CardHeader className="flex flex-row items-center justify-between gap-3">
              <div className="flex items-center gap-2">
                <CardTitle className="text-base">
                  {selectedRelease.name} — v{selectedRelease.version}
                </CardTitle>
                <Badge variant={releaseStatusVariant(selectedRelease.status)}>{selectedRelease.status}</Badge>
              </div>
              {selectedRelease.status === "DRAFT" && (
                <Button size="sm" onClick={handleApprove} disabled={busy || checklist.length > 0} title={checklist.length > 0 ? "Complete the approval checklist first" : "Approve this release"}>
                  <CheckCircle2 className="mr-1 h-3.5 w-3.5" /> Approve Release
                </Button>
              )}
            </CardHeader>
            <CardContent className="flex flex-col gap-3">
              <p className="text-xs text-muted-foreground">
                Target date: {selectedRelease.target_date ?? "TBD"}
                {selectedRelease.approved_at && <> · Approved {new Date(selectedRelease.approved_at).toLocaleString()}</>}
              </p>

              {/* Requirement 5 — release approval checklist */}
              <div className="rounded-md border border-border p-3">
                <div className="mb-2 flex items-center gap-1.5 text-xs font-medium">
                  <ShieldCheck className="h-3.5 w-3.5" /> Release approval checklist
                </div>
                {checklist.length === 0 ? (
                  <p className="text-xs text-emerald-600">All checklist items pass — ready to approve.</p>
                ) : (
                  <ul className="list-disc space-y-1 pl-4 text-xs text-destructive">
                    {checklist.map((item) => (
                      <li key={item}>{item}</li>
                    ))}
                  </ul>
                )}
              </div>
            </CardContent>
          </Card>

          <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
            <Card>
              <CardHeader>
                <CardTitle className="text-sm">Available release-ready stories</CardTitle>
                <CardDescription>Every story whose delivery lane has reached RELEASE_READY.</CardDescription>
              </CardHeader>
              <CardContent className="p-0">
                <Table>
                  <TableBody>
                    {availableStories.length === 0 ? (
                      <TableRow>
                        <TableCell className="text-xs text-muted-foreground">No release-ready stories available.</TableCell>
                      </TableRow>
                    ) : (
                      availableStories.map((s) => (
                        <TableRow key={s.id}>
                          <TableCell className="text-xs">
                            <div className="font-medium">{s.title}</div>
                            <div className="text-muted-foreground">{s.priority || "—"} · {s.story_points ?? "—"} pts</div>
                          </TableCell>
                          <TableCell className="text-right">
                            <Button size="sm" variant="outline" onClick={() => handleAddStory(s.id)} disabled={!editable || busy}>
                              Add
                            </Button>
                          </TableCell>
                        </TableRow>
                      ))
                    )}
                  </TableBody>
                </Table>
              </CardContent>
            </Card>

            <Card>
              <CardHeader>
                <CardTitle className="text-sm">Selected stories</CardTitle>
                <CardDescription>What ships in this release.</CardDescription>
              </CardHeader>
              <CardContent className="p-0">
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>Story</TableHead>
                      <TableHead className="text-right">Actions</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {loadingBoard ? (
                      <TableRow>
                        <TableCell colSpan={2}>
                          <Loader2 className="h-4 w-4 animate-spin" />
                        </TableCell>
                      </TableRow>
                    ) : !board || board.items.length === 0 ? (
                      <TableRow>
                        <TableCell colSpan={2} className="text-xs text-muted-foreground">
                          No stories selected yet.
                        </TableCell>
                      </TableRow>
                    ) : (
                      board.items.map((item) => (
                        <TableRow key={item.release_story.id}>
                          <TableCell className="max-w-[14rem] truncate text-xs font-medium">{item.story.title}</TableCell>
                          <TableCell className="text-right">
                            <Button
                              size="icon" variant="ghost" className="h-7 w-7"
                              onClick={() => handleRemoveStory(item.story.id)} disabled={!editable || busy}
                              title="Remove from release"
                            >
                              <Trash2 className="h-3.5 w-3.5" />
                            </Button>
                          </TableCell>
                        </TableRow>
                      ))
                    )}
                  </TableBody>
                </Table>
              </CardContent>
            </Card>
          </div>

          <Card>
            <CardHeader className="flex flex-row items-center justify-between gap-3">
              <div>
                <CardTitle className="text-sm">Release notes</CardTitle>
                <CardDescription>Generated from story summaries, PR summaries, test reports, and known risks.</CardDescription>
              </div>
              <div className="flex gap-2">
                <Button size="sm" variant="outline" onClick={handleGenerateNotes} disabled={!editable || busy || !board || board.items.length === 0}>
                  <FileText className="mr-1 h-3.5 w-3.5" /> Generate release notes
                </Button>
                {editable && (
                  <Button size="sm" onClick={handleSaveNotes} disabled={busy}>
                    <Save className="mr-1 h-3.5 w-3.5" /> Save
                  </Button>
                )}
              </div>
            </CardHeader>
            <CardContent>
              <Textarea
                value={notesDraft}
                onChange={(e) => setNotesDraft(e.target.value)}
                rows={14}
                className="font-mono text-xs"
                disabled={!editable}
                placeholder="Click “Generate release notes”, or write them by hand."
              />
            </CardContent>
          </Card>
        </>
      )}
    </div>
  );
}
