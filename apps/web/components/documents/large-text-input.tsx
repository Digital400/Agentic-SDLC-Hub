"use client";

import { useEffect, useRef, useState } from "react";
import { Maximize2, Minimize2, X } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import {
  CONDENSE_ABOVE_TOKENS,
  MAX_INPUT_CHARS,
  countWords,
  estimateTokens,
  loadDraft,
  saveDraft,
} from "@/lib/intake-text";

const normalise = (text: string) => text.replace(/\r\n?/g, "\n");

// A compact box for short text that grows into a full editor for long text:
// live word/token counters, an unsent-draft autosave (so a long paste is never
// lost to a refresh or a failed run), and a clear warning when the input is
// long enough to be condensed, or too long to send.
export function LargeTextInput({
  label,
  value,
  onChange,
  storageKey,
  disabled,
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  /** Unique per project + stage + field; drafts are saved under it. */
  storageKey: string;
  disabled?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const [wide, setWide] = useState(false);
  const [restored, setRestored] = useState(false);
  const [savedAt, setSavedAt] = useState<Date | null>(null);
  const restoredOnce = useRef(false);

  // Restore an unsent draft once, when the box is empty.
  useEffect(() => {
    if (restoredOnce.current) return;
    restoredOnce.current = true;
    if (value === "") {
      const draft = loadDraft(storageKey);
      if (draft !== "") {
        onChange(draft);
        setRestored(true);
      }
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps -- run once per key
  }, [storageKey]);

  // Debounced autosave.
  useEffect(() => {
    if (!restoredOnce.current) return;
    const timer = setTimeout(() => {
      saveDraft(storageKey, value);
      if (value !== "") setSavedAt(new Date());
    }, 400);
    return () => clearTimeout(timer);
  }, [value, storageKey]);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open]);

  const words = countWords(value);
  const tokens = estimateTokens(value);
  const chars = value.length;
  const tooLong = chars > MAX_INPUT_CHARS;
  const willCondense = !tooLong && tokens > CONDENSE_ABOVE_TOKENS;
  const pct = Math.min(100, Math.round((chars / MAX_INPUT_CHARS) * 100));
  const barColour = tooLong ? "bg-destructive" : willCondense ? "bg-amber-500" : "bg-primary";
  const pretty = label.replace(/_/g, " ");

  const status = (
    <div className="mt-1 flex flex-col gap-1 text-[11px] text-muted-foreground" aria-live="polite">
      <div className="flex items-center justify-between gap-2">
        <span>
          {words.toLocaleString()} words · ~{tokens.toLocaleString()} tokens
        </span>
        {savedAt && value !== "" ? <span>Draft saved {savedAt.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}</span> : null}
      </div>
      {tooLong ? (
        <p className="text-destructive">
          Too long: {chars.toLocaleString()} of {MAX_INPUT_CHARS.toLocaleString()} characters allowed. Split it into smaller requests or remove repeated
          sections.
        </p>
      ) : willCondense ? (
        <p className="text-amber-700 dark:text-amber-400">
          Long input — it will be condensed automatically (facts, numbers and dates kept) before the agent reads it.
        </p>
      ) : null}
      {restored ? <p>Restored your unsent draft.</p> : null}
    </div>
  );

  return (
    <div className="mb-2">
      <div className="relative">
        <Textarea
          placeholder={pretty}
          value={value}
          onChange={(e) => onChange(normalise(e.target.value))}
          rows={3}
          disabled={disabled}
          aria-label={pretty}
          className="resize-y pr-8 text-xs"
        />
        <button
          type="button"
          onClick={() => setOpen(true)}
          disabled={disabled}
          title="Open large editor"
          aria-label={`Open large editor for ${pretty}`}
          className="absolute right-1.5 top-1.5 rounded p-1 text-muted-foreground hover:bg-muted hover:text-foreground"
        >
          <Maximize2 className="h-3.5 w-3.5" />
        </button>
      </div>
      {status}

      {open ? (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4" role="dialog" aria-modal="true" aria-label={`Edit ${pretty}`}>
          <div className={`flex max-h-full w-full flex-col rounded-lg border bg-background shadow-xl ${wide ? "h-full max-w-none" : "h-[85vh] max-w-4xl"}`}>
            <div className="flex items-center justify-between border-b px-4 py-3">
              <h2 className="text-sm font-semibold capitalize">{pretty}</h2>
              <div className="flex items-center gap-1">
                <Button type="button" variant="ghost" size="icon" onClick={() => setWide((w) => !w)} aria-label={wide ? "Exit full screen" : "Full screen"}>
                  {wide ? <Minimize2 className="h-4 w-4" /> : <Maximize2 className="h-4 w-4" />}
                </Button>
                <Button type="button" variant="ghost" size="icon" onClick={() => setOpen(false)} aria-label="Close editor">
                  <X className="h-4 w-4" />
                </Button>
              </div>
            </div>
            <div className="flex min-h-0 flex-1 flex-col p-4">
              <Textarea
                autoFocus
                value={value}
                onChange={(e) => onChange(normalise(e.target.value))}
                placeholder="Who is asking, what they want, deadline and budget, constraints, and how success is measured. Paste emails, notes or specs here."
                className="min-h-0 flex-1 resize-none text-sm leading-relaxed"
                aria-label={pretty}
              />
              <div className="mt-3 flex flex-col gap-2">
                <div className="h-1.5 w-full overflow-hidden rounded bg-muted" aria-hidden>
                  <div className={`h-full ${barColour}`} style={{ width: `${pct}%` }} />
                </div>
                {status}
              </div>
            </div>
            <div className="flex justify-end gap-2 border-t px-4 py-3">
              <Button type="button" size="sm" onClick={() => setOpen(false)}>
                Done
              </Button>
            </div>
          </div>
        </div>
      ) : null}
    </div>
  );
}
