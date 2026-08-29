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
  let sawHeading = false;

  function flush() {
    const content = currentLines.join("\n").trim();
    if (content.length > 0 || sawHeading) {
      sections.push({ id: nextSectionId(), title: currentTitle, contentMarkdown: content });
    }
  }

  for (const line of lines) {
    const headingMatch = /^##\s+(.*)$/.exec(line);
    if (headingMatch) {
      flush();
      currentTitle = headingMatch[1].trim();
      currentLines = [];
      sawHeading = true;
    } else {
      currentLines.push(line);
    }
  }
  flush();

  // No "##" headings at all (e.g. a title-only "#" document, or plain
  // text) — treat the whole thing as one section rather than losing content.
  if (sections.length === 0) {
    return [{ id: nextSectionId(), title: "Content", contentMarkdown: markdown.trim() }];
  }
  return sections;
}

export function joinSectionsIntoMarkdown(sections: ArtifactSection[]): string {
  return sections.map((s) => `## ${s.title}\n\n${s.contentMarkdown}`).join("\n\n");
}
