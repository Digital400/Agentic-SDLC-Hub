"use client";

import { useEffect, useRef, useState } from "react";
import DOMPurify from "dompurify";
import { marked } from "marked";
import mermaid from "mermaid";

// "default" is tuned for exactly this: a plain light page background —
// which is also why `.mermaid` below gets an explicit white background of
// its own, independent of the app's own light/dark mode (same idea as a
// code block's backdrop never following page theme). The diagram used to
// sit inside a <pre> (see promoteMermaidCodeBlocks's own note on why
// that's gone) whose @tailwindcss/typography styling forces a DARK
// syntax-highlighting backdrop instead — "default" theme's light lavender
// nodes against that were nearly unreadable; that mismatch, not the theme
// choice, was the actual bug.
mermaid.initialize({ startOnLoad: false, theme: "default", securityLevel: "strict" });

// Agents are prompted (see app/db/seed.py's HLD/HLD Delta/Story LLD
// RICH_DEFAULT_PROMPTS) to embed concrete diagrams as ```mermaid fenced
// code blocks — architecture/sequence/ER/class diagrams. `marked` renders
// a fenced block as <pre><code class="language-mermaid">...</code></pre>
// with its text HTML-escaped; this turns exactly that shape into
// <div class="mermaid">raw text</div> (decoding the escaping back out) so
// the render pass below can find and draw it as an SVG.
//
// A <div>, deliberately NOT a <pre> (which is what this used to be): the
// `prose` plugin styles every <pre> as a dark syntax-highlighted code
// block — exactly the "dark backdrop, washed-out diagram" bug reported.
// `prose` has no opinion about a plain <div>, so the diagram's own
// background (set below) is the only one that applies. Any other
// language's code block is left untouched.
function promoteMermaidCodeBlocks(html: string): string {
  return html.replace(
    /<pre><code class="language-mermaid">([\s\S]*?)<\/code><\/pre>/g,
    (_match, escaped: string) => {
      const decoded = escaped
        .replace(/&lt;/g, "<")
        .replace(/&gt;/g, ">")
        .replace(/&quot;/g, '"')
        .replace(/&#39;/g, "'")
        .replace(/&amp;/g, "&");
      return `<div class="mermaid">${decoded}</div>`;
    },
  );
}

let mermaidRenderCounter = 0;

// Renders every `.mermaid` element in `container` individually via
// mermaid.render (not the simpler mermaid.run) specifically so ONE
// diagram's invalid syntax can be caught and handled per-element — run()
// draws its own built-in "bomb" error graphic for a failed diagram with no
// way to intercept it, which is both ugly and useless (a developer can't
// see what was actually wrong, or copy the source to fix it by hand). On a
// parse failure here, the raw Mermaid source is shown instead, with a
// plain-language note — a generation-time syntax error in the source
// document is real and worth surfacing, just not as mermaid's own graphic.
async function renderMermaidDiagrams(container: HTMLElement): Promise<void> {
  const diagrams = Array.from(container.querySelectorAll<HTMLElement>("div.mermaid"));
  for (const el of diagrams) {
    const source = el.textContent ?? "";
    if (!source.trim()) continue;
    try {
      const id = `mermaid-preview-${mermaidRenderCounter++}`;
      const { svg, bindFunctions } = await mermaid.render(id, source);
      el.innerHTML = svg;
      bindFunctions?.(el);
    } catch {
      el.innerHTML = "";
      el.classList.add("mermaid-error");
      const notice = document.createElement("p");
      notice.className = "text-xs text-destructive mb-1";
      notice.textContent = "This diagram has invalid Mermaid syntax and couldn't render — showing its raw source below.";
      const pre = document.createElement("pre");
      pre.className = "whitespace-pre-wrap text-xs";
      pre.textContent = source;
      el.append(notice, pre);
    }
  }
}

// Renders an artifact's Markdown as it will actually look — headings,
// tables, bold, lists, and now diagrams — instead of raw text. `marked`
// has GFM tables on by default; `@tailwindcss/typography`'s `prose` class
// supplies all the heading/table/list styling so this component stays
// this small.
//
// `marked` passes raw HTML in its input straight through (it doesn't
// escape it) — content here is our own agent-generated/human-edited
// Markdown, not arbitrary user HTML, but it's still model output, so it's
// sanitized with DOMPurify before being set as innerHTML rather than
// trusted outright. Sanitizing needs a real DOM (`document`), so this
// renders client-side only, after mount, via useEffect — not during SSR.
export function MarkdownPreview({ markdown }: { markdown: string }) {
  const [html, setHtml] = useState<string | null>(null);
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const rawHtml = marked.parse(markdown, { async: false, gfm: true, breaks: false }) as string;
    setHtml(DOMPurify.sanitize(promoteMermaidCodeBlocks(rawHtml), { ADD_ATTR: ["class"] }));
  }, [markdown]);

  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;
    // A stale run from a previous `html` value has nothing left to corrupt:
    // dangerouslySetInnerHTML has already replaced the DOM nodes it was
    // mutating by the time a later run starts, so writing into them is a
    // harmless no-op on detached elements, not a visible race.
    renderMermaidDiagrams(container).catch(() => {
      // renderMermaidDiagrams already handles a per-diagram failure itself;
      // this only guards against something unrelated (e.g. mermaid.render
      // rejecting outright) from throwing out of the effect entirely.
    });
  }, [html]);

  if (!markdown.trim()) {
    return <p className="text-sm text-muted-foreground">Nothing to preview yet.</p>;
  }
  if (html === null) {
    return <p className="text-sm text-muted-foreground">Rendering preview…</p>;
  }

  return (
    <div
      ref={containerRef}
      className="prose prose-sm max-w-none dark:prose-invert prose-table:text-sm prose-th:bg-muted prose-th:border prose-td:border prose-th:border-border prose-td:border-border [&_.mermaid]:flex [&_.mermaid]:justify-center [&_.mermaid]:overflow-x-auto [&_.mermaid]:rounded-md [&_.mermaid]:border [&_.mermaid]:border-border [&_.mermaid]:bg-white [&_.mermaid]:p-4 [&_.mermaid_svg]:max-w-full [&_.mermaid-error]:block [&_.mermaid-error]:bg-destructive/5 [&_.mermaid-error]:p-3"
      dangerouslySetInnerHTML={{ __html: html }}
    />
  );
}
