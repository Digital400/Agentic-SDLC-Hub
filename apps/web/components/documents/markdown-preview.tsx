"use client";

import { useEffect, useState } from "react";
import DOMPurify from "dompurify";
import { marked } from "marked";

// Renders an artifact's Markdown as it will actually look — headings,
// tables, bold, lists — instead of raw text. `marked` has GFM tables on
// by default; `@tailwindcss/typography`'s `prose` class supplies all the
// heading/table/list styling so this component stays this small.
//
// `marked` passes raw HTML in its input straight through (it doesn't
// escape it) — content here is our own agent-generated/human-edited
// Markdown, not arbitrary user HTML, but it's still model output, so it's
// sanitized with DOMPurify before being set as innerHTML rather than
// trusted outright. Sanitizing needs a real DOM (`document`), so this
// renders client-side only, after mount, via useEffect — not during SSR.
export function MarkdownPreview({ markdown }: { markdown: string }) {
  const [html, setHtml] = useState<string | null>(null);

  useEffect(() => {
    const rawHtml = marked.parse(markdown, { async: false, gfm: true, breaks: false }) as string;
    setHtml(DOMPurify.sanitize(rawHtml));
  }, [markdown]);

  if (!markdown.trim()) {
    return <p className="text-sm text-muted-foreground">Nothing to preview yet.</p>;
  }
  if (html === null) {
    return <p className="text-sm text-muted-foreground">Rendering preview…</p>;
  }

  return (
    <div
      className="prose prose-sm max-w-none dark:prose-invert prose-table:text-sm prose-th:bg-muted prose-th:border prose-td:border prose-th:border-border prose-td:border-border"
      dangerouslySetInnerHTML={{ __html: html }}
    />
  );
}
