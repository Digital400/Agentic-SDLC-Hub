"use client";

import { useEffect, useRef, useState } from "react";
import { Download, Eye, ExternalLink, FileCode, FileJson, FileSpreadsheet, FileText, Github, Pencil, Save, SendHorizontal, GitBranch } from "lucide-react";

import { AgentActionsPanel, type AgentRunOutcome } from "@/components/documents/agent-actions-panel";
import { ClarificationPanel } from "@/components/documents/clarification-panel";
import { CommentsPanel } from "@/components/documents/comments-panel";
import { GithubPrPreviewPanel } from "@/components/documents/github-pr-preview-panel";
import { JiraExportPreviewPanel } from "@/components/documents/jira-export-preview-panel";
import { MarkdownPreview } from "@/components/documents/markdown-preview";
import { SectionNav } from "@/components/documents/section-nav";
import { ArtifactStatusBadge } from "@/components/status-badge";
import { Button, buttonVariants } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import { formatRelativeTime } from "@/lib/format";
import { API_URL, api, ApiError } from "@/lib/api";
import { hasRealSections, isClarificationRequest, joinSectionsIntoMarkdown, splitMarkdownIntoSections } from "@/lib/markdown-sections";
import { toArtifactVersionSummary, toGithubPrPreview, toJiraExportPreview } from "@/lib/mappers";
import type { ArtifactDocument, ArtifactSection, ArtifactStatus, GithubPrPreview, JiraExportPreview } from "@/lib/types";

// Story Crafting's output_artifact_type — see
// apps/api/app/api/routes/artifacts.py's STORY_BACKLOG_ARTIFACT_TYPE.
const STORY_BACKLOG_ARTIFACT_TYPE = "story_backlog";
// Implementation's output_artifact_type — see workflows/sdlc-workflow.json.
// Gates the "Preview GitHub PR" button the same way STORY_BACKLOG_ARTIFACT_TYPE
// gates the Jira preview button above.
const CODE_CHANGE_ARTIFACT_TYPE = "code_change";

export interface ReviewerOption {
  id: string;
  name: string;
}

export function ArtifactEditor({
  document: doc,
  createdById,
  reviewers = [],
  hasOpenReview = false,
}: {
  document: ArtifactDocument;
  createdById: string | null;
  reviewers?: ReviewerOption[];
  /** Whether this artifact already has a PENDING review open — see
   * app/documents/[artifactId]/page.tsx for why this can be false even
   * while status is READY_FOR_REVIEW. */
  hasOpenReview?: boolean;
}) {
  const [sections, setSections] = useState<ArtifactSection[]>(doc.sections);
  const [docHasRealSections, setDocHasRealSections] = useState(doc.hasRealSections);
  const [needsClarification, setNeedsClarification] = useState(doc.needsClarification);
  const [dirty, setDirty] = useState(false);
  const [status, setStatus] = useState<ArtifactStatus>(doc.status);
  const [versions, setVersions] = useState(doc.versions);
  const [currentVersionNumber, setCurrentVersionNumber] = useState(doc.currentVersionNumber);
  const [activeSectionId, setActiveSectionId] = useState(doc.sections[0]?.id ?? "");
  const [viewMode, setViewMode] = useState<"edit" | "preview">("edit");
  const [savedAt, setSavedAt] = useState<string | null>(null);
  const [showVersionForm, setShowVersionForm] = useState(false);
  const [showExportMenu, setShowExportMenu] = useState(false);
  const [showReviewForm, setShowReviewForm] = useState(false);
  const [showJiraPreview, setShowJiraPreview] = useState(false);
  const [jiraPreview, setJiraPreview] = useState<JiraExportPreview | null>(null);
  const [jiraPreviewLoading, setJiraPreviewLoading] = useState(false);
  const [jiraPreviewError, setJiraPreviewError] = useState<string | null>(null);
  const [showGithubPreview, setShowGithubPreview] = useState(false);
  const [githubPreview, setGithubPreview] = useState<GithubPrPreview | null>(null);
  const [githubPreviewLoading, setGithubPreviewLoading] = useState(false);
  const [githubPreviewError, setGithubPreviewError] = useState<string | null>(null);
  const [openReview, setOpenReview] = useState(hasOpenReview);
  const [reviewerId, setReviewerId] = useState(
    reviewers.find((r) => r.id !== createdById)?.id ?? reviewers[0]?.id ?? ""
  );
  const [changeSummaryDraft, setChangeSummaryDraft] = useState("");
  const [banner, setBanner] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const bannerTimeout = useRef<ReturnType<typeof setTimeout>>();

  useEffect(() => () => clearTimeout(bannerTimeout.current), []);

  function flash(message: string) {
    setBanner(message);
    clearTimeout(bannerTimeout.current);
    bannerTimeout.current = setTimeout(() => setBanner(null), 4000);
  }

  const editable = status === "DRAFT";
  const activeSection = sections.find((s) => s.id === activeSectionId) ?? null;

  function updateSection(id: string, contentMarkdown: string) {
    setSections((prev) => prev.map((s) => (s.id === id ? { ...s, contentMarkdown } : s)));
    setDirty(true);
  }

  async function handleSaveDraft() {
    if (createdById === null) {
      flash("No users exist yet to attribute this edit to.");
      return;
    }
    setBusy(true);
    try {
      await api.artifacts.updateContent(doc.id, {
        content_markdown: joinSectionsIntoMarkdown(sections),
        edited_by_id: createdById,
      });
      setDirty(false);
      setSavedAt(new Date().toISOString());
      flash("Draft saved.");
    } catch (err) {
      flash(err instanceof ApiError ? `Failed to save: ${err.message}` : "Failed to save draft.");
    } finally {
      setBusy(false);
    }
  }

  async function handleConfirmNewVersion() {
    if (createdById === null) {
      flash("No users exist yet to attribute this version to.");
      return;
    }
    setBusy(true);
    try {
      const version = await api.artifacts.createVersion(doc.id, {
        content_markdown: joinSectionsIntoMarkdown(sections),
        change_summary: changeSummaryDraft.trim() || undefined,
        created_by_id: createdById,
      });
      setVersions((prev) => [...prev, toArtifactVersionSummary(version)]);
      setCurrentVersionNumber(version.version_number);
      setStatus("DRAFT");
      setOpenReview(false);
      setDirty(false);
      setShowVersionForm(false);
      setChangeSummaryDraft("");
      flash(`Version ${version.version_number} created.`);
    } catch (err) {
      flash(err instanceof ApiError ? `Failed to create version: ${err.message}` : "Failed to create version.");
    } finally {
      setBusy(false);
    }
  }

  async function handleAgentApplied(outcome: AgentRunOutcome) {
    // The agent run's own save-to-artifact call already changed the
    // artifact server-side (new/updated version, possibly a new status) —
    // re-fetch this artifact's current state rather than guessing at it
    // locally, same as every other mutating action in this component.
    try {
      const [freshArtifact, freshVersions] = await Promise.all([api.artifacts.get(doc.id), api.artifacts.versions(doc.id)]);
      const currentVersion = freshVersions.find((v) => v.id === freshArtifact.current_version_id);
      setStatus(outcome.artifactStatus as ArtifactStatus);
      setCurrentVersionNumber(freshArtifact.current_version_number ?? currentVersionNumber);
      setVersions(freshVersions.map(toArtifactVersionSummary));
      const freshMarkdown = currentVersion?.content_markdown ?? "";
      setSections(splitMarkdownIntoSections(freshMarkdown));
      setDocHasRealSections(hasRealSections(freshMarkdown));
      setNeedsClarification(isClarificationRequest(freshMarkdown));
      setDirty(false);
      // A "validate" run can push status straight to READY_FOR_REVIEW —
      // that's a new version needing its own review round, not a
      // continuation of whatever was open (if anything) before this run.
      setOpenReview(false);
      flash("Agent output applied to this document.");
    } catch (err) {
      flash(err instanceof ApiError ? `Applied, but failed to refresh the view: ${err.message}` : "Applied, but failed to refresh the view.");
    }
  }

  async function handleOpenJiraPreview() {
    setShowJiraPreview(true);
    setJiraPreviewLoading(true);
    setJiraPreviewError(null);
    try {
      const preview = await api.projects.previewJiraExport(doc.projectId);
      setJiraPreview(toJiraExportPreview(preview));
    } catch (err) {
      setJiraPreviewError(err instanceof ApiError ? err.message : "Failed to build the Jira export preview.");
    } finally {
      setJiraPreviewLoading(false);
    }
  }

  async function handleOpenGithubPreview() {
    setShowGithubPreview(true);
    setGithubPreviewLoading(true);
    setGithubPreviewError(null);
    try {
      const preview = await api.artifacts.githubPrPreview(doc.id);
      setGithubPreview(toGithubPrPreview(preview));
    } catch (err) {
      setGithubPreviewError(err instanceof ApiError ? err.message : "Failed to build the GitHub PR preview.");
    } finally {
      setGithubPreviewLoading(false);
    }
  }

  // Submitting an artifact (status -> READY_FOR_REVIEW) and opening the
  // review round that actually shows up on /reviews are two separate API
  // calls (see apps/api/app/api/routes/{artifacts,reviews}.py) — done here
  // as one action so a document can never end up "ready for review" with
  // no review anyone can act on. See app/documents/[artifactId]/page.tsx's
  // hasOpenReview for the one case this button alone can't have caused
  // (an artifact left in that state before this pairing existed).
  async function handleSendForReview() {
    if (!reviewerId) {
      flash("No users exist yet to assign as reviewer.");
      return;
    }
    setBusy(true);
    try {
      if (status === "DRAFT" || status === "NEEDS_CHANGES") {
        const artifact = await api.artifacts.submitForReview(doc.id);
        setStatus(artifact.status);
      }
      await api.reviews.create({ artifact_id: doc.id, reviewer_id: reviewerId });
      setOpenReview(true);
      setShowReviewForm(false);
      flash("Sent for review.");
    } catch (err) {
      flash(err instanceof ApiError ? `Failed to send for review: ${err.message}` : "Failed to send for review.");
    } finally {
      setBusy(false);
    }
  }

  return (
    // Fixed viewport height + 3 side-by-side columns only makes sense once
    // there's room for all three — below lg the section nav, content, and
    // agent/comments rail stack instead and the page scrolls normally.
    <div className="flex flex-col lg:h-[calc(100vh-8.5rem)]">
      {/* Header + toolbar */}
      <div className="mb-4 flex flex-col gap-3 border-b border-border pb-4">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <div className="flex items-center gap-2">
              <h1 className="text-lg font-semibold">{doc.title}</h1>
              <ArtifactStatusBadge status={status} />
            </div>
            <p className="mt-1 text-xs text-muted-foreground">
              {doc.projectName} · {doc.workflowStageName} · v{currentVersionNumber}
              {savedAt ? ` · Saved ${formatRelativeTime(savedAt)}` : ""}
              {dirty ? " · Unsaved changes" : ""}
            </p>
          </div>

          <div className="flex flex-wrap items-center gap-2">
            <div className="flex rounded-md border border-border p-0.5">
              <button
                type="button"
                onClick={() => setViewMode("edit")}
                className={`flex items-center gap-1.5 rounded px-2.5 py-1 text-xs font-medium transition-colors ${
                  viewMode === "edit" ? "bg-secondary text-secondary-foreground" : "text-muted-foreground"
                }`}
              >
                <Pencil className="h-3.5 w-3.5" />
                Edit
              </button>
              <button
                type="button"
                onClick={() => setViewMode("preview")}
                className={`flex items-center gap-1.5 rounded px-2.5 py-1 text-xs font-medium transition-colors ${
                  viewMode === "preview" ? "bg-secondary text-secondary-foreground" : "text-muted-foreground"
                }`}
              >
                <Eye className="h-3.5 w-3.5" />
                Preview
              </button>
            </div>
            <div className="mx-1 h-5 w-px bg-border" />
            <Button variant="outline" size="sm" onClick={handleSaveDraft} disabled={!editable || !dirty || busy}>
              <Save className="h-3.5 w-3.5" />
              Save draft
            </Button>
            <Button variant="outline" size="sm" onClick={() => setShowVersionForm((v) => !v)} disabled={busy}>
              <GitBranch className="h-3.5 w-3.5" />
              Create new version
            </Button>
            <Button
              size="sm"
              onClick={() => setShowReviewForm((v) => !v)}
              disabled={
                dirty ||
                busy ||
                !(status === "DRAFT" || status === "NEEDS_CHANGES" || (status === "READY_FOR_REVIEW" && !openReview))
              }
              title={dirty ? "Save your draft first" : undefined}
            >
              <SendHorizontal className="h-3.5 w-3.5" />
              {status === "READY_FOR_REVIEW" && !openReview ? "Open review" : "Send for review"}
            </Button>
            <div className="mx-1 h-5 w-px bg-border" />
            {doc.artifactType === STORY_BACKLOG_ARTIFACT_TYPE && status === "APPROVED" ? (
              <div className="relative">
                <Button variant="outline" size="sm" onClick={() => setShowExportMenu((v) => !v)}>
                  <Download className="h-3.5 w-3.5" />
                  Export Stories
                </Button>
                {showExportMenu ? (
                  <div className="absolute right-0 top-full z-10 mt-1 flex flex-col gap-1 rounded-md border border-border bg-card p-1.5 shadow-md">
                    <a
                      href={`${API_URL}/artifacts/${doc.id}/export/stories?format=markdown`}
                      className={buttonVariants({ variant: "ghost", size: "sm", className: "justify-start" })}
                      onClick={() => setShowExportMenu(false)}
                    >
                      <FileText className="h-3.5 w-3.5" />
                      Markdown (.md)
                    </a>
                    <a
                      href={`${API_URL}/artifacts/${doc.id}/export/stories?format=csv`}
                      className={buttonVariants({ variant: "ghost", size: "sm", className: "justify-start" })}
                      onClick={() => setShowExportMenu(false)}
                    >
                      <FileSpreadsheet className="h-3.5 w-3.5" />
                      CSV (.csv)
                    </a>
                    <a
                      href={`${API_URL}/artifacts/${doc.id}/export/stories?format=json`}
                      className={buttonVariants({ variant: "ghost", size: "sm", className: "justify-start" })}
                      onClick={() => setShowExportMenu(false)}
                    >
                      <FileJson className="h-3.5 w-3.5" />
                      JSON (.json)
                    </a>
                  </div>
                ) : null}
              </div>
            ) : null}
            {doc.artifactType === STORY_BACKLOG_ARTIFACT_TYPE && status === "APPROVED" ? (
              <Button variant="outline" size="sm" onClick={handleOpenJiraPreview}>
                <ExternalLink className="h-3.5 w-3.5" />
                Preview Jira Export
              </Button>
            ) : null}
            {doc.artifactType === CODE_CHANGE_ARTIFACT_TYPE ? (
              <Button variant="outline" size="sm" onClick={handleOpenGithubPreview}>
                <Github className="h-3.5 w-3.5" />
                Preview GitHub PR
              </Button>
            ) : null}
            <a
              href={`${API_URL}/artifacts/${doc.id}/export/document?format=pdf`}
              className={buttonVariants({ variant: "outline", size: "sm" })}
            >
              <Download className="h-3.5 w-3.5" />
              Export PDF
            </a>
            <a
              href={`${API_URL}/artifacts/${doc.id}/export/document?format=html`}
              className={buttonVariants({ variant: "outline", size: "sm" })}
            >
              <FileCode className="h-3.5 w-3.5" />
              Export HTML
            </a>
          </div>
        </div>

        {showVersionForm ? (
          <div className="flex items-center gap-2 rounded-md border border-border bg-muted/40 p-2">
            <Input
              placeholder="What changed in this version? (optional)"
              value={changeSummaryDraft}
              onChange={(e) => setChangeSummaryDraft(e.target.value)}
              className="flex-1"
            />
            <Button size="sm" onClick={handleConfirmNewVersion} disabled={busy}>
              Confirm
            </Button>
            <Button size="sm" variant="ghost" onClick={() => setShowVersionForm(false)} disabled={busy}>
              Cancel
            </Button>
          </div>
        ) : null}

        {showReviewForm ? (
          <div className="flex items-center gap-2 rounded-md border border-border bg-muted/40 p-2">
            <Select value={reviewerId} onChange={(e) => setReviewerId(e.target.value)} className="flex-1">
              {reviewers.length === 0 ? <option value="">No users available</option> : null}
              {reviewers.map((r) => (
                <option key={r.id} value={r.id}>
                  {r.name}
                </option>
              ))}
            </Select>
            <Button size="sm" onClick={handleSendForReview} disabled={busy || !reviewerId}>
              Confirm
            </Button>
            <Button size="sm" variant="ghost" onClick={() => setShowReviewForm(false)} disabled={busy}>
              Cancel
            </Button>
          </div>
        ) : null}

        {!editable ? (
          <p className="rounded-md bg-muted/50 px-3 py-2 text-xs text-muted-foreground">
            This document is read-only while its status is {status.replace(/_/g, " ").toLowerCase()}. Create a new
            version to keep editing.
          </p>
        ) : null}

        {needsClarification && editable ? (
          <ClarificationPanel
            projectId={doc.projectId}
            workflowNodeId={doc.workflowNodeId}
            freeformInputKeys={doc.freeformInputKeys}
            triggeredByUserId={createdById}
            onApplied={handleAgentApplied}
          />
        ) : null}
      </div>

      {/* Three-panel layout — column below lg, row at lg+ (see the outer
          container's comment above). */}
      <div className="flex min-h-0 flex-1 flex-col gap-4 lg:flex-row">
        <aside className="max-h-56 w-full shrink-0 overflow-hidden rounded-lg border border-border lg:h-auto lg:max-h-none lg:w-56">
          <SectionNav
            sections={sections}
            activeSectionId={activeSectionId}
            onSelectSection={setActiveSectionId}
            versions={versions}
            currentVersionNumber={currentVersionNumber}
          />
        </aside>

        <main className="min-w-0 flex-1 overflow-y-auto rounded-lg border border-border p-5">
          {viewMode === "preview" ? (
            <div className="mx-auto max-w-2xl">
              <MarkdownPreview markdown={joinSectionsIntoMarkdown(sections)} />
            </div>
          ) : (
            <div className="mx-auto flex max-w-2xl flex-col gap-6">
              {sections.map((section) => (
                <section
                  key={section.id}
                  onFocus={() => setActiveSectionId(section.id)}
                  className="scroll-mt-4"
                >
                  <h2 className="mb-2 text-sm font-semibold">{section.title}</h2>
                  <Textarea
                    value={section.contentMarkdown}
                    onChange={(e) => updateSection(section.id, e.target.value)}
                    disabled={!editable}
                    rows={Math.max(4, Math.ceil(section.contentMarkdown.length / 70))}
                    className="font-mono text-sm"
                  />
                </section>
              ))}
            </div>
          )}
        </main>

        <aside className="w-full shrink-0 space-y-4 overflow-y-auto lg:w-72">
          <AgentActionsPanel
            activeSectionTitle={activeSection?.title ?? null}
            projectId={doc.projectId}
            workflowNodeId={doc.workflowNodeId}
            agentKey={doc.agentKey}
            artifactId={doc.id}
            artifactEditable={editable}
            documentHasRealSections={docHasRealSections}
            freeformInputKeys={doc.freeformInputKeys}
            triggeredByUserId={createdById}
            onApplied={handleAgentApplied}
          />
          <CommentsPanel comments={doc.comments} />
        </aside>
      </div>

      {showJiraPreview ? (
        <JiraExportPreviewPanel
          preview={jiraPreview}
          loading={jiraPreviewLoading}
          error={jiraPreviewError}
          onClose={() => setShowJiraPreview(false)}
        />
      ) : null}

      {showGithubPreview ? (
        <GithubPrPreviewPanel
          preview={githubPreview}
          loading={githubPreviewLoading}
          error={githubPreviewError}
          onClose={() => setShowGithubPreview(false)}
        />
      ) : null}

      {banner ? (
        <div className="fixed bottom-6 right-6 z-50 rounded-md bg-foreground px-4 py-2 text-sm text-background shadow-lg">
          {banner}
        </div>
      ) : null}
    </div>
  );
}
