"use client";

import { useMemo, useState } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import {
  ADDITIONAL_CONTEXT_MAX_LENGTH,
  buildInputContext,
  missingRequiredFields,
  type FieldSchema,
} from "@/lib/structured-actions/schemas";

export interface StructuredActionFormProps {
  schema: FieldSchema[];
  /** Called with the exact same `inputContext` shape the existing freeform
   * flow already sends — see agent-actions-panel.tsx's runAgentAndApply
   * call. No backend change is required to consume this. */
  onSubmit: (inputContext: Record<string, string>) => void;
  submitLabel?: string;
  submitting?: boolean;
}

function FieldInput({ field, value, onChange }: { field: FieldSchema; value: string; onChange: (v: string) => void }) {
  const id = `structured-field-${field.key}`;
  if (field.type === "textarea") {
    return <Textarea id={id} value={value} onChange={(e) => onChange(e.target.value)} placeholder={field.placeholder} />;
  }
  if (field.type === "select") {
    return (
      <Select id={id} value={value} onChange={(e) => onChange(e.target.value)}>
        <option value="">Select…</option>
        {field.options?.map((opt) => (
          <option key={opt.value} value={opt.value}>
            {opt.label}
          </option>
        ))}
      </Select>
    );
  }
  if (field.type === "multiselect") {
    const selected = new Set(value ? value.split(",") : []);
    return (
      <div className="flex flex-wrap gap-2">
        {field.options?.map((opt) => {
          const checked = selected.has(opt.value);
          return (
            <label key={opt.value} className="flex items-center gap-1 text-xs">
              <input
                type="checkbox"
                checked={checked}
                onChange={() => {
                  const next = new Set(selected);
                  if (checked) next.delete(opt.value);
                  else next.add(opt.value);
                  onChange([...next].join(","));
                }}
              />
              {opt.label}
            </label>
          );
        })}
      </div>
    );
  }
  return <Input id={id} value={value} onChange={(e) => onChange(e.target.value)} placeholder={field.placeholder} />;
}

export function StructuredActionForm({ schema, onSubmit, submitLabel = "Generate WorkPacket", submitting = false }: StructuredActionFormProps) {
  const [values, setValues] = useState<Record<string, string>>({});
  const [additionalContext, setAdditionalContext] = useState("");
  const [showPreview, setShowPreview] = useState(false);
  const [attemptedSubmit, setAttemptedSubmit] = useState(false);

  const missing = useMemo(() => missingRequiredFields(schema, values), [schema, values]);
  const inputContext = useMemo(() => buildInputContext(schema, values, additionalContext), [schema, values, additionalContext]);

  function handlePreviewToggle() {
    setAttemptedSubmit(true);
    if (missing.length === 0) setShowPreview((v) => !v);
  }

  function handleSubmit() {
    setAttemptedSubmit(true);
    if (missing.length > 0) return;
    onSubmit(inputContext);
  }

  return (
    <div className="flex flex-col gap-3">
      {schema.map((field) => (
        <div key={field.key} className="flex flex-col gap-1">
          <label htmlFor={`structured-field-${field.key}`} className="text-xs font-medium text-muted-foreground">
            {field.label}
            {field.required && <span className="text-destructive"> *</span>}
          </label>
          <FieldInput
            field={field}
            value={values[field.key] ?? ""}
            onChange={(v) => setValues((prev) => ({ ...prev, [field.key]: v }))}
          />
          {attemptedSubmit && field.required && !(values[field.key] ?? "").trim() && (
            <p className="text-xs text-destructive">{field.label} is required.</p>
          )}
        </div>
      ))}

      <div className="flex flex-col gap-1">
        <label htmlFor="additional-context" className="text-xs font-medium text-muted-foreground">
          Additional context (optional)
        </label>
        <Textarea
          id="additional-context"
          value={additionalContext}
          maxLength={ADDITIONAL_CONTEXT_MAX_LENGTH}
          onChange={(e) => setAdditionalContext(e.target.value)}
          placeholder="Anything not covered by the fields above — kept short and secondary, not the primary way to describe this task."
        />
        <p className="text-right text-xs text-muted-foreground">
          {additionalContext.length}/{ADDITIONAL_CONTEXT_MAX_LENGTH}
        </p>
      </div>

      <div className="flex gap-2">
        <Button type="button" variant="outline" size="sm" onClick={handlePreviewToggle}>
          {showPreview ? "Hide WorkPacket preview" : "Preview WorkPacket"}
        </Button>
        <Button type="button" size="sm" onClick={handleSubmit} disabled={submitting}>
          {submitLabel}
        </Button>
      </div>

      {showPreview && (
        <pre className="max-h-64 overflow-auto rounded-md bg-muted p-3 text-xs" data-testid="workpacket-preview">
          {JSON.stringify({ input_context: inputContext }, null, 2)}
        </pre>
      )}
    </div>
  );
}
