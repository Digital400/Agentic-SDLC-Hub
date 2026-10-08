"use client";

import { Input } from "@/components/ui/input";
import type { ApiProviderOption, ApiProviderOverride } from "@/lib/api";

// Shown next to the "LLM:" provider dropdown (see agent-actions-panel.tsx
// and workflow/node-details-panel.tsx) whenever a specific provider is
// selected — lets a user pick a specific MODEL within that provider (e.g.
// "claude-opus-5" instead of whatever the backend has configured as its
// default), not just the provider itself. A native <input list> +
// <datalist> rather than a closed <Select>: a curated provider (Anthropic,
// Claude Code) offers real suggestions, but Hugging Face/OpenRouter/NVIDIA
// have far too large a catalog to enumerate — typing any id must always
// work, so suggestions are offered, never enforced.
export function ModelOverrideInput({
  provider,
  providerOptions,
  value,
  onChange,
}: {
  provider: ApiProviderOverride | "";
  providerOptions: ApiProviderOption[];
  value: string;
  onChange: (value: string) => void;
}) {
  if (!provider) return null;
  const option = providerOptions.find((p) => p.value === provider);
  const listId = `model-suggestions-${provider}`;

  return (
    <div className="mb-2">
      <Input
        list={option?.available_models.length ? listId : undefined}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={option?.model ? `Model (default: ${option.model})` : "Model (optional)"}
        className="h-8 text-xs"
        title="Override the specific model used for this run — leave blank to use this provider's configured default."
      />
      {option?.available_models.length ? (
        <datalist id={listId}>
          {option.available_models.map((m) => (
            <option key={m} value={m} />
          ))}
        </datalist>
      ) : null}
    </div>
  );
}
