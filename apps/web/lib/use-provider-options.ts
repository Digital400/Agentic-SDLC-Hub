"use client";

import { useEffect, useState } from "react";

import { api, type ApiProviderOption, type ApiProviderOverride } from "@/lib/api";

// Backs the "LLM:" dropdown next to Run Agent — see GET /agent-runs/providers
// and app/schemas/agent_run.py's ProviderOptionRead. Real configured model
// names and real availability (an API key actually being set, or, for
// claude_agent_sdk, this project having a connected repository), not a
// static label list the user has no way to tell apart from what will
// actually work.
export function useProviderOptions(projectId: string): { loading: boolean; options: ApiProviderOption[] } {
  const [state, setState] = useState<{ loading: boolean; options: ApiProviderOption[] }>({ loading: true, options: [] });

  useEffect(() => {
    let cancelled = false;
    api.agentRuns
      .listProviders(projectId)
      .then((options) => {
        if (!cancelled) setState({ loading: false, options });
      })
      .catch(() => {
        if (!cancelled) setState({ loading: false, options: [] });
      });
    return () => {
      cancelled = true;
    };
  }, [projectId]);

  return state;
}

/** "Anthropic (Claude API) — claude-sonnet-5" / "Hugging Face — Qwen/Qwen2.5-Coder-32B-Instruct (not configured)". */
export function providerOptionLabel(option: ApiProviderOption): string {
  const model = option.model ? ` — ${option.model}` : "";
  const warning = option.configured ? "" : model ? " (not configured)" : " — not configured";
  return `${option.label}${model}${warning}`;
}

/** One row of the flattened "quick pick" dropdown — see buildQuickPicks. */
export interface ProviderQuickPick {
  /** Encodes both provider + model as one <select> value: "anthropic::claude-sonnet-5", or just "anthropic" when there's no curated model for this row. */
  value: string;
  label: string;
  provider: ApiProviderOverride;
  /** The model to apply when this row is picked — null means "leave the model field as the user already has it". */
  model: string | null;
  configured: boolean;
  unavailable_reason: string | null;
}

export function quickPickValue(provider: ApiProviderOverride, model: string | null): string {
  return model ? `${provider}::${model}` : provider;
}

export function parseQuickPickValue(value: string): { provider: ApiProviderOverride; model: string | null } {
  const [provider, model] = value.split("::");
  return { provider: provider as ApiProviderOverride, model: model ?? null };
}

/** Flattens each provider's curated available_models into its own selectable
 * row (e.g. "Anthropic (Claude API) — claude-sonnet-5" as a single pick,
 * not "pick Anthropic, then separately type claude-sonnet-5") — so a known
 * model is one click away in the main dropdown, not a two-step combo. A
 * provider with no curated suggestions (Gemini/OpenRouter/NVIDIA/Hugging
 * Face/Ollama) still gets exactly one row, same as before; the separate
 * free-text Model field below remains the way to reach anything not listed
 * here (a newer Claude model, a different Hugging Face repo id, ...). */
export function buildQuickPicks(options: ApiProviderOption[]): ProviderQuickPick[] {
  return options.flatMap((option): ProviderQuickPick[] => {
    if (option.available_models.length === 0) {
      return [
        {
          value: quickPickValue(option.value, null),
          label: providerOptionLabel(option),
          provider: option.value,
          model: null,
          configured: option.configured,
          unavailable_reason: option.unavailable_reason,
        },
      ];
    }
    return option.available_models.map((model) => ({
      value: quickPickValue(option.value, model),
      label: `${option.label} — ${model}${option.configured ? "" : " (not configured)"}`,
      provider: option.value,
      model,
      configured: option.configured,
      unavailable_reason: option.unavailable_reason,
    }));
  });
}
