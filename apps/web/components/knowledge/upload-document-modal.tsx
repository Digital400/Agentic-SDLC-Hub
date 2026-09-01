"use client";

import { useState, type FormEvent } from "react";
import { useRouter } from "next/navigation";
import { X } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import { api, ApiError } from "@/lib/api";
import type { KnowledgeContentType } from "@/lib/types";

const CONTENT_TYPE_OPTIONS: { value: KnowledgeContentType; label: string }[] = [
  { value: "COMPANY_STANDARD", label: "Company standard" },
  { value: "PAST_ARTIFACT", label: "Past artifact" },
  { value: "UI_GUIDELINE", label: "UI guideline" },
  { value: "ARCHITECTURE_RULE", label: "Architecture rule" },
  { value: "TESTING_STANDARD", label: "Testing standard" },
  { value: "OTHER", label: "Other" },
];

// Uploads a .txt/.md/.pdf file as a new Knowledge Source — extracted,
// chunked, and embedded in one call (see
// apps/api/app/services/document_ingestion.py and the
// POST /knowledge-sources/upload endpoint). `stage` narrows which
// workflow stage(s) can retrieve this content (see
// apps/api/app/services/retrieval.py's stage-aware retrieval) — left
// blank, a chunk is eligible for every stage.
export function UploadDocumentModal({
  uploadedById,
  onClose,
}: {
  uploadedById: string | null;
  onClose: () => void;
}) {
  const router = useRouter();
  const [file, setFile] = useState<File | null>(null);
  const [title, setTitle] = useState("");
  const [category, setCategory] = useState("");
  const [contentType, setContentType] = useState<KnowledgeContentType>("OTHER");
  const [stage, setStage] = useState("");
  const [domain, setDomain] = useState("");
  const [tags, setTags] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);

    if (uploadedById === null) {
      setError("No users exist yet to attribute this upload to.");
      return;
    }
    if (!file) {
      setError("Choose a file to upload.");
      return;
    }
    if (title.trim() === "" || category.trim() === "") {
      setError("Title and category are required.");
      return;
    }

    const formData = new FormData();
    formData.set("file", file);
    formData.set("title", title.trim());
    formData.set("category", category.trim());
    formData.set("uploaded_by_id", uploadedById);
    formData.set("content_type", contentType);
    if (stage.trim()) formData.set("stage", stage.trim());
    if (domain.trim()) formData.set("domain", domain.trim());
    if (tags.trim()) formData.set("tags", tags.trim());

    setSubmitting(true);
    try {
      await api.knowledgeSources.upload(formData);
      onClose();
      router.refresh();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to upload the document.");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
      <div className="w-full max-w-lg rounded-lg border border-border bg-card shadow-xl">
        <div className="flex items-center justify-between border-b border-border p-4">
          <div>
            <h2 className="text-sm font-semibold">Upload document</h2>
            <p className="text-xs text-muted-foreground">
              .txt, .md, or .pdf — extracted, chunked, and embedded immediately, so it&apos;s retrievable right away.
            </p>
          </div>
          <Button variant="ghost" size="icon" onClick={onClose} aria-label="Close">
            <X className="h-4 w-4" />
          </Button>
        </div>

        <form onSubmit={handleSubmit} className="flex flex-col gap-4 p-4" noValidate>
          <div>
            <label htmlFor="upload-file" className="mb-1 block text-sm font-medium">
              File <span className="text-destructive">*</span>
            </label>
            <input
              id="upload-file"
              type="file"
              accept=".txt,.md,.markdown,.pdf"
              onChange={(e) => {
                const selected = e.target.files?.[0] ?? null;
                setFile(selected);
                if (selected && title.trim() === "") setTitle(selected.name.replace(/\.[^.]+$/, ""));
              }}
              className="block w-full text-sm file:mr-3 file:rounded-md file:border-0 file:bg-secondary file:px-3 file:py-1.5 file:text-sm file:font-medium"
            />
          </div>

          <div>
            <label htmlFor="upload-title" className="mb-1 block text-sm font-medium">
              Title <span className="text-destructive">*</span>
            </label>
            <Input id="upload-title" value={title} onChange={(e) => setTitle(e.target.value)} placeholder="e.g. Company Engineering Handbook" />
          </div>

          <div>
            <label htmlFor="upload-category" className="mb-1 block text-sm font-medium">
              Category <span className="text-destructive">*</span>
            </label>
            <Input id="upload-category" value={category} onChange={(e) => setCategory(e.target.value)} placeholder="e.g. Engineering Standards" />
          </div>

          <div className="grid grid-cols-2 gap-3">
            <div>
              <label htmlFor="upload-content-type" className="mb-1 block text-sm font-medium">
                Content type
              </label>
              <Select
                id="upload-content-type"
                value={contentType}
                onChange={(e) => setContentType(e.target.value as KnowledgeContentType)}
              >
                {CONTENT_TYPE_OPTIONS.map((opt) => (
                  <option key={opt.value} value={opt.value}>
                    {opt.label}
                  </option>
                ))}
              </Select>
            </div>
            <div>
              <label htmlFor="upload-stage" className="mb-1 block text-sm font-medium">
                Stage (optional)
              </label>
              <Input id="upload-stage" value={stage} onChange={(e) => setStage(e.target.value)} placeholder="e.g. hld" />
            </div>
          </div>

          <div className="grid grid-cols-2 gap-3">
            <div>
              <label htmlFor="upload-domain" className="mb-1 block text-sm font-medium">
                Domain (optional)
              </label>
              <Input id="upload-domain" value={domain} onChange={(e) => setDomain(e.target.value)} placeholder="e.g. engineering" />
            </div>
            <div>
              <label htmlFor="upload-tags" className="mb-1 block text-sm font-medium">
                Tags (optional)
              </label>
              <Input id="upload-tags" value={tags} onChange={(e) => setTags(e.target.value)} placeholder="comma, separated" />
            </div>
          </div>

          <p className="text-xs text-muted-foreground">
            Leaving Stage blank makes this content available to every workflow stage; setting it (e.g. &quot;hld&quot;,
            &quot;testing&quot;) narrows it to just that one.
          </p>

          {error ? <p className="text-sm text-destructive">{error}</p> : null}

          <div className="mt-1 flex justify-end gap-2">
            <Button type="button" variant="outline" onClick={onClose} disabled={submitting}>
              Cancel
            </Button>
            <Button type="submit" disabled={submitting}>
              {submitting ? "Uploading…" : "Upload"}
            </Button>
          </div>
        </form>
      </div>
    </div>
  );
}
