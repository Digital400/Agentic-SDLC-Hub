"use client";

import { Fragment, useState } from "react";
import Link from "next/link";
import {
  CalendarPlus,
  CheckCircle2,
  CheckSquare,
  ClipboardList,
  Eye,
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
import { api, ApiError, ApiSprint, ApiStory, ApiStoryJiraPreview, ApiUser } from "@/lib/api";
import { cn } from "@/lib/utils";

const MODE_HELPER_TEXT: Record<"VERTICAL" | "HORIZONTAL", string> = {
  VERTICAL: "Creates end-to-end user value stories suitable for Scrum sprint delivery.",
  HORIZONTAL: "Creates technical layer stories such as frontend, backend, database, integration, infra, testing, or documentation.",
};

// This app's own Jira create-state machine (Story.jira_sync_status) —
// distinct from story.jira_status below, which is Jira's own live
// workflow status once synced.
const SYNC_STATUS_LABEL: Record<ApiStory["jira_sync_status"], string> = {
  NOT_SYNCED: "Not Synced",
  SYNC_PENDING: "Syncing…",
  SYNCED: "Synced",
  SYNC_FAILED: "Sync Failed",
};

function syncStatusBadgeVariant(status: ApiStory["jira_sync_status"]): "success" | "gray" | "warning" | "destructive" {
  if (status === "SYNCED") return "success";
  if (status === "SYNC_PENDING") return "warning";
  if (status === "SYNC_FAILED") return "destructive";
  return "gray";
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
  // Feedback for the sync action specifically — a sync that runs
  // successfully but finds/creates nothing must never look identical to
  // one that silently did nothing; see handleSync.
  const [syncNotice, setSyncNotice] = useState<{ tone: "warning" | "success"; text: string } | null>(null);
  // Requirement 2 — "User must preview Jira payload before creating/
  // updating Jira issue": a preview is fetched and shown inline before
  // any sync call can be confirmed, for exactly one story at a time.
  const [previewingId, setPreviewingId] = useState<string | null>(null);
  const [preview, setPreview] = useState<ApiStoryJiraPreview | null>(null);
  const [previewLoading, setPreviewLoading] = useState(false);
  // Requirement 4/5/rule — bulk preview + bulk sync, always in that
  // order: bulkPreviews is populated before any bulk-sync call is ever
  // reachable.
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  const [bulkPreviews, setBulkPreviews] = useState<ApiStoryJiraPreview[] | null>(null);
  const [bulkBusy, setBulkBusy] = useState(false);

  function updateStory(updated: ApiStory) {
    setStories((prev) => prev.map((s) => (s.id === updated.id ? updated : s)));
  }

  async function handleSync(syncMode: "VERTICAL" | "HORIZONTAL") {
    if (currentUserId === null) return;
    setMode(syncMode);
    setSyncing(true);
    setError(null);
    setSyncNotice(null);
    try {
      const result = await api.stories.syncFromBacklog(projectId, {
        story_type: syncMode,
        triggered_by_user_id: currentUserId,
      });
      // Always re-fetch the full list from the server rather than only
      // appending `created` — this component's `stories` state can be
      // stale relative to the database (e.g. the page was loaded before
      // these rows existed, or another sync ran elsewhere), and an
      // already_existed-only response has no row data to append at all.
      // Without this, "All N stories were already synced" could still
      // render an empty list right below it.
      const refreshed = await api.stories.list(projectId);
      setStories(refreshed.items);

      // Never let a sync that changed nothing look identical to one that
      // silently did nothing — the parsed_count===0 case in particular is
      // a real drafting/formatting problem worth surfacing, not a no-op.
      if (result.parsed_count === 0) {
        setSyncNotice({
          tone: "warning",
          text:
            "No stories were found in the approved Story Crafting document — it should contain one \"## Story: <title>\" " +
            "section per story. If it doesn't, re-draft Story Crafting (Workflow tab) before syncing again.",
        });
      } else if (result.created.length === 0) {
        setSyncNotice({
          tone: "success",
          text: `All ${result.already_existed} stor${result.already_existed === 1 ? "y was" : "ies were"} already synced — nothing new to add.`,
        });
      } else {
        setSyncNotice({
          tone: "success",
          text:
            `Synced ${result.created.length} new stor${result.created.length === 1 ? "y" : "ies"}` +
            (result.already_existed > 0 ? ` (${result.already_existed} already existed).` : "."),
        });
      }
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

  async function handleOpenPreview(story: ApiStory) {
    if (previewingId === story.id) {
      setPreviewingId(null);
      setPreview(null);
      return;
    }
    setPreviewingId(story.id);
    setPreview(null);
    setPreviewLoading(true);
    setError(null);
    try {
      setPreview(await api.jira.storyPreview(story.id));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to load the Jira preview for this story.");
      setPreviewingId(null);
    } finally {
      setPreviewLoading(false);
    }
  }

  async function handleConfirmSyncToJira(story: ApiStory) {
    if (currentUserId === null) return;
    setBusyStoryId(story.id);
    setError(null);
    try {
      await api.jira.syncStory(story.id, { triggered_by_user_id: currentUserId });
      const refreshed = await api.stories.list(projectId);
      setStories(refreshed.items);
      setPreviewingId(null);
      setPreview(null);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to sync this story to Jira.");
    } finally {
      setBusyStoryId(null);
    }
  }

  function toggleSelected(storyId: string) {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (next.has(storyId)) next.delete(storyId);
      else next.add(storyId);
      return next;
    });
  }

  async function handleOpenBulkPreview() {
    if (selectedIds.size === 0) return;
    setBulkBusy(true);
    setBulkPreviews(null);
    setError(null);
    try {
      const response = await api.jira.bulkPreviewStories(Array.from(selectedIds));
      setBulkPreviews(response.previews);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to load the bulk Jira preview.");
    } finally {
      setBulkBusy(false);
    }
  }

  async function handleConfirmBulkSync() {
    if (currentUserId === null || bulkPreviews === null) return;
    setBulkBusy(true);
    setError(null);
    try {
      await api.jira.bulkSyncStories({ story_ids: Array.from(selectedIds), triggered_by_user_id: currentUserId });
      const refreshed = await api.stories.list(projectId);
      setStories(refreshed.items);
      setBulkPreviews(null);
      setSelectedIds(new Set());
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to bulk sync the selected stories to Jira.");
    } finally {
      setBulkBusy(false);
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
          <div className="flex flex-wrap items-start gap-4">
            <div className="flex flex-col gap-1">
              <Button onClick={() => handleSync("VERTICAL")} disabled={!storyCraftingApproved || syncing || currentUserId === null}>
                {syncing && mode === "VERTICAL" ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <RefreshCw className="mr-2 h-4 w-4" />}
                Sync Vertical Stories
              </Button>
              <p className="text-xs text-muted-foreground">{MODE_HELPER_TEXT.VERTICAL}</p>
            </div>
            <div className="flex flex-col gap-1">
              <Button
                variant="outline"
                onClick={() => handleSync("HORIZONTAL")}
                disabled={!storyCraftingApproved || syncing || currentUserId === null}
              >
                {syncing && mode === "HORIZONTAL" ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <RefreshCw className="mr-2 h-4 w-4" />}
                Sync Horizontal Stories
              </Button>
              <p className="text-xs text-muted-foreground">{MODE_HELPER_TEXT.HORIZONTAL}</p>
            </div>
          </div>
          {!storyCraftingApproved && <p className="text-xs text-muted-foreground">Story Crafting must be approved before syncing.</p>}
          {syncNotice && (
            <p className={cn("text-sm", syncNotice.tone === "warning" ? "text-amber-600 dark:text-amber-500" : "text-muted-foreground")}>
              {syncNotice.text}
            </p>
          )}
        </CardContent>
      </Card>

      {error && <p className="text-sm text-destructive">{error}</p>}

      {stories.length > 0 && jiraConnected && (
        <Card>
          <CardHeader className="flex flex-row items-center justify-between gap-3 py-3">
            <div className="flex items-center gap-2 text-sm">
              <CheckSquare className="h-4 w-4 text-muted-foreground" />
              {selectedIds.size === 0 ? "Select stories to bulk sync to Jira." : `${selectedIds.size} selected.`}
            </div>
            <Button
              size="sm" variant="outline" onClick={handleOpenBulkPreview}
              disabled={selectedIds.size === 0 || bulkBusy || currentUserId === null}
            >
              {bulkBusy ? <Loader2 className="mr-1 h-3.5 w-3.5 animate-spin" /> : <Eye className="mr-1 h-3.5 w-3.5" />}
              Preview Bulk Sync ({selectedIds.size})
            </Button>
          </CardHeader>
          {bulkPreviews && (
            <CardContent className="flex flex-col gap-3 border-t pt-3">
              <ul className="flex flex-col gap-1 text-xs">
                {bulkPreviews.map((p) => {
                  const s = stories.find((st) => st.id === p.story_id);
                  const isValid = p.validation_errors.length === 0;
                  return (
                    <li key={p.story_id} className="flex items-center gap-2">
                      {isValid ? (
                        <Badge variant={p.already_linked ? "gray" : "success"}>{p.already_linked ? "Already synced" : "Ready"}</Badge>
                      ) : (
                        <Badge variant="destructive">Invalid</Badge>
                      )}
                      <span className="font-medium">{s?.title ?? p.summary}</span>
                      {!isValid && <span className="text-destructive">— {p.validation_errors.join(" ")}</span>}
                    </li>
                  );
                })}
              </ul>
              <div className="flex justify-end gap-2">
                <Button size="sm" variant="outline" onClick={() => setBulkPreviews(null)}>
                  Cancel
                </Button>
                <Button size="sm" onClick={handleConfirmBulkSync} disabled={bulkBusy || currentUserId === null}>
                  {bulkBusy ? <Loader2 className="mr-1 h-3.5 w-3.5 animate-spin" /> : <UploadCloud className="mr-1 h-3.5 w-3.5" />}
                  Confirm &amp; Sync Selected to Jira
                </Button>
              </div>
            </CardContent>
          )}
        </Card>
      )}

      {stories.length === 0 ? (
        <EmptyState icon={ClipboardList} title="No stories yet" description="Sync from the approved backlog to get started." />
      ) : (
        <Card>
          <CardContent className="overflow-x-auto p-0">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead className="w-8" />
                  <TableHead>Story</TableHead>
                  <TableHead>Mode</TableHead>
                  <TableHead>Priority</TableHead>
                  <TableHead>Suggested Owner</TableHead>
                  <TableHead>Owner</TableHead>
                  <TableHead>Points</TableHead>
                  <TableHead>Jira</TableHead>
                  <TableHead>Lane / Workspace</TableHead>
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
                        <TableCell>
                          <input
                            type="checkbox"
                            checked={selectedIds.has(story.id)}
                            onChange={() => toggleSelected(story.id)}
                            aria-label={`Select ${story.title} for bulk Jira sync`}
                          />
                        </TableCell>
                        <TableCell className="max-w-[16rem]">
                          <div className="flex items-center gap-1.5">
                            <span className="truncate font-medium">{story.title}</span>
                            {story.status === "DONE" && (
                              <Badge variant="success" className="shrink-0 gap-1">
                                <CheckCircle2 className="h-3 w-3" /> Done
                              </Badge>
                            )}
                          </div>
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
                          <div className="flex flex-col gap-0.5">
                            <Badge variant={syncStatusBadgeVariant(story.jira_sync_status)} className="w-fit">
                              {SYNC_STATUS_LABEL[story.jira_sync_status]}
                            </Badge>
                            {story.jira_issue_url && (
                              <a
                                href={story.jira_issue_url}
                                target="_blank"
                                rel="noreferrer"
                                className="inline-flex items-center gap-1 text-xs font-medium text-primary underline-offset-2 hover:underline"
                              >
                                {story.jira_issue_key} <ExternalLink className="h-3 w-3" />
                              </a>
                            )}
                          </div>
                        </TableCell>
                        <TableCell>
                          <div className="flex flex-col items-start gap-1">
                            <Badge variant={laneBadgeVariant(story.lane_status)} className="whitespace-nowrap">
                              {story.lane_status}
                            </Badge>
                            {story.lane_created_at ? (
                              <Link href={`/projects/${projectId}/stories/${story.id}/lane`}>
                                <Button size="sm" className="h-6 px-2 text-xs">
                                  Open story workspace
                                </Button>
                              </Link>
                            ) : (
                              <Button
                                size="sm"
                                variant="outline"
                                className="h-6 px-2 text-xs"
                                onClick={() => handleCreateLane(story.id)}
                                disabled={busy || currentUserId === null}
                              >
                                {busy ? <Loader2 className="mr-1 h-3 w-3 animate-spin" /> : <PlusCircle className="mr-1 h-3 w-3" />}
                                Create lane
                              </Button>
                            )}
                          </div>
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
                              title={jiraConnected ? "Preview Jira sync" : "Connect Jira first"}
                              disabled={!jiraConnected}
                              onClick={() => handleOpenPreview(story)}
                            >
                              <Eye className="h-3.5 w-3.5" />
                            </Button>
                            {story.jira_issue_url && (
                              <a
                                href={story.jira_issue_url}
                                target="_blank"
                                rel="noreferrer"
                                title="Open Jira issue"
                                className="inline-flex h-7 w-7 items-center justify-center rounded-md text-muted-foreground hover:bg-accent hover:text-accent-foreground"
                              >
                                <ExternalLink className="h-3.5 w-3.5" />
                              </a>
                            )}
                            <Button
                              size="icon"
                              variant="ghost"
                              className="h-7 w-7"
                              title="Add to sprint"
                              onClick={() => setAddingSprintFor(addingSprintFor === story.id ? null : story.id)}
                            >
                              <CalendarPlus className="h-3.5 w-3.5" />
                            </Button>
                          </div>
                        </TableCell>
                      </TableRow>

                      {previewingId === story.id && (
                        <TableRow>
                          <TableCell colSpan={11} className="bg-muted/30">
                            <div className="flex flex-col gap-3 py-2">
                              {previewLoading ? (
                                <p className="flex items-center gap-2 text-xs text-muted-foreground">
                                  <Loader2 className="h-3.5 w-3.5 animate-spin" /> Loading Jira preview…
                                </p>
                              ) : preview ? (
                                <>
                                  <div className="grid grid-cols-2 gap-3 text-xs">
                                    <div>
                                      <div className="mb-1 text-muted-foreground">Summary</div>
                                      <div className="font-medium">{preview.summary}</div>
                                    </div>
                                    <div>
                                      <div className="mb-1 text-muted-foreground">Priority</div>
                                      <div className="font-medium">{preview.priority ?? "—"}</div>
                                    </div>
                                    <div className="col-span-2">
                                      <div className="mb-1 text-muted-foreground">Description (sent to Jira as-is)</div>
                                      <pre className="whitespace-pre-wrap rounded-md border bg-background p-2 font-mono text-xs">{preview.description}</pre>
                                    </div>
                                    {preview.subtasks.length > 0 && (
                                      <div className="col-span-2">
                                        <div className="mb-1 text-muted-foreground">Subtasks (from Implementation tasks)</div>
                                        <ul className="list-disc space-y-1 pl-4">
                                          {preview.subtasks.map((s) => (
                                            <li key={s.implementation_task_id}>
                                              {s.title}
                                              {s.already_linked && (
                                                <span className="ml-1 text-muted-foreground">(already linked — {s.already_linked.jira_issue_key})</span>
                                              )}
                                              {s.validation_errors.length > 0 && (
                                                <span className="ml-1 text-destructive">— {s.validation_errors.join(" ")}</span>
                                              )}
                                            </li>
                                          ))}
                                        </ul>
                                      </div>
                                    )}
                                  </div>
                                  {preview.already_linked ? (
                                    <p className="text-xs text-muted-foreground">
                                      Already linked to Jira issue {preview.already_linked.jira_issue_key} — syncing again will not
                                      create a duplicate or edit the existing issue.
                                    </p>
                                  ) : preview.validation_errors.length > 0 ? (
                                    <p className="text-xs text-destructive">{preview.validation_errors.join(" ")}</p>
                                  ) : null}
                                  <div className="flex justify-end gap-2">
                                    <Button size="sm" variant="outline" onClick={() => { setPreviewingId(null); setPreview(null); }}>
                                      Cancel
                                    </Button>
                                    <Button
                                      size="sm"
                                      onClick={() => handleConfirmSyncToJira(story)}
                                      disabled={busy || currentUserId === null || preview.validation_errors.length > 0 || !!preview.already_linked}
                                    >
                                      {busy ? <Loader2 className="mr-1 h-3 w-3 animate-spin" /> : <UploadCloud className="mr-1 h-3 w-3" />}
                                      {preview.already_linked ? "Already synced" : "Confirm & sync to Jira"}
                                    </Button>
                                  </div>
                                </>
                              ) : null}
                            </div>
                          </TableCell>
                        </TableRow>
                      )}

                      {addingSprintFor === story.id && (
                        <TableRow>
                          <TableCell colSpan={11} className="bg-muted/30">
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
                          <TableCell colSpan={11} className="bg-muted/30">
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
