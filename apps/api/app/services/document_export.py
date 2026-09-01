"""Exports an artifact's current content as a standalone HTML or PDF
document — the "Export HTML" / "Export PDF" buttons in the Document
Editor (see app/api/routes/artifacts.py's export_document endpoint).

Works for any artifact type/status with a current version (unlike the
Story Crafting -> Jira export, which only makes sense for one specific
artifact type once approved) — this is just "give me what's on screen as
a file", so nothing about the artifact's own lifecycle gates it.

Pipeline: Markdown -> HTML (via the `markdown` library) -> optionally PDF
(via `xhtml2pdf`, built on reportlab — pure Python, no system Cairo/Pango
build step, unlike e.g. WeasyPrint). Both formats render the exact same
HTML underneath, so what you see in "Export HTML" is what "Export PDF"
prints — one rendering path, not two independently-maintained ones.
"""

import io

import markdown as markdown_lib
from xhtml2pdf import pisa

# Extensions: "tables" for Markdown tables (story backlogs, matrices),
# "fenced_code" for ``` code blocks — the two Markdown features this app's
# own generated documents actually use.
_MARKDOWN_EXTENSIONS = ["tables", "fenced_code"]

# Deliberately plain, print-friendly CSS — this is an exported document
# meant to be read or archived, not a themed app screen.
_DOCUMENT_CSS = """
body { font-family: Helvetica, Arial, sans-serif; color: #1a1a1a; line-height: 1.5; margin: 2rem; }
h1 { font-size: 20px; border-bottom: 1px solid #ccc; padding-bottom: 8px; }
h2 { font-size: 16px; margin-top: 1.5em; }
h3 { font-size: 14px; }
code, pre { font-family: "Courier New", monospace; background: #f4f4f4; }
pre { padding: 8px; border-radius: 4px; }
table { border-collapse: collapse; width: 100%; margin: 1em 0; }
th, td { border: 1px solid #ccc; padding: 6px 8px; text-align: left; }
.export-meta { color: #666; font-size: 11px; margin-bottom: 1.5em; }
"""


class DocumentExportError(Exception):
    """Raised when xhtml2pdf fails to render a PDF — a malformed-content
    problem, not something the caller can retry its way out of."""


def render_html_document(*, title: str, subtitle: str, content_markdown: str) -> str:
    """Renders a complete, standalone HTML document — used directly for
    Export HTML, and as the input xhtml2pdf converts for Export PDF."""
    body_html = markdown_lib.markdown(content_markdown, extensions=_MARKDOWN_EXTENSIONS)
    return (
        "<!DOCTYPE html>\n"
        "<html><head>"
        f"<meta charset=\"utf-8\"><title>{title}</title>"
        f"<style>{_DOCUMENT_CSS}</style>"
        "</head><body>"
        f"<h1>{title}</h1>"
        f"<p class=\"export-meta\">{subtitle}</p>"
        f"{body_html}"
        "</body></html>"
    )


def render_pdf_document(*, title: str, subtitle: str, content_markdown: str) -> bytes:
    """Renders the same document as `render_html_document`, as PDF bytes.
    Raises DocumentExportError if xhtml2pdf can't produce a PDF from it
    (e.g. content with markup xhtml2pdf's HTML parser chokes on) — the
    route turns that into a 500 with a clear message rather than
    returning a corrupt/empty file."""
    html = render_html_document(title=title, subtitle=subtitle, content_markdown=content_markdown)
    buffer = io.BytesIO()
    result = pisa.CreatePDF(html, dest=buffer)
    if result.err:
        raise DocumentExportError(f"Failed to render PDF ({result.err} error(s) from xhtml2pdf).")
    return buffer.getvalue()
