/**
 * The backend stores one `content_markdown` blob per ArtifactVersion — it
 * has no concept of "sections". The document editor UI is built around
 * sections (left-panel navigation, one editable block per section), so
 * these two functions reconcile the two: split real content into sections
 * by top-level (`##`) heading for editing, and join them back into one
 * blob before saving. Purely a client-side view convenience — nothing is
 * lost or reordered by round-tripping through these.
 */

import type { ArtifactSection } from "@/lib/types";

let sectionIdCounter = 0;
function nextSectionId(): string {
  sectionIdCounter += 1;
  return `section-${sectionIdCounter}`;
}

export function splitMarkdownIntoSections(markdown: string): ArtifactSection[] {
  const lines = markdown.split("\n");
  const sections: ArtifactSection[] = [];
  let currentTitle = "Content";
  let currentLines: string[] = [];
  let currentIsSynthetic = true;

  function flush() {
    const content = currentLines.join("\n").trim();
    if (content.length > 0 || !currentIsSynthetic) {
      sections.push({ id: nextSectionId(), title: currentTitle, contentMarkdown: content, isSynthetic: currentIsSynthetic });
    }
  }

  for (const line of lines) {
    const headingMatch = /^##\s+(.*)$/.exec(line);
    if (headingMatch) {
      flush();
      currentTitle = headingMatch[1].trim();
      currentLines = [];
      currentIsSynthetic = false;
    } else {
      currentLines.push(line);
    }
  }
  flush();

  // No "##" headings at all (e.g. a title-only "#" document, or plain
  // text) — treat the whole thing as one section rather than losing content.
  if (sections.length === 0) {
    return [{ id: nextSectionId(), title: "Content", contentMarkdown: markdown.trim(), isSynthetic: true }];
  }
  return sections;
}

/** True only if `markdown` has at least one genuine `##` heading —
 * mirrors apps/api/app/services/markdown_sections.py's has_real_sections.
 * splitMarkdownIntoSections's fallback "Content" section (above) is a
 * synthesized label that never appears in the source text, not a real,
 * individually-improvable section — see agent-actions-panel.tsx, which
 * uses this to disable "Improve section" for a headingless document
 * (asking the IMPROVE agent to revise "the Content section" there is
 * really asking it to regenerate the whole document from one
 * instruction, which has previously wiped out real content). */
export function hasRealSections(markdown: string): boolean {
  return /^##\s+\S/m.test(markdown);
}

// Mirrors apps/api/app/services/ai_generation.py's CLARIFICATION_MARKER —
// an agent's output starts with this exact heading when it needs more
// information before it can draft the real artifact (see
// format_clarification_output). Kept as a literal here rather than
// fetched from the backend since it's a fixed protocol constant, not
// configuration.
const CLARIFICATION_MARKER = "# Clarification Needed";

/** True if `markdown` is an agent's clarification request rather than a
 * real drafted document — see components/documents/clarification-panel.tsx
 * for the UI that lets a user answer it directly. */
export function isClarificationRequest(markdown: string): boolean {
  return markdown.trimStart().startsWith(CLARIFICATION_MARKER);
}

export function joinSectionsIntoMarkdown(sections: ArtifactSection[]): string {
  // A synthetic "Content" section's title never existed in the source —
  // writing it back as a literal "## Content" heading would permanently
  // inject a heading the original document never had (this exact bug
  // once turned a story backlog's intro paragraph into a "## Content"
  // section on its very first save). Write its text as-is instead.
  return sections
    .map((s) => (s.isSynthetic ? s.contentMarkdown : `## ${s.title}\n\n${s.contentMarkdown}`))
    .filter((part) => part.trim().length > 0)
    .join("\n\n");
}
