"use client";

import { useEffect, useState } from "react";
import { CheckCircle2, Loader2, PlayCircle, PlusCircle, Save, Trash2 } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Textarea } from "@/components/ui/textarea";
import { api, ApiError, ApiSprint, ApiSprintBoard, ApiStory, ApiUser } from "@/lib/api";

function sprintStatusVariant(status: ApiSprint["status"]): "gray" | "info" | "success" | "destructive" {
  if (status === "PLANNED") return "gray";
  if (status === "ACTIVE") return "info";
  if (status === "COMPLETED") return "success";
  return "destructive";
}

export function SprintPlanningView({
  projectId,
  initialSprints,
  initialStories,
  users,
  currentUserId,
}: {
  projectId: string;
  initialSprints: ApiSprint[];
  initialStories: ApiStory[];
  users: ApiUser[];
  currentUserId: string | null;
}) {
  const [sprints, setSprints] = useState(initialSprints);
  const [stories, setStories] = useState(initialStories);
  const [selectedSprintId, setSelectedSprintId] = useState<string | null>(initialSprints[0]?.id ?? null);
  const [board, setBoard] = useState<ApiSprintBoard | null>(null);
  const [loadingBoard, setLoadingBoard] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const [showNewSprint, setShowNewSprint] = useState(initialSprints.length === 0);
  const [newName, setNewName] = useState("");
  const [newGoal, setNewGoal] = useState("");
  const [newStart, setNewStart] = useState("");
  const [newEnd, setNewEnd] = useState("");
  const [newCapacity, setNewCapacity] = useState("");

  const [goalDraft, setGoalDraft] = useState("");
  const [startDraft, setStartDraft] = useState("");
  const [endDraft, setEndDraft] = useState("");
  const [capacityDraft, setCapacityDraft] = useState("");

  const selectedSprint = sprints.find((s) => s.id === selectedSprintId) ?? null;
  const editable = selectedSprint !== null && selectedSprint.status !== "COMPLETED" && selectedSprint.status !== "CANCELLED";

  async function refreshBoard(sprintId: string) {
    setLoadingBoard(true);
    try {
      const result = await api.sprints.board(sprintId);
      setBoard(result);
      setGoalDraft(result.sprint.goal);
      setStartDraft(result.sprint.start_date ?? "");
      setEndDraft(result.sprint.end_date ?? "");
      setCapacityDraft(result.sprint.capacity_points === null ? "" : String(result.sprint.capacity_points));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to load the sprint board.");
    } finally {
      setLoadingBoard(false);
    }
  }

  useEffect(() => {
    if (selectedSprintId) refreshBoard(selectedSprintId);
    else setBoard(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedSprintId]);

  async function handleCreateSprint() {
    if (currentUserId === null || !newName.trim()) return;
    setBusy(true);
    setError(null);
    try {
      const sprint = await api.sprints.create({
        project_id: projectId,
        name: newName.trim(),
        goal: newGoal.trim() || undefined,
        start_date: newStart || undefined,
        end_date: newEnd || undefined,
        capacity_points: newCapacity.trim() ? Number(newCapacity) : undefined,
        created_by_id: currentUserId,
      });
      setSprints((prev) => [sprint, ...prev]);
      setSelectedSprintId(sprint.id);
      setShowNewSprint(false);
      setNewName("");
      setNewGoal("");
      setNewStart("");
      setNewEnd("");
      setNewCapacity("");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to create the sprint.");
    } finally {
      setBusy(false);
    }
  }

  async function handleSaveSprintDetails() {
    if (currentUserId === null || selectedSprint === null) return;
    setBusy(true);
    setError(null);
    try {
      const updated = await api.sprints.update(selectedSprint.id, {
        goal: goalDraft,
        start_date: startDraft || undefined,
        end_date: endDraft || undefined,
        capacity_points: capacityDraft.trim() ? Number(capacityDraft) : undefined,
        updated_by_id: currentUserId,
      });
      setSprints((prev) => prev.map((s) => (s.id === updated.id ? updated : s)));
      await refreshBoard(updated.id);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to save sprint details.");
    } finally {
      setBusy(false);
    }
  }

  async function handleAddStory(storyId: string) {
    if (currentUserId === null || selectedSprint === null) return;
    setBusy(true);
    setError(null);
    try {
      await api.sprints.addStory(selectedSprint.id, { story_id: storyId, actor_user_id: currentUserId });
      const refreshedStories = await api.stories.list(projectId);
      setStories(refreshedStories.items);
      await refreshBoard(selectedSprint.id);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to add this story to the sprint.");
    } finally {
      setBusy(false);
    }
  }

  async function handleRemoveStory(storyId: string) {
    if (currentUserId === null || selectedSprint === null) return;
    setBusy(true);
    setError(null);
    try {
      await api.sprints.removeStory(selectedSprint.id, storyId, currentUserId);
      const refreshedStories = await api.stories.list(projectId);
      setStories(refreshedStories.items);
      await refreshBoard(selectedSprint.id);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to remove this story from the sprint.");
    } finally {
      setBusy(false);
    }
  }

  async function handleUpdatePlannedPoints(storyId: string, points: string) {
    if (currentUserId === null || selectedSprint === null) return;
    try {
      await api.sprints.updateStory(selectedSprint.id, storyId, {
        planned_points: points.trim() ? Number(points) : undefined,
        actor_user_id: currentUserId,
      });
      await refreshBoard(selectedSprint.id);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to update planned points.");
    }
  }

  async function handleAssignSprintOwner(storyId: string, ownerId: string) {
    if (currentUserId === null || selectedSprint === null || !ownerId) return;
    try {
      await api.sprints.updateStory(selectedSprint.id, storyId, { assigned_owner_id: ownerId, actor_user_id: currentUserId });
      await refreshBoard(selectedSprint.id);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to assign an owner.");
    }
  }

  async function handleStartSprint() {
    if (currentUserId === null || selectedSprint === null) return;
    setBusy(true);
    setError(null);
    try {
      const updated = await api.sprints.start(selectedSprint.id, currentUserId);
      setSprints((prev) => prev.map((s) => (s.id === updated.id ? updated : s)));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to start the sprint.");
    } finally {
      setBusy(false);
    }
  }

  async function handleCompleteSprint() {
    if (currentUserId === null || selectedSprint === null) return;
    setBusy(true);
    setError(null);
    try {
      const updated = await api.sprints.complete(selectedSprint.id, currentUserId);
      setSprints((prev) => prev.map((s) => (s.id === updated.id ? updated : s)));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to complete the sprint.");
    } finally {
      setBusy(false);
    }
  }

  const plannedStoryIds = new Set(board?.items.map((item) => item.story.id) ?? []);
  const availableStories = stories.filter((s) => s.sprint_id === null && s.status !== "DONE" && !plannedStoryIds.has(s.id));

  return (
    <div className="flex flex-col gap-4">
      <Card>
        <CardHeader className="flex flex-row items-center justify-between gap-3">
          <div>
            <CardTitle className="text-base">Sprint</CardTitle>
            <CardDescription>Choose an existing sprint to plan, or create a new one.</CardDescription>
          </div>
          <Button size="sm" variant="outline" onClick={() => setShowNewSprint((v) => !v)}>
            <PlusCircle className="mr-1 h-3.5 w-3.5" /> New Sprint
          </Button>
        </CardHeader>
        <CardContent className="flex flex-col gap-3">
          {sprints.length > 0 && (
            <Select value={selectedSprintId ?? ""} onChange={(e) => setSelectedSprintId(e.target.value || null)} className="w-64">
              <option value="">Select a sprint…</option>
              {sprints.map((s) => (
                <option key={s.id} value={s.id}>
                  {s.name} — {s.status}
                </option>
              ))}
            </Select>
          )}

          {showNewSprint && (
            <div className="grid grid-cols-2 gap-3 rounded-md border border-border p-3">
              <div className="col-span-2">
                <label className="mb-1 block text-xs text-muted-foreground">Name</label>
                <Input value={newName} onChange={(e) => setNewName(e.target.value)} placeholder="Sprint 12" className="text-xs" />
              </div>
              <div className="col-span-2">
                <label className="mb-1 block text-xs text-muted-foreground">Sprint goal</label>
                <Textarea value={newGoal} onChange={(e) => setNewGoal(e.target.value)} rows={2} className="text-xs" />
              </div>
              <div>
                <label className="mb-1 block text-xs text-muted-foreground">Start date</label>
                <Input type="date" value={newStart} onChange={(e) => setNewStart(e.target.value)} className="text-xs" />
              </div>
              <div>
                <label className="mb-1 block text-xs text-muted-foreground">End date</label>
                <Input type="date" value={newEnd} onChange={(e) => setNewEnd(e.target.value)} className="text-xs" />
              </div>
              <div>
                <label className="mb-1 block text-xs text-muted-foreground">Capacity (points)</label>
                <Input type="number" min={0} value={newCapacity} onChange={(e) => setNewCapacity(e.target.value)} className="text-xs" />
              </div>
              <div className="col-span-2 flex justify-end">
                <Button size="sm" onClick={handleCreateSprint} disabled={busy || !newName.trim() || currentUserId === null}>
                  Create Sprint
                </Button>
              </div>
            </div>
          )}
        </CardContent>
      </Card>

      {error && <p className="text-sm text-destructive">{error}</p>}

      {selectedSprint && (
        <>
          <Card>
            <CardHeader className="flex flex-row items-center justify-between gap-3">
              <div className="flex items-center gap-2">
                <CardTitle className="text-base">{selectedSprint.name}</CardTitle>
                <Badge variant={sprintStatusVariant(selectedSprint.status)}>{selectedSprint.status}</Badge>
              </div>
              <div className="flex gap-2">
                {selectedSprint.status === "PLANNED" && (
                  <Button size="sm" onClick={handleStartSprint} disabled={busy}>
                    <PlayCircle className="mr-1 h-3.5 w-3.5" /> Start Sprint
                  </Button>
                )}
                {selectedSprint.status === "ACTIVE" && (
                  <Button size="sm" variant="outline" onClick={handleCompleteSprint} disabled={busy}>
                    <CheckCircle2 className="mr-1 h-3.5 w-3.5" /> Complete Sprint
                  </Button>
                )}
              </div>
            </CardHeader>
            <CardContent className="grid grid-cols-2 gap-3">
              <div className="col-span-2">
                <label className="mb-1 block text-xs text-muted-foreground">Sprint goal</label>
                <Textarea value={goalDraft} onChange={(e) => setGoalDraft(e.target.value)} rows={2} className="text-xs" disabled={!editable} />
              </div>
              <div>
                <label className="mb-1 block text-xs text-muted-foreground">Start date</label>
                <Input type="date" value={startDraft} onChange={(e) => setStartDraft(e.target.value)} className="text-xs" disabled={!editable} />
              </div>
              <div>
                <label className="mb-1 block text-xs text-muted-foreground">End date</label>
                <Input type="date" value={endDraft} onChange={(e) => setEndDraft(e.target.value)} className="text-xs" disabled={!editable} />
              </div>
              <div>
                <label className="mb-1 block text-xs text-muted-foreground">Capacity (points)</label>
                <Input
                  type="number" min={0} value={capacityDraft} onChange={(e) => setCapacityDraft(e.target.value)}
                  className="text-xs" disabled={!editable}
                />
              </div>
              <div className="flex items-end">
                <p className="text-xs text-muted-foreground">
                  Planned: <span className={board && board.over_capacity ? "font-semibold text-destructive" : "font-semibold"}>{board?.planned_points_total ?? 0}</span>
                  {board?.capacity_points != null && <> / {board.capacity_points}</>} points
                  {board?.over_capacity && <span className="ml-1 text-destructive">(over capacity)</span>}
                </p>
              </div>
              {editable && (
                <div className="col-span-2 flex justify-end">
                  <Button size="sm" variant="outline" onClick={handleSaveSprintDetails} disabled={busy}>
                    <Save className="mr-1 h-3.5 w-3.5" /> Save details
                  </Button>
                </div>
              )}
            </CardContent>
          </Card>

          <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
            <Card>
              <CardHeader>
                <CardTitle className="text-sm">Available approved stories</CardTitle>
                <CardDescription>Not yet planned into any sprint.</CardDescription>
              </CardHeader>
              <CardContent className="p-0">
                <Table>
                  <TableBody>
                    {availableStories.length === 0 ? (
                      <TableRow>
                        <TableCell className="text-xs text-muted-foreground">No available stories.</TableCell>
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
                <CardTitle className="text-sm">Selected sprint stories</CardTitle>
                <CardDescription>Owner assignment and points are scoped to this sprint.</CardDescription>
              </CardHeader>
              <CardContent className="p-0">
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>Story</TableHead>
                      <TableHead>Owner</TableHead>
                      <TableHead>Points</TableHead>
                      <TableHead className="text-right">Actions</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {loadingBoard ? (
                      <TableRow>
                        <TableCell colSpan={4}>
                          <Loader2 className="h-4 w-4 animate-spin" />
                        </TableCell>
                      </TableRow>
                    ) : !board || board.items.length === 0 ? (
                      <TableRow>
                        <TableCell colSpan={4} className="text-xs text-muted-foreground">
                          No stories planned yet.
                        </TableCell>
                      </TableRow>
                    ) : (
                      board.items.map((item) => (
                        <TableRow key={item.sprint_story.id}>
                          <TableCell className="max-w-[10rem] truncate text-xs font-medium">{item.story.title}</TableCell>
                          <TableCell>
                            <Select
                              value={item.sprint_story.assigned_owner_id ?? ""}
                              onChange={(e) => handleAssignSprintOwner(item.story.id, e.target.value)}
                              className="w-32 text-xs"
                              disabled={!editable}
                            >
                              <option value="">Unassigned</option>
                              {users.map((u) => (
                                <option key={u.id} value={u.id}>
                                  {u.full_name}
                                </option>
                              ))}
                            </Select>
                          </TableCell>
                          <TableCell>
                            <Input
                              type="number" min={0}
                              defaultValue={item.sprint_story.planned_points ?? ""}
                              onBlur={(e) => handleUpdatePlannedPoints(item.story.id, e.target.value)}
                              className="w-16 text-xs" disabled={!editable}
                            />
                          </TableCell>
                          <TableCell className="text-right">
                            <Button
                              size="icon" variant="ghost" className="h-7 w-7"
                              onClick={() => handleRemoveStory(item.story.id)} disabled={!editable || busy}
                              title="Remove from sprint"
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
        </>
      )}
    </div>
  );
}
