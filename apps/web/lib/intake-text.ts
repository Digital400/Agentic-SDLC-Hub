// Helpers for long freeform agent input (e.g. a big stakeholder request).
// Mirrors apps/api/app/services/token_budget.py (4 characters per token) and
// apps/api/app/schemas/agent_run.py's MAX_INPUT_CHARS so the counters the user
// sees match what the server does.

export const MAX_INPUT_CHARS = 200_000;
const CHARS_PER_TOKEN = 4;
// Roughly the share of a default (8,000-token) context budget the server lets
// freeform input use before it condenses it automatically.
export const CONDENSE_ABOVE_TOKENS = 4_000;

export function countWords(text: string): number {
  const trimmed = text.trim();
  return trimmed === "" ? 0 : trimmed.split(/\s+/).length;
}

export function estimateTokens(text: string): number {
  return Math.ceil(text.length / CHARS_PER_TOKEN);
}

export function totalChars(values: Record<string, string>): number {
  return Object.values(values).reduce((sum, v) => sum + v.length, 0);
}

export function exceedsInputLimit(values: Record<string, string>): boolean {
  return totalChars(values) > MAX_INPUT_CHARS;
}

export interface InputCondensation {
  condensed: boolean;
  original_tokens: number;
  final_tokens: number;
  parts: number;
  provider_summaries: number;
  extractive_summaries: number;
  hard_truncated: boolean;
}

/** Reads the condensation report the server attaches to a run, if any. */
export function readCondensation(tokenBudgetReport: Record<string, unknown> | null | undefined): InputCondensation | null {
  const raw = tokenBudgetReport?.["input_condensation"] as InputCondensation | undefined;
  return raw && raw.condensed ? raw : null;
}

export function describeCondensation(c: InputCondensation): string {
  const how = c.provider_summaries > 0 ? "summarised by the AI" : "shortened automatically";
  const base = `Your input was long (~${c.original_tokens.toLocaleString()} tokens), so it was ${how} in ${c.parts} part${c.parts === 1 ? "" : "s"} to ~${c.final_tokens.toLocaleString()} tokens before the agent read it. Your original text is kept on the run.`;
  return c.hard_truncated ? `${base} It was so large that the end had to be cut — please split it into smaller requests.` : base;
}

const DRAFT_PREFIX = "sdlc-hub:intake-draft:";

export function loadDraft(key: string): string {
  try {
    return window.localStorage.getItem(DRAFT_PREFIX + key) ?? "";
  } catch {
    return "";
  }
}

export function saveDraft(key: string, value: string): void {
  try {
    if (value === "") window.localStorage.removeItem(DRAFT_PREFIX + key);
    else window.localStorage.setItem(DRAFT_PREFIX + key, value);
  } catch {
    // Storage can be unavailable (private mode, quota) — autosave is a convenience only.
  }
}

export function clearDraft(key: string): void {
  saveDraft(key, "");
}
