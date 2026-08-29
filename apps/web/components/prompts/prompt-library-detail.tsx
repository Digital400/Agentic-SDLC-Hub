"use client";

import { useState } from "react";
import { Bot } from "lucide-react";

import { PromptEditor, type PromptEditableFields } from "@/components/prompts/prompt-editor";
import { PromptVersionHistory } from "@/components/prompts/prompt-version-history";
import { TestPromptPlaceholder } from "@/components/prompts/test-prompt-placeholder";
import { api, ApiError } from "@/lib/api";
import { toAgentPromptVersion } from "@/lib/mappers";
import type { AgentDefinitionSummary, AgentPromptVersion } from "@/lib/types";

export function PromptLibraryDetail({
  agent,
  initialVersions,
}: {
  agent: AgentDefinitionSummary;
  initialVersions: AgentPromptVersion[];
}) {
  const [versions, setVersions] = useState<AgentPromptVersion[]>(initialVersions);
  const activeVersion = versions.find((v) => v.isActive) ?? versions[0];
  const [selectedId, setSelectedId] = useState(activeVersion.id);
  const selected = versions.find((v) => v.id === selectedId) ?? activeVersion;
  const [banner, setBanner] = useState<string | null>(null);

  function flash(message: string) {
    setBanner(message);
    window.setTimeout(() => setBanner(null), 4000);
  }

  async function handleSaveInPlace(fields: PromptEditableFields) {
    try {
      const updated = await api.prompts.update(selected.id, {
        name: fields.name,
        stage: fields.stage,
        system_prompt: fields.systemPrompt,
        output_format: fields.outputFormat,
        validation_checklist: fields.validationChecklist,
      });
      const mapped = toAgentPromptVersion(updated);
      setVersions((prev) => prev.map((v) => (v.id === mapped.id ? mapped : v)));
      flash(`v${mapped.version} updated.`);
    } catch (err) {
      flash(err instanceof ApiError ? `Failed to save: ${err.message}` : "Failed to save prompt.");
    }
  }

  async function handleSaveAsNewVersion(fields: PromptEditableFields) {
    try {
      const created = await api.prompts.createVersion(selected.id, {
        name: fields.name,
        stage: fields.stage,
        system_prompt: fields.systemPrompt,
        output_format: fields.outputFormat,
        validation_checklist: fields.validationChecklist,
      });
      const mapped = toAgentPromptVersion(created);
      setVersions((prev) => [...prev, mapped]);
      setSelectedId(mapped.id);
      flash(`v${mapped.version} created — activate it to make it live.`);
    } catch (err) {
      flash(err instanceof ApiError ? `Failed to create version: ${err.message}` : "Failed to create version.");
    }
  }

  async function handleActivate(versionId: string) {
    try {
      const activated = await api.prompts.activate(versionId);
      const mapped = toAgentPromptVersion(activated);
      setVersions((prev) => prev.map((v) => ({ ...v, isActive: v.id === mapped.id, updatedAt: v.id === mapped.id ? mapped.updatedAt : v.updatedAt })));
      flash(`v${mapped.version} activated.`);
    } catch (err) {
      flash(err instanceof ApiError ? `Failed to activate: ${err.message}` : "Failed to activate version.");
    }
  }

  return (
    <div>
      <div className="mb-6 flex items-center gap-3 border-b border-border pb-4">
        <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-md bg-secondary">
          <Bot className="h-5 w-5" />
        </div>
        <div>
          <h1 className="text-lg font-semibold">{agent.name}</h1>
          <p className="text-xs text-muted-foreground">
            {agent.agentKey} · {agent.modelName}
          </p>
        </div>
      </div>

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-3">
        <div className="xl:col-span-2">
          <PromptEditor version={selected} onSaveInPlace={handleSaveInPlace} onSaveAsNewVersion={handleSaveAsNewVersion} />
        </div>
        <div className="flex flex-col gap-4">
          <PromptVersionHistory
            versions={versions}
            selectedVersionId={selected.id}
            onSelect={setSelectedId}
            onActivate={handleActivate}
          />
          <TestPromptPlaceholder />
        </div>
      </div>

      {banner ? (
        <div className="fixed bottom-6 right-6 z-50 rounded-md bg-foreground px-4 py-2 text-sm text-background shadow-lg">
          {banner}
        </div>
      ) : null}
    </div>
  );
}
