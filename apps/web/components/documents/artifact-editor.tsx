"use client";

import { useEffect, useRef, useState } from "react";
import { Download, FileCode, Save, SendHorizontal, GitBranch } from "lucide-react";

import { AgentActionsPanel } from "@/components/documents/agent-actions-panel";
import { CommentsPanel } from "@/components/documents/comments-panel";
import { SectionNav } from "@/components/documents/section-nav";
import { ArtifactStatusBadge } from "@/components/status-badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { formatRelativeTime } from "@/lib/format";
import { api, ApiError } from "@/lib/api";
import { joinSectionsIntoMarkdown } from "@/lib/markdown-sections";
import { toArtifactVersionSummary } from "@/lib/mappers";
import type { ArtifactDocument, ArtifactSection, ArtifactStatus } from "@/lib/types";

export function ArtifactEditor({ document: doc, createdById }: { document: ArtifactDocument; createdById: string | null }) {
  const [sections, setSections] = useState<ArtifactSection[]>(doc.sections);
  const [dirty, setDirty] = useState(false);
  const [status, setStatus] = useState<ArtifactStatus>(doc.status);
  const [versions, setVersions] = useState(doc.versions);
  const [currentVersionNumber, setCurrentVersionNumber] = useState(doc.currentVersionNumber);
  const [activeSectionId, setActiveSectionId] = useState(doc.sections[0]?.id ?? "");
  const [savedAt, setSavedAt] = useState<string | null>(null);
  const [showVersionForm, setShowVersionForm] = useState(false);
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
    setBusy(true);
    try {
      await api.artifacts.updateContent(doc.id, { content_markdown: joinSectionsIntoMarkdown(sections) });
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

  async function handleSendForReview() {
    setBusy(true);
    try {
      const artifact = await api.artifacts.submitForReview(doc.id);
      setStatus(artifact.status);
      flash("Sent for review.");
    } catch (err) {
      flash(err instanceof ApiError ? `Failed to submit: ${err.message}` : "Failed to submit for review.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex h-[calc(100vh-8.5rem)] flex-col">
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
              onClick={handleSendForReview}
              disabled={dirty || busy || (status !== "DRAFT" && status !== "NEEDS_CHANGES")}
              title={dirty ? "Save your draft first" : undefined}
            >
              <SendHorizontal className="h-3.5 w-3.5" />
              Send for review
            </Button>
            <div className="mx-1 h-5 w-px bg-border" />
            <Button variant="outline" size="sm" onClick={() => flash("PDF export isn't available yet.")}>
              <Download className="h-3.5 w-3.5" />
              Export PDF
            </Button>
            <Button variant="outline" size="sm" onClick={() => flash("HTML export isn't available yet.")}>
              <FileCode className="h-3.5 w-3.5" />
              Export HTML
            </Button>
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

        {!editable ? (
          <p className="rounded-md bg-muted/50 px-3 py-2 text-xs text-muted-foreground">
            This document is read-only while its status is {status.replace(/_/g, " ").toLowerCase()}. Create a new
            version to keep editing.
          </p>
        ) : null}
      </div>

      {/* Three-panel layout */}
      <div className="flex min-h-0 flex-1 gap-4">
        <aside className="w-56 shrink-0 overflow-hidden rounded-lg border border-border">
          <SectionNav
            sections={sections}
            activeSectionId={activeSectionId}
            onSelectSection={setActiveSectionId}
            versions={versions}
            currentVersionNumber={currentVersionNumber}
          />
        </aside>

        <main className="min-w-0 flex-1 overflow-y-auto rounded-lg border border-border p-5">
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
        </main>

        <aside className="w-72 shrink-0 space-y-4 overflow-y-auto">
          <AgentActionsPanel activeSectionTitle={activeSection?.title ?? null} />
          <CommentsPanel comments={doc.comments} />
        </aside>
      </div>

      {banner ? (
        <div className="fixed bottom-6 right-6 z-50 rounded-md bg-foreground px-4 py-2 text-sm text-background shadow-lg">
          {banner}
        </div>
      ) : null}
    </div>
  );
}
