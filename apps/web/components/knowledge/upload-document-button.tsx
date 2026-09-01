"use client";

import { useState } from "react";
import { Upload } from "lucide-react";

import { Button } from "@/components/ui/button";
import { UploadDocumentModal } from "@/components/knowledge/upload-document-modal";

export function UploadDocumentButton({ uploadedById }: { uploadedById: string | null }) {
  const [open, setOpen] = useState(false);

  return (
    <>
      <Button onClick={() => setOpen(true)}>
        <Upload className="h-4 w-4" />
        Upload document
      </Button>
      {open ? <UploadDocumentModal uploadedById={uploadedById} onClose={() => setOpen(false)} /> : null}
    </>
  );
}
