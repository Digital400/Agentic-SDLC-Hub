"use client";

import { useState } from "react";
import { Pencil, Plus, Settings2, Trash2, X } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { EmptyState } from "@/components/ui/empty-state";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import {
  api,
  ApiCodingStandardCategory,
  ApiDocumentationTarget,
  ApiEngineeringSetup,
  ApiError,
  ApiGithubSetupOption,
  ApiJiraSetupOption,
} from "@/lib/api";

const CODING_STANDARD_CATEGORY_OPTIONS: { value: ApiCodingStandardCategory; label: string }[] = [
  { value: "GENERAL", label: "General" },
  { value: "ARCHITECTURE", label: "Architecture" },
  { value: "SECURITY", label: "Security" },
  { value: "TESTING", label: "Testing" },
  { value: "GIT", label: "Git" },
  { value: "DOCUMENTATION", label: "Documentation" },
];

const GITHUB_OPTIONS: { value: ApiGithubSetupOption; label: string }[] = [
  { value: "CONNECT_EXISTING_REPO", label: "Connect an existing repository" },
  { value: "CREATE_NEW_REPO", label: "Create a new repository" },
  { value: "SKIP_FOR_NOW", label: "Skip for now" },
];

const JIRA_OPTIONS: { value: ApiJiraSetupOption; label: string }[] = [
  { value: "CONNECT_EXISTING_PROJECT", label: "Connect an existing Jira project" },
  { value: "MAPPING_ONLY", label: "Field mapping only (connect later)" },
  { value: "SKIP_FOR_NOW", label: "Skip for now" },
];

const DOCUMENTATION_OPTIONS: { value: ApiDocumentationTarget; label: string }[] = [
  { value: "INTERNAL_ONLY", label: "Internal only (this app)" },
  { value: "CONFLUENCE", label: "Publish to Confluence" },
  { value: "REPO_MARKDOWN", label: "Publish as Markdown in the repository" },
  { value: "CONFLUENCE_AND_REPO", label: "Both Confluence and repository Markdown" },
];

/**
 * Everything the Create Project wizard collected — technology stack,
 * GitHub/Jira setup intent, coding standards, guardrails, documentation
 * flow, and build/test commands (see
 * app/models/project_engineering_setup.py) — shown here and editable
 * section by section, since until now this data was write-once at
 * project creation and never visible again anywhere in the app.
 *
 * Each section keeps its own local edit-mode + draft state and saves
 * independently via PATCH /projects/{id}/engineering-setup (only the
 * section actually being edited is sent) — a mistake in one section's
 * draft never risks the others. The full updated setup comes back on
 * every save, so `setup` is always the source of truth after that.
 */
export function EngineeringSetupView({
  projectId,
  initialSetup,
  currentUserId,
}: {
  projectId: string;
  initialSetup: ApiEngineeringSetup | null;
  currentUserId: string | null;
}) {
  const [setup, setSetup] = useState(initialSetup);

  if (setup === null) {
    return (
      <EmptyState
        icon={Settings2}
        title="No engineering setup for this project"
        description="This project has no Engineering Setup on record — either it was created before this feature existed, or GitHub/Jira/coding standards were skipped in the Create Project wizard. Nothing here is required for the project to keep working."
      />
    );
  }

  return (
    <div className="flex flex-col gap-4">
      <TechnologyStackSection projectId={projectId} setup={setup} currentUserId={currentUserId} onSaved={setSetup} />
      <RepositorySection projectId={projectId} setup={setup} currentUserId={currentUserId} onSaved={setSetup} />
      <JiraSection projectId={projectId} setup={setup} currentUserId={currentUserId} onSaved={setSetup} />
      <CodingStandardsSection projectId={projectId} setup={setup} currentUserId={currentUserId} onSaved={setSetup} />
      <GuardrailsSection projectId={projectId} setup={setup} currentUserId={currentUserId} onSaved={setSetup} />
      <DocumentationSection projectId={projectId} setup={setup} currentUserId={currentUserId} onSaved={setSetup} />
      <CommandsSection projectId={projectId} setup={setup} currentUserId={currentUserId} onSaved={setSetup} />
    </div>
  );
}

type SectionProps = {
  projectId: string;
  setup: ApiEngineeringSetup;
  currentUserId: string | null;
  onSaved: (setup: ApiEngineeringSetup) => void;
};

function SectionCard({
  title,
  description,
  editing,
  onEdit,
  onCancel,
  onSave,
  saving,
  error,
  children,
  viewContent,
}: {
  title: string;
  description?: string;
  editing: boolean;
  onEdit: () => void;
  onCancel: () => void;
  onSave: () => void;
  saving: boolean;
  error: string | null;
  children: React.ReactNode;
  viewContent: React.ReactNode;
}) {
  return (
    <Card>
      <CardHeader className="flex-row items-start justify-between space-y-0">
        <div>
          <CardTitle className="text-base">{title}</CardTitle>
          {description ? <p className="mt-1 text-xs text-muted-foreground">{description}</p> : null}
        </div>
        {editing ? (
          <div className="flex shrink-0 gap-2">
            <Button type="button" variant="outline" size="sm" onClick={onCancel} disabled={saving}>
              <X className="h-3.5 w-3.5" />
              Cancel
            </Button>
            <Button type="button" size="sm" onClick={onSave} disabled={saving}>
              {saving ? "Saving…" : "Save"}
            </Button>
          </div>
        ) : (
          <Button type="button" variant="outline" size="sm" onClick={onEdit}>
            <Pencil className="h-3.5 w-3.5" />
            Edit
          </Button>
        )}
      </CardHeader>
      <CardContent className="space-y-3">
        {editing ? children : viewContent}
        {error ? <p className="text-xs text-destructive">{error}</p> : null}
      </CardContent>
    </Card>
  );
}

function dd(value: string | null | undefined): string {
  return value && value.trim() !== "" ? value : "—";
}

// --- Technology Stack --------------------------------------------------------------------

function TechnologyStackSection({ projectId, setup, currentUserId, onSaved }: SectionProps) {
  const [editing, setEditing] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [applicationType, setApplicationType] = useState(setup.application_type);
  const [primaryLanguage, setPrimaryLanguage] = useState(setup.primary_language);
  const [frontendFramework, setFrontendFramework] = useState(setup.frontend_framework ?? "");
  const [backendFramework, setBackendFramework] = useState(setup.backend_framework ?? "");
  const [database, setDatabase] = useState(setup.database ?? "");
  const [cloudProvider, setCloudProvider] = useState(setup.cloud_provider ?? "");

  function startEdit() {
    setApplicationType(setup.application_type);
    setPrimaryLanguage(setup.primary_language);
    setFrontendFramework(setup.frontend_framework ?? "");
    setBackendFramework(setup.backend_framework ?? "");
    setDatabase(setup.database ?? "");
    setCloudProvider(setup.cloud_provider ?? "");
    setError(null);
    setEditing(true);
  }

  async function save() {
    if (currentUserId === null) {
      setError("No users exist yet to attribute this change to.");
      return;
    }
    if (applicationType.trim() === "" || primaryLanguage.trim() === "") {
      setError("Application type and primary language are both required.");
      return;
    }
    setSaving(true);
    setError(null);
    try {
      const updated = await api.engineeringSetup.update(projectId, {
        updated_by_id: currentUserId,
        technology_stack: {
          application_type: applicationType.trim(),
          primary_language: primaryLanguage.trim(),
          frontend_framework: frontendFramework.trim() || null,
          backend_framework: backendFramework.trim() || null,
          database: database.trim() || null,
          cloud_provider: cloudProvider.trim() || null,
        },
      });
      onSaved(updated);
      setEditing(false);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to save the technology stack.");
    } finally {
      setSaving(false);
    }
  }

  return (
    <SectionCard
      title="Technology Stack"
      editing={editing}
      onEdit={startEdit}
      onCancel={() => setEditing(false)}
      onSave={save}
      saving={saving}
      error={error}
      viewContent={
        <dl className="grid grid-cols-2 gap-x-4 gap-y-2 text-sm sm:grid-cols-3">
          <div>
            <dt className="text-xs text-muted-foreground">Application type</dt>
            <dd className="font-medium">{setup.application_type}</dd>
          </div>
          <div>
            <dt className="text-xs text-muted-foreground">Primary language</dt>
            <dd className="font-medium">{setup.primary_language}</dd>
          </div>
          <div>
            <dt className="text-xs text-muted-foreground">Frontend framework</dt>
            <dd className="font-medium">{dd(setup.frontend_framework)}</dd>
          </div>
          <div>
            <dt className="text-xs text-muted-foreground">Backend framework</dt>
            <dd className="font-medium">{dd(setup.backend_framework)}</dd>
          </div>
          <div>
            <dt className="text-xs text-muted-foreground">Database</dt>
            <dd className="font-medium">{dd(setup.database)}</dd>
          </div>
          <div>
            <dt className="text-xs text-muted-foreground">Cloud provider</dt>
            <dd className="font-medium">{dd(setup.cloud_provider)}</dd>
          </div>
        </dl>
      }
    >
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        <div>
          <label className="mb-1 block text-xs font-medium">
            Application type <span className="text-destructive">*</span>
          </label>
          <Input value={applicationType} onChange={(e) => setApplicationType(e.target.value)} />
        </div>
        <div>
          <label className="mb-1 block text-xs font-medium">
            Primary language <span className="text-destructive">*</span>
          </label>
          <Input value={primaryLanguage} onChange={(e) => setPrimaryLanguage(e.target.value)} />
        </div>
        <div>
          <label className="mb-1 block text-xs font-medium">Frontend framework</label>
          <Input value={frontendFramework} onChange={(e) => setFrontendFramework(e.target.value)} />
        </div>
        <div>
          <label className="mb-1 block text-xs font-medium">Backend framework</label>
          <Input value={backendFramework} onChange={(e) => setBackendFramework(e.target.value)} />
        </div>
        <div>
          <label className="mb-1 block text-xs font-medium">Database</label>
          <Input value={database} onChange={(e) => setDatabase(e.target.value)} />
        </div>
        <div>
          <label className="mb-1 block text-xs font-medium">Cloud provider</label>
          <Input value={cloudProvider} onChange={(e) => setCloudProvider(e.target.value)} />
        </div>
      </div>
    </SectionCard>
  );
}

// --- GitHub Setup (intent only — see route docstring) -------------------------------------

function RepositorySection({ projectId, setup, currentUserId, onSaved }: SectionProps) {
  const config = setup.repository_config;
  const [editing, setEditing] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [option, setOption] = useState<ApiGithubSetupOption>(config?.option ?? "SKIP_FOR_NOW");
  const [newRepoName, setNewRepoName] = useState(config?.new_repo_name ?? "");
  const [branchNamingPattern, setBranchNamingPattern] = useState(config?.branch_naming_pattern ?? "agent/{task}-{run_id}");
  const [targetBranch, setTargetBranch] = useState(config?.target_branch ?? "main");

  function startEdit() {
    setOption(config?.option ?? "SKIP_FOR_NOW");
    setNewRepoName(config?.new_repo_name ?? "");
    setBranchNamingPattern(config?.branch_naming_pattern ?? "agent/{task}-{run_id}");
    setTargetBranch(config?.target_branch ?? "main");
    setError(null);
    setEditing(true);
  }

  async function save() {
    if (currentUserId === null) {
      setError("No users exist yet to attribute this change to.");
      return;
    }
    setSaving(true);
    setError(null);
    try {
      const updated = await api.engineeringSetup.update(projectId, {
        updated_by_id: currentUserId,
        repository: {
          option,
          new_repo_name: option === "CREATE_NEW_REPO" ? newRepoName.trim() || null : null,
          branch_naming_pattern: branchNamingPattern.trim() || "agent/{task}-{run_id}",
          target_branch: targetBranch.trim() || "main",
        },
      });
      onSaved(updated);
      setEditing(false);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to save the GitHub setup.");
    } finally {
      setSaving(false);
    }
  }

  return (
    <SectionCard
      title="GitHub Setup"
      description="Setup intent only — actually connecting/creating the repository still happens in Settings → Integrations → GitHub. Editing this never touches an already-connected repository."
      editing={editing}
      onEdit={startEdit}
      onCancel={() => setEditing(false)}
      onSave={save}
      saving={saving}
      error={error}
      viewContent={
        <dl className="grid grid-cols-2 gap-x-4 gap-y-2 text-sm sm:grid-cols-3">
          <div>
            <dt className="text-xs text-muted-foreground">Option</dt>
            <dd className="font-medium">{GITHUB_OPTIONS.find((o) => o.value === config?.option)?.label ?? "—"}</dd>
          </div>
          {config?.new_repo_name ? (
            <div>
              <dt className="text-xs text-muted-foreground">New repository name</dt>
              <dd className="font-medium">{config.new_repo_name}</dd>
            </div>
          ) : null}
          <div>
            <dt className="text-xs text-muted-foreground">Branch naming pattern</dt>
            <dd className="font-medium">{dd(config?.branch_naming_pattern)}</dd>
          </div>
          <div>
            <dt className="text-xs text-muted-foreground">Target branch</dt>
            <dd className="font-medium">{dd(config?.target_branch)}</dd>
          </div>
          <div>
            <dt className="text-xs text-muted-foreground">Connected repository</dt>
            <dd className="font-medium">{config?.repository_id ? "Connected" : "Not yet connected"}</dd>
          </div>
        </dl>
      }
    >
      <div className="flex flex-col gap-3">
        <Select value={option} onChange={(e) => setOption(e.target.value as ApiGithubSetupOption)}>
          {GITHUB_OPTIONS.map((opt) => (
            <option key={opt.value} value={opt.value}>
              {opt.label}
            </option>
          ))}
        </Select>
        {option === "CREATE_NEW_REPO" ? (
          <div>
            <label className="mb-1 block text-xs font-medium">New repository name</label>
            <Input value={newRepoName} onChange={(e) => setNewRepoName(e.target.value)} />
          </div>
        ) : null}
        {option !== "SKIP_FOR_NOW" ? (
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="mb-1 block text-xs font-medium">Branch naming pattern</label>
              <Input value={branchNamingPattern} onChange={(e) => setBranchNamingPattern(e.target.value)} />
            </div>
            <div>
              <label className="mb-1 block text-xs font-medium">Target branch</label>
              <Input value={targetBranch} onChange={(e) => setTargetBranch(e.target.value)} />
            </div>
          </div>
        ) : null}
      </div>
    </SectionCard>
  );
}

// --- Jira Setup (intent only) --------------------------------------------------------------

function JiraSection({ projectId, setup, currentUserId, onSaved }: SectionProps) {
  const config = setup.jira_config;
  const [editing, setEditing] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [option, setOption] = useState<ApiJiraSetupOption>(config?.option ?? "SKIP_FOR_NOW");

  function startEdit() {
    setOption(config?.option ?? "SKIP_FOR_NOW");
    setError(null);
    setEditing(true);
  }

  async function save() {
    if (currentUserId === null) {
      setError("No users exist yet to attribute this change to.");
      return;
    }
    setSaving(true);
    setError(null);
    try {
      const updated = await api.engineeringSetup.update(projectId, { updated_by_id: currentUserId, jira: { option } });
      onSaved(updated);
      setEditing(false);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to save the Jira setup.");
    } finally {
      setSaving(false);
    }
  }

  return (
    <SectionCard
      title="Jira Setup"
      description="Setup intent only — actually connecting a Jira project still happens in Settings → Integrations → Jira."
      editing={editing}
      onEdit={startEdit}
      onCancel={() => setEditing(false)}
      onSave={save}
      saving={saving}
      error={error}
      viewContent={
        <dl className="grid grid-cols-2 gap-x-4 gap-y-2 text-sm sm:grid-cols-3">
          <div>
            <dt className="text-xs text-muted-foreground">Option</dt>
            <dd className="font-medium">{JIRA_OPTIONS.find((o) => o.value === config?.option)?.label ?? "—"}</dd>
          </div>
          <div>
            <dt className="text-xs text-muted-foreground">Connected Jira project</dt>
            <dd className="font-medium">{config?.jira_project_link_id ? "Connected" : "Not yet connected"}</dd>
          </div>
        </dl>
      }
    >
      <Select value={option} onChange={(e) => setOption(e.target.value as ApiJiraSetupOption)}>
        {JIRA_OPTIONS.map((opt) => (
          <option key={opt.value} value={opt.value}>
            {opt.label}
          </option>
        ))}
      </Select>
    </SectionCard>
  );
}

// --- Coding Standards (full-list replace on save) ------------------------------------------

interface CodingStandardDraft {
  title: string;
  content: string;
  category: ApiCodingStandardCategory;
}

function CodingStandardsSection({ projectId, setup, currentUserId, onSaved }: SectionProps) {
  const [editing, setEditing] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [drafts, setDrafts] = useState<CodingStandardDraft[]>([]);

  function startEdit() {
    setDrafts(setup.coding_standards.map((s) => ({ title: s.title, content: s.content, category: s.category })));
    setError(null);
    setEditing(true);
  }

  async function save() {
    if (currentUserId === null) {
      setError("No users exist yet to attribute this change to.");
      return;
    }
    const valid = drafts.filter((s) => s.title.trim() && s.content.trim());
    setSaving(true);
    setError(null);
    try {
      const updated = await api.engineeringSetup.update(projectId, {
        updated_by_id: currentUserId,
        coding_standards: valid.map((s) => ({ title: s.title.trim(), content: s.content.trim(), category: s.category })),
      });
      onSaved(updated);
      setEditing(false);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to save coding standards.");
    } finally {
      setSaving(false);
    }
  }

  return (
    <SectionCard
      title="Coding Standards"
      description="Injected into every agent's context — not just semantically retrieved."
      editing={editing}
      onEdit={startEdit}
      onCancel={() => setEditing(false)}
      onSave={save}
      saving={saving}
      error={error}
      viewContent={
        setup.coding_standards.length === 0 ? (
          <p className="text-xs text-muted-foreground">None configured.</p>
        ) : (
          <div className="flex flex-col gap-2">
            {setup.coding_standards.map((s) => (
              <div key={s.id} className="rounded-md border border-border p-3">
                <div className="mb-1 flex items-center gap-2">
                  <span className="text-sm font-medium">{s.title}</span>
                  <Badge variant="outline">{CODING_STANDARD_CATEGORY_OPTIONS.find((o) => o.value === s.category)?.label ?? s.category}</Badge>
                </div>
                <p className="whitespace-pre-wrap text-xs text-muted-foreground">{s.content}</p>
              </div>
            ))}
          </div>
        )
      }
    >
      <div className="flex flex-col gap-2">
        {drafts.map((s, i) => (
          <div key={i} className="flex flex-col gap-2 rounded-md border border-border p-3">
            <div className="flex items-center gap-2">
              <Input
                value={s.title}
                onChange={(e) => setDrafts((prev) => prev.map((d, idx) => (idx === i ? { ...d, title: e.target.value } : d)))}
                placeholder="e.g. Naming conventions"
                className="flex-1"
              />
              <Select
                value={s.category}
                onChange={(e) =>
                  setDrafts((prev) => prev.map((d, idx) => (idx === i ? { ...d, category: e.target.value as ApiCodingStandardCategory } : d)))
                }
                className="w-40"
              >
                {CODING_STANDARD_CATEGORY_OPTIONS.map((opt) => (
                  <option key={opt.value} value={opt.value}>
                    {opt.label}
                  </option>
                ))}
              </Select>
              <Button type="button" variant="ghost" size="icon" onClick={() => setDrafts((prev) => prev.filter((_, idx) => idx !== i))}>
                <Trash2 className="h-3.5 w-3.5" />
              </Button>
            </div>
            <Textarea
              value={s.content}
              onChange={(e) => setDrafts((prev) => prev.map((d, idx) => (idx === i ? { ...d, content: e.target.value } : d)))}
              rows={2}
            />
          </div>
        ))}
        <Button
          type="button"
          variant="outline"
          size="sm"
          className="w-fit"
          onClick={() => setDrafts((prev) => [...prev, { title: "", content: "", category: "GENERAL" }])}
        >
          <Plus className="h-3.5 w-3.5" />
          Add coding standard
        </Button>
      </div>
    </SectionCard>
  );
}

// --- Agent Guardrails (full-list replace on save) -------------------------------------------

function GuardrailsSection({ projectId, setup, currentUserId, onSaved }: SectionProps) {
  const [editing, setEditing] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [drafts, setDrafts] = useState<string[]>([]);

  function startEdit() {
    setDrafts(setup.guardrails.map((g) => g.rule_text));
    setError(null);
    setEditing(true);
  }

  async function save() {
    if (currentUserId === null) {
      setError("No users exist yet to attribute this change to.");
      return;
    }
    const valid = drafts.map((g) => g.trim()).filter((g) => g.length > 0);
    setSaving(true);
    setError(null);
    try {
      const updated = await api.engineeringSetup.update(projectId, {
        updated_by_id: currentUserId,
        guardrails: valid.map((rule_text) => ({ rule_text })),
      });
      onSaved(updated);
      setEditing(false);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to save guardrails.");
    } finally {
      setSaving(false);
    }
  }

  return (
    <SectionCard
      title="Agent Guardrails"
      description="Rules every agent must follow — always injected verbatim into agent context."
      editing={editing}
      onEdit={startEdit}
      onCancel={() => setEditing(false)}
      onSave={save}
      saving={saving}
      error={error}
      viewContent={
        setup.guardrails.length === 0 ? (
          <p className="text-xs text-muted-foreground">None configured.</p>
        ) : (
          <ul className="flex flex-col gap-1 text-sm">
            {setup.guardrails.map((g) => (
              <li key={g.id}>- {g.rule_text}</li>
            ))}
          </ul>
        )
      }
    >
      <div className="flex flex-col gap-2">
        {drafts.map((g, i) => (
          <div key={i} className="flex items-center gap-2">
            <Input
              value={g}
              onChange={(e) => setDrafts((prev) => prev.map((d, idx) => (idx === i ? e.target.value : d)))}
              placeholder="e.g. Always add a test for a new endpoint"
              className="flex-1"
            />
            <Button type="button" variant="ghost" size="icon" onClick={() => setDrafts((prev) => prev.filter((_, idx) => idx !== i))}>
              <Trash2 className="h-3.5 w-3.5" />
            </Button>
          </div>
        ))}
        <Button type="button" variant="outline" size="sm" className="w-fit" onClick={() => setDrafts((prev) => [...prev, ""])}>
          <Plus className="h-3.5 w-3.5" />
          Add guardrail
        </Button>
      </div>
    </SectionCard>
  );
}

// --- Documentation Flow ----------------------------------------------------------------------

function DocumentationSection({ projectId, setup, currentUserId, onSaved }: SectionProps) {
  const [editing, setEditing] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [target, setTarget] = useState<ApiDocumentationTarget>(setup.documentation_config?.target ?? "INTERNAL_ONLY");

  function startEdit() {
    setTarget(setup.documentation_config?.target ?? "INTERNAL_ONLY");
    setError(null);
    setEditing(true);
  }

  async function save() {
    if (currentUserId === null) {
      setError("No users exist yet to attribute this change to.");
      return;
    }
    setSaving(true);
    setError(null);
    try {
      const updated = await api.engineeringSetup.update(projectId, { updated_by_id: currentUserId, documentation: { target } });
      onSaved(updated);
      setEditing(false);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to save the documentation flow.");
    } finally {
      setSaving(false);
    }
  }

  return (
    <SectionCard
      title="Documentation Flow"
      editing={editing}
      onEdit={startEdit}
      onCancel={() => setEditing(false)}
      onSave={save}
      saving={saving}
      error={error}
      viewContent={
        <p className="text-sm font-medium">{DOCUMENTATION_OPTIONS.find((o) => o.value === setup.documentation_config?.target)?.label ?? "—"}</p>
      }
    >
      <Select value={target} onChange={(e) => setTarget(e.target.value as ApiDocumentationTarget)}>
        {DOCUMENTATION_OPTIONS.map((opt) => (
          <option key={opt.value} value={opt.value}>
            {opt.label}
          </option>
        ))}
      </Select>
    </SectionCard>
  );
}

// --- Build/Test Commands ----------------------------------------------------------------------

function CommandsSection({ projectId, setup, currentUserId, onSaved }: SectionProps) {
  const config = setup.command_config;
  const [editing, setEditing] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [buildCommand, setBuildCommand] = useState(config?.build_command ?? "");
  const [testCommandsText, setTestCommandsText] = useState((config?.test_commands ?? []).join("\n"));
  const [lintCommand, setLintCommand] = useState(config?.lint_command ?? "");

  function startEdit() {
    setBuildCommand(config?.build_command ?? "");
    setTestCommandsText((config?.test_commands ?? []).join("\n"));
    setLintCommand(config?.lint_command ?? "");
    setError(null);
    setEditing(true);
  }

  async function save() {
    if (currentUserId === null) {
      setError("No users exist yet to attribute this change to.");
      return;
    }
    const testCommands = testCommandsText
      .split("\n")
      .map((c) => c.trim())
      .filter((c) => c.length > 0);
    setSaving(true);
    setError(null);
    try {
      const updated = await api.engineeringSetup.update(projectId, {
        updated_by_id: currentUserId,
        commands: { build_command: buildCommand.trim() || null, test_commands: testCommands, lint_command: lintCommand.trim() || null },
      });
      onSaved(updated);
      setEditing(false);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to save build/test commands.");
    } finally {
      setSaving(false);
    }
  }

  return (
    <SectionCard
      title="Build/Test Commands"
      description="Required before CodeRunner can apply and test a patch. Each command is still checked against the server's allowlisted executables when it actually runs."
      editing={editing}
      onEdit={startEdit}
      onCancel={() => setEditing(false)}
      onSave={save}
      saving={saving}
      error={error}
      viewContent={
        <dl className="grid grid-cols-1 gap-2 text-sm sm:grid-cols-3">
          <div>
            <dt className="text-xs text-muted-foreground">Build command</dt>
            <dd className="font-medium">{dd(config?.build_command)}</dd>
          </div>
          <div>
            <dt className="text-xs text-muted-foreground">Test commands</dt>
            <dd className="font-medium">{config?.test_commands && config.test_commands.length > 0 ? config.test_commands.join(", ") : "—"}</dd>
          </div>
          <div>
            <dt className="text-xs text-muted-foreground">Lint command</dt>
            <dd className="font-medium">{dd(config?.lint_command)}</dd>
          </div>
        </dl>
      }
    >
      <div className="flex flex-col gap-3">
        <div>
          <label className="mb-1 block text-xs font-medium">Build command</label>
          <Input value={buildCommand} onChange={(e) => setBuildCommand(e.target.value)} placeholder="e.g. npm run build" />
        </div>
        <div>
          <label className="mb-1 block text-xs font-medium">Test commands (one per line)</label>
          <Textarea value={testCommandsText} onChange={(e) => setTestCommandsText(e.target.value)} rows={3} placeholder="npm test" />
        </div>
        <div>
          <label className="mb-1 block text-xs font-medium">Lint command</label>
          <Input value={lintCommand} onChange={(e) => setLintCommand(e.target.value)} placeholder="e.g. npm run lint" />
        </div>
      </div>
    </SectionCard>
  );
}
