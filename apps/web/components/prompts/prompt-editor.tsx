"use client";

import { useEffect, useState } from "react";
import { Plus, X } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import type { AgentPromptVersion } from "@/lib/types";

export interface PromptEditableFields {
  name: string;
  stage: string;
  systemPrompt: string;
  outputFormat: string;
  validationChecklist: string[];
}

// 2. Prompt editor. Editing behavior deliberately mirrors the backend's
// real rule (see apps/api/app/api/routes/prompts.py): the active version
// can't be silently edited in place — doing so creates a new (inactive)
// version instead, so a live prompt never changes without a new,
// reviewable version. Editing an inactive version saves in place.
export function PromptEditor({
  version,
  onSaveInPlace,
  onSaveAsNewVersion,
}: {
  version: AgentPromptVersion;
  onSaveInPlace: (fields: PromptEditableFields) => void;
  onSaveAsNewVersion: (fields: PromptEditableFields) => void;
}) {
  const [fields, setFields] = useState<PromptEditableFields>(toFields(version));
  const [dirty, setDirty] = useState(false);
  const [newChecklistItem, setNewChecklistItem] = useState("");

  useEffect(() => {
    setFields(toFields(version));
    setDirty(false);
  }, [version]);

  function update<K extends keyof PromptEditableFields>(key: K, value: PromptEditableFields[K]) {
    setFields((prev) => ({ ...prev, [key]: value }));
    setDirty(true);
  }

  function addChecklistItem() {
    if (newChecklistItem.trim().length === 0) return;
    update("validationChecklist", [...fields.validationChecklist, newChecklistItem.trim()]);
    setNewChecklistItem("");
  }

  function removeChecklistItem(index: number) {
    update(
      "validationChecklist",
      fields.validationChecklist.filter((_, i) => i !== index)
    );
  }

  function save() {
    if (version.isActive) {
      onSaveAsNewVersion(fields);
    } else {
      onSaveInPlace(fields);
    }
    setDirty(false);
  }

  return (
    <Card>
      <CardHeader className="flex-row items-center justify-between space-y-0">
        <div>
          <CardTitle className="text-sm">
            Prompt editor — v{version.version}
            {version.isActive ? (
              <Badge variant="success" className="ml-2 align-middle">
                Active
              </Badge>
            ) : (
              <Badge variant="gray" className="ml-2 align-middle">
                Draft version
              </Badge>
            )}
          </CardTitle>
          <CardDescription>
            {version.isActive
              ? "This version is live. Saving changes creates a new version instead of editing it in place."
              : "Editing this inactive version saves in place."}
          </CardDescription>
        </div>
        <Button onClick={save} disabled={!dirty}>
          {version.isActive ? "Save as new version" : "Save"}
        </Button>
      </CardHeader>
      <CardContent className="flex flex-col gap-4">
        <div>
          <label className="mb-1 block text-xs font-medium">Name</label>
          <Input value={fields.name} onChange={(e) => update("name", e.target.value)} />
        </div>

        <div>
          <label className="mb-1 block text-xs font-medium">Stage</label>
          <Input value={fields.stage} onChange={(e) => update("stage", e.target.value)} className="font-mono text-sm" />
        </div>

        <div>
          <label className="mb-1 block text-xs font-medium">System prompt</label>
          <Textarea
            value={fields.systemPrompt}
            onChange={(e) => update("systemPrompt", e.target.value)}
            rows={8}
            className="font-mono text-sm"
          />
        </div>

        <div>
          <label className="mb-1 block text-xs font-medium">Output format</label>
          <Textarea value={fields.outputFormat} onChange={(e) => update("outputFormat", e.target.value)} rows={2} />
        </div>

        <div>
          <label className="mb-1 block text-xs font-medium">Validation checklist</label>
          <p className="mb-2 text-xs text-muted-foreground">
            Criteria the agent should self-check before finalizing output.
          </p>
          <ul className="mb-2 flex flex-col gap-1.5">
            {fields.validationChecklist.map((item, i) => (
              <li key={i} className="flex items-center gap-2 text-sm">
                <span className="flex-1 rounded-md border border-border bg-muted/30 px-2 py-1">{item}</span>
                <Button variant="ghost" size="icon" className="h-7 w-7 shrink-0" onClick={() => removeChecklistItem(i)}>
                  <X className="h-3.5 w-3.5" />
                </Button>
              </li>
            ))}
          </ul>
          <div className="flex gap-2">
            <Input
              value={newChecklistItem}
              onChange={(e) => setNewChecklistItem(e.target.value)}
              placeholder="Add a checklist item…"
              onKeyDown={(e) => e.key === "Enter" && (e.preventDefault(), addChecklistItem())}
            />
            <Button variant="outline" onClick={addChecklistItem}>
              <Plus className="h-3.5 w-3.5" />
              Add
            </Button>
          </div>
        </div>
      </CardContent>
    </Card>
  );
}

function toFields(version: AgentPromptVersion): PromptEditableFields {
  return {
    name: version.name,
    stage: version.stage,
    systemPrompt: version.systemPrompt,
    outputFormat: version.outputFormat,
    validationChecklist: [...version.validationChecklist],
  };
}
