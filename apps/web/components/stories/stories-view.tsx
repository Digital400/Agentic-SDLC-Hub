"use client";

import { Fragment, useState } from "react";
import Link from "next/link";
import {
  CalendarPlus,
  ClipboardList,
  ExternalLink,
  Loader2,
  Pencil,
  PlusCircle,
  RefreshCw,
  UploadCloud,
} from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { EmptyState } from "@/components/ui/empty-state";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Textarea } from "@/components/ui/textarea";
import { api, ApiError, ApiSprint, ApiStory, ApiUser } from "@/lib/api";

const MODE_HELPER_TEXT: Record<"VERTICAL" | "HORIZONTAL", string> = {
  VERTICAL: "Creates end-to-end user value stories suitable for Scrum sprint delivery.",
  HORIZONTAL: "Creates technical layer stories such as frontend, backend, database, integration, infra, testing, or documentation.",
};

function jiraBadgeVariant(status: string): "success" | "gray" {
  return status === "Not Synced" ? "gray" : "success";
}

function laneBadgeVariant(status: string): "gray" | "info" | "success" {
  if (status === "No delivery lane yet") return "gray";
  if (status.startsWith("Release Ready")) return "success";
  return "info";
}

type EditDraft = {
  title: string;
  user_story: string;
  priority: string;
  dependencies: string;
  story_points: string;
  acceptance_criteria: string;
  definition_of_done: string;
};

function toDraft(story: ApiStory): EditDraft {
  return {
    title: story.title,
    user_story: story.user_story,
    priority: story.priority,
    dependencies: story.dependencies,
    story_points: story.story_points === null ? "" : String(story.story_points),
    acceptance_criteria: story.acceptance_criteria.join("\n"),
    definition_of_done: story.definition_of_done.join("\n"),
  };
}

/**
 * Story Crafting mode (VERTICAL/HORIZONTAL) is chosen here, at sync time —
 * not at Story Crafting draft time — because the mode only matters once a
 * backlog is turned into real, persisted Story rows (see
 * app/api/routes/stories.py's sync-from-backlog). The draft itself still
 * accepts a matching `story_crafting_mode` freeform input on the Story
 * Crafting node's own panel, for the agent's drafting framing.
 */
export function StoriesView({
  projectId,
  storyCraftingApproved,
  initialStories,
  initialSprints,
  users,
  currentUserId,
  jiraConnected,
}: {
  projectId: string;
  storyCraftingApproved: boolean;
  initialStories: ApiStory[];
  initialSprints: ApiSprint[];
  users: ApiUser[];
  currentUserId: string | null;
  jiraConnected: boolean;
}) {
  const [stories, setStories] = useState(initialStories);
  const [sprints, setSprints] = useState(initialSprints);
  const [mode, setMode] = useState<"VERTICAL" | "HORIZONTAL">("VERTICAL");
  const [syncing, setSyncing] = useState(false);
  const [busyStoryId, setBusyStoryId] = useState<string | null>(null);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [draft, setDraft] = useState<EditDraft | null>(null);
  const [addingSprintFor, setAddingSprintFor] = useState<string | null>(null);
  const [newSprintName, setNewSprintName] = useState("");
  const [error, setError] = useState<string | null>(null);

  function updateStory(updated: ApiStory) {
    setStories((prev) => prev.map((s) => (s.id === updated.id ? updated : s)));
  }

  async function handleSync() {
    if (currentUserId === null) return;
    setSyncing(true);
    setError(null);
    try {
      const result = await api.stories.syncFromBacklog(projectId, {
        story_type: mode,
        triggered_by_user_id: currentUserId,
      });
      if (result.created.length > 0) setStories((prev) => [...prev, ...result.created]);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to sync stories from the approved backlog.");
    } finally {
      setSyncing(false);
    }
  }

  async function handleAssignOwner(storyId: string, ownerUserId: string) {
    if (currentUserId === null || !ownerUserId) return;
    try {
      updateStory(await api.stories.assign(storyId, ownerUserId, currentUserId));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to assign an owner.");
    }
  }

  function startEdit(story: ApiStory) {
    setEditingId(story.id);
    setDraft(toDraft(story));
  }

  async function handleSaveEdit(storyId: string) {
    if (currentUserId === null || draft === null) return;
    setBusyStoryId(storyId);
    setError(null);
    try {
      const points = draft.story_points.trim();
      const updated = await api.stories.update(storyId, {
        title: draft.title.trim(),
        user_story: draft.user_story.trim(),
        priority: draft.priority.trim(),
        dependencies: draft.dependencies.trim(),
        story_points: points === "" ? undefined : Number(points),
        acceptance_criteria: draft.acceptance_criteria.split("\n").map((l) => l.trim()).filter(Boolean),
        definition_of_done: draft.definition_of_done.split("\n").map((l) => l.trim()).filter(Boolean),
        updated_by_id: currentUserId,
      });
      updateStory(updated);
      setEditingId(null);
      setDraft(null);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to save this story.");
    } finally {
      setBusyStoryId(null);
    }
  }

  async function handleSyncToJira(story: ApiStory) {
    if (currentUserId === null) return;
    setBusyStoryId(story.id);
    setError(null);
    try {
      await api.jira.push({
        project_id: projectId,
        triggered_by_user_id: currentUserId,
        selections: [{ source_type: "STORY", source_key: story.title }],
      });
      const refreshed = await api.stories.list(projectId);
      setStories(refreshed.items);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to sync this story to Jira.");
    } finally {
      setBusyStoryId(null);
    }
  }

  async function handleAddToExistingSprint(storyId: string, sprintId: string) {
    if (!sprintId || currentUserId === null) return;
    setBusyStoryId(storyId);
    setError(null);
    try {
      await api.sprints.addStory(sprintId, { story_id: storyId, actor_user_id: currentUserId });
      const refreshed = await api.stories.list(projectId);
      setStories(refreshed.items);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to add this story to the sprint.");
    } finally {
      setBusyStoryId(null);
    }
  }

  async function handleCreateSprintWithStory(storyId: string) {
    if (currentUserId === null || !newSprintName.trim()) return;
    setBusyStoryId(storyId);
    setError(null);
    try {
      const sprint = await api.sprints.create({ project_id: projectId, name: newSprintName.trim(), created_by_id: currentUserId });
      await api.sprints.addStory(sprint.id, { story_id: storyId, actor_user_id: currentUserId });
      setSprints((prev) => [sprint, ...prev]);
      const refreshed = await api.stories.list(projectId);
      setStories(refreshed.items);
      setAddingSprintFor(null);
      setNewSprintName("");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to create the sprint.");
    } finally {
      setBusyStoryId(null);
    }
  }

  async function handleCreateLane(storyId: string) {
    if (currentUserId === null) return;
    setBusyStoryId(storyId);
    setError(null);
    try {
      updateStory(await api.stories.createLane(storyId, currentUserId));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to create this story's delivery lane.");
    } finally {
      setBusyStoryId(null);
    }
  }

  return (
    <div className="flex flex-col gap-4">
      <Card>
        <CardHeader>
          <CardTitle className="text-base">Sync stories from the approved backlog</CardTitle>
          <CardDescription>
            Parses the approved Story Crafting document into individually trackable stories. Safe to run again
            later — already-synced stories (matched by title) are left untouched.
          </CardDescription>
        </CardHeader>
        <CardContent className="flex flex-col gap-2">
          <div className="flex flex-wrap items-center gap-3">
            <Select value={mode} onChange={(e) => setMode(e.target.value as "VERTICAL" | "HORIZONTAL")} className="w-56">
              <option value="VERTICAL">Vertical Stories</option>
              <option value="HORIZONTAL">Horizontal Stories</option>
            </Select>
            <Button onClick={handleSync} disabled={!storyCraftingApproved || syncing || currentUserId === null}>
              {syncing ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <RefreshCw className="mr-2 h-4 w-4" />}
              Sync from approved backlog
            </Button>
            {!storyCraftingApproved && (
              <p className="text-xs text-muted-foreground">Story Crafting must be approved before syncing.</p>
            )}
          </div>
          <p className="text-xs text-muted-foreground">{MODE_HELPER_TEXT[mode]}</p>
        </CardContent>
      </Card>

      {error && <p className="text-sm text-destructive">{error}</p>}

      {stories.length === 0 ? (
        <EmptyState icon={ClipboardList} title="No stories yet" description="Sync from the approved backlog to get started." />
      ) : (
        <Card>
          <CardContent className="overflow-x-auto p-0">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Story</TableHead>
                  <TableHead>Mode</TableHead>
                  <TableHead>Priority</TableHead>
                  <TableHead>Suggested Owner</TableHead>
                  <TableHead>Owner</TableHead>
                  <TableHead>Points</TableHead>
                  <TableHead>Jira</TableHead>
                  <TableHead>Lane</TableHead>
                  <TableHead>Dependencies</TableHead>
                  <TableHead className="text-right">Actions</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {stories.map((story) => {
                  const busy = busyStoryId === story.id;
                  const isEditing = editingId === story.id;
                  return (
                    <Fragment key={story.id}>
                      <TableRow>
                        <TableCell className="max-w-[16rem]">
                          <div className="truncate font-medium">{story.title}</div>
                          <div className="truncate text-xs text-muted-foreground">{story.epic || "—"}</div>
                        </TableCell>
                        <TableCell>
                          <Badge variant={story.story_type === "VERTICAL" ? "info" : "purple"}>
                            {story.story_type === "VERTICAL" ? "Vertical" : "Horizontal"}
                          </Badge>
                        </TableCell>
                        <TableCell className="text-xs">{story.priority || "—"}</TableCell>
                        <TableCell>
                          {story.suggested_owner_role ? (
                            <Badge variant="gray">{story.suggested_owner_role}</Badge>
                          ) : (
                            <span className="text-xs text-muted-foreground">—</span>
                          )}
                        </TableCell>
                        <TableCell>
                          <Select
                            value={story.owner_user_id ?? ""}
                            onChange={(e) => handleAssignOwner(story.id, e.target.value)}
                            className="w-36 text-xs"
                            aria-label="Assign owner"
                          >
                            <option value="">Unassigned</option>
                            {users.map((u) => (
                              <option key={u.id} value={u.id}>
                                {u.full_name}
                              </option>
                            ))}
                          </Select>
                        </TableCell>
                        <TableCell className="text-xs">{story.story_points ?? "—"}</TableCell>
                        <TableCell>
                          {story.jira_issue_url ? (
                            <a
                              href={story.jira_issue_url}
                              target="_blank"
                              rel="noreferrer"
                              className="inline-flex items-center gap-1 text-xs font-medium text-primary underline-offset-2 hover:underline"
                            >
                              {story.jira_issue_key ?? story.jira_status} <ExternalLink className="h-3 w-3" />
                            </a>
                          ) : (
                            <Badge variant={jiraBadgeVariant(story.jira_status)}>{story.jira_status}</Badge>
                          )}
                        </TableCell>
                        <TableCell>
                          <Badge variant={laneBadgeVariant(story.lane_status)} className="whitespace-nowrap">
                            {story.lane_status}
                          </Badge>
                        </TableCell>
                        <TableCell className="max-w-[10rem] truncate text-xs text-muted-foreground" title={story.dependencies}>
                          {story.dependencies || "None."}
                        </TableCell>
                        <TableCell>
                          <div className="flex items-center justify-end gap-1">
                            <Button
                              size="icon"
                              variant="ghost"
                              className="h-7 w-7"
                              title="Edit story"
                              onClick={() => (isEditing ? setEditingId(null) : startEdit(story))}
                            >
                              <Pencil className="h-3.5 w-3.5" />
                            </Button>
                            <Button
                              size="icon"
                              variant="ghost"
                              className="h-7 w-7"
                              title={jiraConnected ? "Sync to Jira" : "Connect Jira first"}
                              disabled={!jiraConnected || busy}
                              onClick={() => handleSyncToJira(story)}
                            >
                              {busy ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <UploadCloud className="h-3.5 w-3.5" />}
                            </Button>
                            <Button
                              size="icon"
                              variant="ghost"
                              className="h-7 w-7"
                              title="Add to sprint"
                              onClick={() => setAddingSprintFor(addingSprintFor === story.id ? null : story.id)}
                            >
                              <CalendarPlus className="h-3.5 w-3.5" />
                            </Button>
                            {story.lane_created_at ? (
                              <Link
                                href={`/projects/${projectId}/stories/${story.id}/lane`}
                                className="text-xs font-medium text-primary underline-offset-2 hover:underline"
                              >
                                Open lane
                              </Link>
                            ) : (
                              <Button
                                size="sm"
                                variant="outline"
                                className="h-7 px-2 text-xs"
                                onClick={() => handleCreateLane(story.id)}
                                disabled={busy || currentUserId === null}
                              >
                                {busy ? <Loader2 className="mr-1 h-3 w-3 animate-spin" /> : <PlusCircle className="mr-1 h-3 w-3" />}
                                Create lane
                              </Button>
                            )}
                          </div>
                        </TableCell>
                      </TableRow>

                      {addingSprintFor === story.id && (
                        <TableRow>
                          <TableCell colSpan={10} className="bg-muted/30">
                            <div className="flex flex-wrap items-center gap-2 py-1">
                              <span className="text-xs text-muted-foreground">Add to sprint:</span>
                              {sprints.length > 0 && (
                                <Select
                                  defaultValue=""
                                  onChange={(e) => handleAddToExistingSprint(story.id, e.target.value)}
                                  className="w-48 text-xs"
                                >
                                  <option value="">Choose an existing sprint…</option>
                                  {sprints.map((s) => (
                                    <option key={s.id} value={s.id}>
                                      {s.name}
                                    </option>
                                  ))}
                                </Select>
                              )}
                              <span className="text-xs text-muted-foreground">or</span>
                              <Input
                                value={newSprintName}
                                onChange={(e) => setNewSprintName(e.target.value)}
                                placeholder="New sprint name"
                                className="h-8 w-40 text-xs"
                              />
                              <Button
                                size="sm"
                                className="h-8 text-xs"
                                disabled={!newSprintName.trim() || busy}
                                onClick={() => handleCreateSprintWithStory(story.id)}
                              >
                                Create &amp; add
                              </Button>
                            </div>
                          </TableCell>
                        </TableRow>
                      )}

                      {isEditing && draft && (
                        <TableRow>
                          <TableCell colSpan={10} className="bg-muted/30">
                            <div className="grid grid-cols-2 gap-3 py-2">
                              <div className="col-span-2">
                                <label className="mb-1 block text-xs text-muted-foreground">Title</label>
                                <Input value={draft.title} onChange={(e) => setDraft({ ...draft, title: e.target.value })} className="text-xs" />
                              </div>
                              <div className="col-span-2">
                                <label className="mb-1 block text-xs text-muted-foreground">User Story</label>
                                <Textarea
                                  value={draft.user_story}
                                  onChange={(e) => setDraft({ ...draft, user_story: e.target.value })}
                                  rows={2}
                                  className="text-xs"
                                />
                              </div>
                              <div>
                                <label className="mb-1 block text-xs text-muted-foreground">Priority</label>
                                <Input value={draft.priority} onChange={(e) => setDraft({ ...draft, priority: e.target.value })} className="text-xs" />
                              </div>
                              <div>
                                <label className="mb-1 block text-xs text-muted-foreground">Story Points</label>
                                <Input
                                  type="number"
                                  min={0}
                                  value={draft.story_points}
                                  onChange={(e) => setDraft({ ...draft, story_points: e.target.value })}
                                  className="text-xs"
                                />
                              </div>
                              <div className="col-span-2">
                                <label className="mb-1 block text-xs text-muted-foreground">Dependencies</label>
                                <Input
                                  value={draft.dependencies}
                                  onChange={(e) => setDraft({ ...draft, dependencies: e.target.value })}
                                  className="text-xs"
                                />
                              </div>
                              <div>
                                <label className="mb-1 block text-xs text-muted-foreground">Acceptance Criteria (one per line)</label>
                                <Textarea
                                  value={draft.acceptance_criteria}
                                  onChange={(e) => setDraft({ ...draft, acceptance_criteria: e.target.value })}
                                  rows={3}
                                  className="text-xs"
                                />
                              </div>
                              <div>
                                <label className="mb-1 block text-xs text-muted-foreground">Definition of Done (one per line)</label>
                                <Textarea
                                  value={draft.definition_of_done}
                                  onChange={(e) => setDraft({ ...draft, definition_of_done: e.target.value })}
                                  rows={3}
                                  className="text-xs"
                                />
                              </div>
                              <div className="col-span-2 flex justify-end gap-2">
                                <Button size="sm" variant="outline" onClick={() => { setEditingId(null); setDraft(null); }}>
                                  Cancel
                                </Button>
                                <Button size="sm" onClick={() => handleSaveEdit(story.id)} disabled={busy}>
                                  {busy ? <Loader2 className="mr-1 h-3 w-3 animate-spin" /> : null}
                                  Save
                                </Button>
                              </div>
                            </div>
                          </TableCell>
                        </TableRow>
                      )}
                    </Fragment>
                  );
                })}
              </TableBody>
            </Table>
          </CardContent>
        </Card>
      )}
    </div>
  );
}
