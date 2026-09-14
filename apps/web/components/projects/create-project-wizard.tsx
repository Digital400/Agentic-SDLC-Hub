"use client";

import { useState, type FormEvent } from "react";
import { useRouter } from "next/navigation";
import { Check, Plus, Trash2 } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import { api, ApiCodingStandardCategory, ApiError } from "@/lib/api";
import type { WorkType } from "@/lib/types";

const STEPS = [
  "Basic Info",
  "Technology Stack",
  "GitHub Setup",
  "Jira Setup",
  "Coding Standards",
  "Knowledge Base",
  "Agent Guardrails",
  "Documentation Flow",
  "Build/Test Commands",
  "Review & Create",
] as const;

const WORK_TYPE_OPTIONS: { value: WorkType; label: string }[] = [
  { value: "NEW_PROJECT", label: "New Project" },
  { value: "EXISTING_PROJECT_FEATURE", label: "Existing Project — Feature" },
  { value: "BUG_FIX", label: "Bug Fix" },
  { value: "TECHNICAL_IMPROVEMENT", label: "Technical Improvement" },
];

interface CodingStandardDraft {
  title: string;
  content: string;
  category: ApiCodingStandardCategory;
}

interface KnowledgeItemDraft {
  title: string;
  category: string;
  content: string;
  sourceUrl: string;
}

const CODING_STANDARD_CATEGORY_OPTIONS: { value: ApiCodingStandardCategory; label: string }[] = [
  { value: "GENERAL", label: "General" },
  { value: "ARCHITECTURE", label: "Architecture" },
  { value: "SECURITY", label: "Security" },
  { value: "TESTING", label: "Testing" },
  { value: "GIT", label: "Git" },
  { value: "DOCUMENTATION", label: "Documentation" },
];

// Users must configure engineering setup — GitHub/Jira intent, coding
// standards, guardrails, documentation flow, and build/test commands —
// before Implementation/Jira Sync/CodeRunner can run against this
// project (see app/models/project_engineering_setup.py's gates). Nothing
// is persisted until Step 9's "Create Project" — POST /projects, then
// POST /projects/{id}/engineering-setup — so backing out mid-wizard never
// leaves a half-configured project behind.
export function CreateProjectWizard({ createdById }: { createdById: string | null }) {
  const router = useRouter();
  const [step, setStep] = useState(0);
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);

  // Step 1
  const [name, setName] = useState("");
  const [businessOwner, setBusinessOwner] = useState("");
  const [description, setDescription] = useState("");
  const [workType, setWorkType] = useState<WorkType>("NEW_PROJECT");

  // Step 2
  const [applicationType, setApplicationType] = useState("");
  const [primaryLanguage, setPrimaryLanguage] = useState("");
  const [frontendFramework, setFrontendFramework] = useState("");
  const [backendFramework, setBackendFramework] = useState("");
  const [database, setDatabase] = useState("");
  const [cloudProvider, setCloudProvider] = useState("");

  // Step 3
  const [githubOption, setGithubOption] = useState<"CONNECT_EXISTING_REPO" | "CREATE_NEW_REPO" | "SKIP_FOR_NOW">("SKIP_FOR_NOW");
  const [newRepoName, setNewRepoName] = useState("");
  const [branchNamingPattern, setBranchNamingPattern] = useState("agent/{task}-{run_id}");
  const [targetBranch, setTargetBranch] = useState("main");

  // Step 4
  const [jiraOption, setJiraOption] = useState<"CONNECT_EXISTING_PROJECT" | "MAPPING_ONLY" | "SKIP_FOR_NOW">("SKIP_FOR_NOW");

  // Step 5
  const [codingStandards, setCodingStandards] = useState<CodingStandardDraft[]>([]);

  // Step 6 — Knowledge Base: content is pasted directly here; sourceUrl
  // (e.g. a Confluence link) is recorded purely as a citation label —
  // nothing is fetched from it. Submitted after project creation via
  // api.knowledgeSources.createFromText, scoped to the new project (see
  // app/models/knowledge.py's KnowledgeSource.project_id).
  const [knowledgeItems, setKnowledgeItems] = useState<KnowledgeItemDraft[]>([]);

  // Step 7
  const [guardrails, setGuardrails] = useState<string[]>([]);

  // Step 7
  const [documentationTarget, setDocumentationTarget] = useState<"INTERNAL_ONLY" | "CONFLUENCE" | "REPO_MARKDOWN" | "CONFLUENCE_AND_REPO">("INTERNAL_ONLY");

  // Step 8
  const [buildCommand, setBuildCommand] = useState("");
  const [testCommandsText, setTestCommandsText] = useState("");
  const [lintCommand, setLintCommand] = useState("");

  const [step1Error, setStep1Error] = useState<string | null>(null);
  const [step2Error, setStep2Error] = useState<string | null>(null);

  function goNext() {
    if (step === 0) {
      if (name.trim() === "" || businessOwner.trim() === "") {
        setStep1Error("Project name and business owner are both required.");
        return;
      }
      setStep1Error(null);
    }
    if (step === 1) {
      if (applicationType.trim() === "" || primaryLanguage.trim() === "") {
        setStep2Error("Application type and primary language are both required.");
        return;
      }
      setStep2Error(null);
    }
    setStep((s) => Math.min(s + 1, STEPS.length - 1));
  }

  function goBack() {
    setStep((s) => Math.max(s - 1, 0));
  }

  function addCodingStandard() {
    setCodingStandards((prev) => [...prev, { title: "", content: "", category: "GENERAL" }]);
  }

  function updateCodingStandard(index: number, patch: Partial<CodingStandardDraft>) {
    setCodingStandards((prev) => prev.map((s, i) => (i === index ? { ...s, ...patch } : s)));
  }

  function removeCodingStandard(index: number) {
    setCodingStandards((prev) => prev.filter((_, i) => i !== index));
  }

  function addKnowledgeItem() {
    setKnowledgeItems((prev) => [...prev, { title: "", category: "", content: "", sourceUrl: "" }]);
  }

  function updateKnowledgeItem(index: number, patch: Partial<KnowledgeItemDraft>) {
    setKnowledgeItems((prev) => prev.map((k, i) => (i === index ? { ...k, ...patch } : k)));
  }

  function removeKnowledgeItem(index: number) {
    setKnowledgeItems((prev) => prev.filter((_, i) => i !== index));
  }

  function addGuardrail() {
    setGuardrails((prev) => [...prev, ""]);
  }

  function updateGuardrail(index: number, value: string) {
    setGuardrails((prev) => prev.map((g, i) => (i === index ? value : g)));
  }

  function removeGuardrail(index: number) {
    setGuardrails((prev) => prev.filter((_, i) => i !== index));
  }

  async function handleCreate(e: FormEvent) {
    e.preventDefault();
    if (createdById === null) {
      setSubmitError("No users exist yet to attribute this project to.");
      return;
    }
    setSubmitting(true);
    setSubmitError(null);
    try {
      const project = await api.projects.create({
        name: name.trim(),
        business_owner: businessOwner.trim(),
        description: description.trim() || undefined,
        created_by_id: createdById,
        work_type: workType,
      });

      const testCommands = testCommandsText
        .split("\n")
        .map((c) => c.trim())
        .filter((c) => c.length > 0);
      const validStandards = codingStandards.filter((s) => s.title.trim() && s.content.trim());
      const validGuardrails = guardrails.map((g) => g.trim()).filter((g) => g.length > 0);

      await api.engineeringSetup.create(project.id, {
        created_by_id: createdById,
        technology_stack: {
          application_type: applicationType.trim(),
          primary_language: primaryLanguage.trim(),
          frontend_framework: frontendFramework.trim() || null,
          backend_framework: backendFramework.trim() || null,
          database: database.trim() || null,
          cloud_provider: cloudProvider.trim() || null,
        },
        repository: {
          option: githubOption,
          new_repo_name: githubOption === "CREATE_NEW_REPO" ? newRepoName.trim() || null : null,
          branch_naming_pattern: branchNamingPattern.trim() || "agent/{task}-{run_id}",
          target_branch: targetBranch.trim() || "main",
        },
        jira: { option: jiraOption },
        coding_standards: validStandards.map((s) => ({ title: s.title.trim(), content: s.content.trim(), category: s.category })),
        guardrails: validGuardrails.map((rule_text) => ({ rule_text })),
        documentation: { target: documentationTarget },
        commands: { build_command: buildCommand.trim() || null, test_commands: testCommands, lint_command: lintCommand.trim() || null },
      });

      const validKnowledgeItems = knowledgeItems.filter((k) => k.title.trim() && k.content.trim());
      for (const item of validKnowledgeItems) {
        await api.knowledgeSources.createFromText({
          title: item.title.trim(),
          category: item.category.trim() || "General",
          content: item.content.trim(),
          uploaded_by_id: createdById,
          project_id: project.id,
          file_url: item.sourceUrl.trim() || null,
        });
      }

      router.push(`/projects/${project.id}`);
      router.refresh();
    } catch (err) {
      setSubmitError(err instanceof ApiError ? err.message : "Failed to create the project.");
      setSubmitting(false);
    }
  }

  return (
    <div className="flex flex-col gap-4">
      {/* Step indicator */}
      <div className="flex flex-wrap items-center gap-1.5 text-xs">
        {STEPS.map((label, i) => (
          <div key={label} className="flex items-center gap-1.5">
            <button
              type="button"
              onClick={() => i < step && setStep(i)}
              disabled={i > step}
              className={`flex h-6 items-center gap-1.5 rounded-full px-2.5 ${
                i === step
                  ? "bg-primary text-primary-foreground"
                  : i < step
                    ? "bg-muted text-foreground"
                    : "bg-muted/40 text-muted-foreground"
              }`}
            >
              {i < step ? <Check className="h-3 w-3" /> : <span>{i + 1}</span>}
              {label}
            </button>
            {i < STEPS.length - 1 ? <span className="text-muted-foreground">→</span> : null}
          </div>
        ))}
      </div>

      <Card className="max-w-2xl">
        <CardContent className="p-6">
          <form onSubmit={handleCreate} className="flex flex-col gap-4" noValidate>
            {step === 0 ? (
              <div className="flex flex-col gap-4">
                <div>
                  <h2 className="text-sm font-semibold">Step 1: Basic Info</h2>
                </div>
                <div>
                  <label className="mb-1 block text-sm font-medium">
                    Project name <span className="text-destructive">*</span>
                  </label>
                  <Input value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. Customer Loyalty Rewards Platform" />
                </div>
                <div>
                  <label className="mb-1 block text-sm font-medium">
                    Business owner <span className="text-destructive">*</span>
                  </label>
                  <Input value={businessOwner} onChange={(e) => setBusinessOwner(e.target.value)} placeholder="e.g. Marketing — Jordan Lee" />
                </div>
                <div>
                  <label className="mb-1 block text-sm font-medium">Description</label>
                  <Textarea value={description} onChange={(e) => setDescription(e.target.value)} rows={3} placeholder="What is this project for?" />
                </div>
                <div>
                  <label className="mb-1 block text-sm font-medium">
                    Work type <span className="text-destructive">*</span>
                  </label>
                  <Select value={workType} onChange={(e) => setWorkType(e.target.value as WorkType)}>
                    {WORK_TYPE_OPTIONS.map((opt) => (
                      <option key={opt.value} value={opt.value}>
                        {opt.label}
                      </option>
                    ))}
                  </Select>
                </div>
                {step1Error ? <p className="text-xs text-destructive">{step1Error}</p> : null}
              </div>
            ) : null}

            {step === 1 ? (
              <div className="flex flex-col gap-4">
                <h2 className="text-sm font-semibold">Step 2: Technology Stack</h2>
                <div>
                  <label className="mb-1 block text-sm font-medium">
                    Application type <span className="text-destructive">*</span>
                  </label>
                  <Input value={applicationType} onChange={(e) => setApplicationType(e.target.value)} placeholder="e.g. Web Application" />
                </div>
                <div>
                  <label className="mb-1 block text-sm font-medium">
                    Primary language <span className="text-destructive">*</span>
                  </label>
                  <Input value={primaryLanguage} onChange={(e) => setPrimaryLanguage(e.target.value)} placeholder="e.g. TypeScript" />
                </div>
                <div className="grid grid-cols-2 gap-3">
                  <div>
                    <label className="mb-1 block text-sm font-medium">Frontend framework</label>
                    <Input value={frontendFramework} onChange={(e) => setFrontendFramework(e.target.value)} placeholder="e.g. Next.js" />
                  </div>
                  <div>
                    <label className="mb-1 block text-sm font-medium">Backend framework</label>
                    <Input value={backendFramework} onChange={(e) => setBackendFramework(e.target.value)} placeholder="e.g. FastAPI" />
                  </div>
                  <div>
                    <label className="mb-1 block text-sm font-medium">Database</label>
                    <Input value={database} onChange={(e) => setDatabase(e.target.value)} placeholder="e.g. PostgreSQL" />
                  </div>
                  <div>
                    <label className="mb-1 block text-sm font-medium">Cloud provider</label>
                    <Input value={cloudProvider} onChange={(e) => setCloudProvider(e.target.value)} placeholder="e.g. AWS" />
                  </div>
                </div>
                {step2Error ? <p className="text-xs text-destructive">{step2Error}</p> : null}
              </div>
            ) : null}

            {step === 2 ? (
              <div className="flex flex-col gap-4">
                <h2 className="text-sm font-semibold">Step 3: GitHub Setup</h2>
                <p className="text-xs text-muted-foreground">
                  Code Implementation cannot run until a repository is actually connected — see Settings →
                  Integrations → GitHub once the project exists.
                </p>
                <div>
                  <Select value={githubOption} onChange={(e) => setGithubOption(e.target.value as typeof githubOption)}>
                    <option value="CONNECT_EXISTING_REPO">Connect an existing repository</option>
                    <option value="CREATE_NEW_REPO">Create a new repository</option>
                    <option value="SKIP_FOR_NOW">Skip for now</option>
                  </Select>
                </div>
                {githubOption === "CREATE_NEW_REPO" ? (
                  <div>
                    <label className="mb-1 block text-sm font-medium">New repository name</label>
                    <Input value={newRepoName} onChange={(e) => setNewRepoName(e.target.value)} placeholder="e.g. loyalty-rewards-api" />
                  </div>
                ) : null}
                {githubOption !== "SKIP_FOR_NOW" ? (
                  <div className="grid grid-cols-2 gap-3">
                    <div>
                      <label className="mb-1 block text-sm font-medium">Branch naming pattern</label>
                      <Input value={branchNamingPattern} onChange={(e) => setBranchNamingPattern(e.target.value)} />
                      <p className="mt-1 text-xs text-muted-foreground">Supports {"{task}"}, {"{run_id}"}, {"{story}"}.</p>
                    </div>
                    <div>
                      <label className="mb-1 block text-sm font-medium">Target branch</label>
                      <Input value={targetBranch} onChange={(e) => setTargetBranch(e.target.value)} />
                    </div>
                  </div>
                ) : null}
              </div>
            ) : null}

            {step === 3 ? (
              <div className="flex flex-col gap-4">
                <h2 className="text-sm font-semibold">Step 4: Jira Setup</h2>
                <p className="text-xs text-muted-foreground">
                  Jira Sync cannot run until a Jira project is actually connected — see Settings → Integrations →
                  Jira once the project exists.
                </p>
                <Select value={jiraOption} onChange={(e) => setJiraOption(e.target.value as typeof jiraOption)}>
                  <option value="CONNECT_EXISTING_PROJECT">Connect an existing Jira project</option>
                  <option value="MAPPING_ONLY">Field mapping only (connect later)</option>
                  <option value="SKIP_FOR_NOW">Skip for now</option>
                </Select>
              </div>
            ) : null}

            {step === 4 ? (
              <div className="flex flex-col gap-4">
                <h2 className="text-sm font-semibold">Step 5: Coding Standards</h2>
                <p className="text-xs text-muted-foreground">
                  Injected into every agent&apos;s context (summarized automatically if long) — not just
                  semantically retrieved. Categorize a standard to group it under the matching rule section
                  (architecture, security, testing, Git, documentation) an agent sees.
                </p>
                {codingStandards.map((s, i) => (
                  <div key={i} className="flex flex-col gap-2 rounded-md border border-border p-3">
                    <div className="flex items-center gap-2">
                      <Input
                        value={s.title}
                        onChange={(e) => updateCodingStandard(i, { title: e.target.value })}
                        placeholder="e.g. Naming conventions"
                        className="flex-1"
                      />
                      <Select
                        value={s.category}
                        onChange={(e) => updateCodingStandard(i, { category: e.target.value as ApiCodingStandardCategory })}
                        className="w-40"
                      >
                        {CODING_STANDARD_CATEGORY_OPTIONS.map((opt) => (
                          <option key={opt.value} value={opt.value}>
                            {opt.label}
                          </option>
                        ))}
                      </Select>
                      <Button type="button" variant="ghost" size="icon" onClick={() => removeCodingStandard(i)}>
                        <Trash2 className="h-3.5 w-3.5" />
                      </Button>
                    </div>
                    <Textarea
                      value={s.content}
                      onChange={(e) => updateCodingStandard(i, { content: e.target.value })}
                      placeholder="e.g. Use camelCase for variables, PascalCase for classes..."
                      rows={2}
                    />
                  </div>
                ))}
                <Button type="button" variant="outline" size="sm" className="w-fit" onClick={addCodingStandard}>
                  <Plus className="h-3.5 w-3.5" />
                  Add coding standard
                </Button>
              </div>
            ) : null}

            {step === 5 ? (
              <div className="flex flex-col gap-4">
                <h2 className="text-sm font-semibold">Step 6: Knowledge Base</h2>
                <p className="text-xs text-muted-foreground">
                  Optional context for agents drafting this project&apos;s artifacts — pasted directly, scoped
                  to just this project (on top of whatever&apos;s already shared org-wide). Note a source link
                  (e.g. a Confluence page) if you want it cited as a reference — nothing is fetched from that
                  link.
                </p>
                {knowledgeItems.map((k, i) => (
                  <div key={i} className="flex flex-col gap-2 rounded-md border border-border p-3">
                    <div className="flex items-center gap-2">
                      <Input
                        value={k.title}
                        onChange={(e) => updateKnowledgeItem(i, { title: e.target.value })}
                        placeholder="e.g. Habit streak calculation rules"
                        className="flex-1"
                      />
                      <Input
                        value={k.category}
                        onChange={(e) => updateKnowledgeItem(i, { category: e.target.value })}
                        placeholder="e.g. Product Domain"
                        className="w-44"
                      />
                      <Button type="button" variant="ghost" size="icon" onClick={() => removeKnowledgeItem(i)}>
                        <Trash2 className="h-3.5 w-3.5" />
                      </Button>
                    </div>
                    <Textarea
                      value={k.content}
                      onChange={(e) => updateKnowledgeItem(i, { content: e.target.value })}
                      placeholder="Paste the reference content here..."
                      rows={3}
                    />
                    <Input
                      value={k.sourceUrl}
                      onChange={(e) => updateKnowledgeItem(i, { sourceUrl: e.target.value })}
                      placeholder="Optional source link, e.g. a Confluence page URL"
                    />
                  </div>
                ))}
                <Button type="button" variant="outline" size="sm" className="w-fit" onClick={addKnowledgeItem}>
                  <Plus className="h-3.5 w-3.5" />
                  Add knowledge item
                </Button>
              </div>
            ) : null}

            {step === 6 ? (
              <div className="flex flex-col gap-4">
                <h2 className="text-sm font-semibold">Step 7: Agent Guardrails</h2>
                <p className="text-xs text-muted-foreground">
                  Rules every agent must follow — e.g. &ldquo;never touch payment code without human review.&rdquo;
                </p>
                {guardrails.map((g, i) => (
                  <div key={i} className="flex items-center gap-2">
                    <Input value={g} onChange={(e) => updateGuardrail(i, e.target.value)} placeholder="e.g. Always add a test for a new endpoint" className="flex-1" />
                    <Button type="button" variant="ghost" size="icon" onClick={() => removeGuardrail(i)}>
                      <Trash2 className="h-3.5 w-3.5" />
                    </Button>
                  </div>
                ))}
                <Button type="button" variant="outline" size="sm" className="w-fit" onClick={addGuardrail}>
                  <Plus className="h-3.5 w-3.5" />
                  Add guardrail
                </Button>
              </div>
            ) : null}

            {step === 7 ? (
              <div className="flex flex-col gap-4">
                <h2 className="text-sm font-semibold">Step 8: Documentation Flow</h2>
                <Select value={documentationTarget} onChange={(e) => setDocumentationTarget(e.target.value as typeof documentationTarget)}>
                  <option value="INTERNAL_ONLY">Internal only (this app)</option>
                  <option value="CONFLUENCE">Publish to Confluence</option>
                  <option value="REPO_MARKDOWN">Publish as Markdown in the repository</option>
                  <option value="CONFLUENCE_AND_REPO">Both Confluence and repository Markdown</option>
                </Select>
              </div>
            ) : null}

            {step === 8 ? (
              <div className="flex flex-col gap-4">
                <h2 className="text-sm font-semibold">Step 9: Build/Test Commands</h2>
                <p className="text-xs text-muted-foreground">
                  Required before CodeRunner can apply and test a patch. Each command is still checked against the
                  server&rsquo;s allowlisted executables when it actually runs.
                </p>
                <div>
                  <label className="mb-1 block text-sm font-medium">Build command</label>
                  <Input value={buildCommand} onChange={(e) => setBuildCommand(e.target.value)} placeholder="e.g. npm run build" />
                </div>
                <div>
                  <label className="mb-1 block text-sm font-medium">Test commands (one per line)</label>
                  <Textarea value={testCommandsText} onChange={(e) => setTestCommandsText(e.target.value)} rows={3} placeholder="npm test" />
                </div>
                <div>
                  <label className="mb-1 block text-sm font-medium">Lint command</label>
                  <Input value={lintCommand} onChange={(e) => setLintCommand(e.target.value)} placeholder="e.g. npm run lint" />
                </div>
              </div>
            ) : null}

            {step === 9 ? (
              <div className="flex flex-col gap-4">
                <h2 className="text-sm font-semibold">Step 10: Review & Create</h2>
                <dl className="grid grid-cols-1 gap-x-4 gap-y-2 text-xs sm:grid-cols-2">
                  <div>
                    <dt className="text-muted-foreground">Project</dt>
                    <dd className="font-medium">{name || "—"}</dd>
                  </div>
                  <div>
                    <dt className="text-muted-foreground">Work type</dt>
                    <dd className="font-medium">{WORK_TYPE_OPTIONS.find((o) => o.value === workType)?.label}</dd>
                  </div>
                  <div>
                    <dt className="text-muted-foreground">Stack</dt>
                    <dd className="font-medium">
                      {[applicationType, primaryLanguage, frontendFramework, backendFramework, database, cloudProvider].filter(Boolean).join(" · ") || "—"}
                    </dd>
                  </div>
                  <div>
                    <dt className="text-muted-foreground">GitHub</dt>
                    <dd className="font-medium">{githubOption.replace(/_/g, " ")}</dd>
                  </div>
                  <div>
                    <dt className="text-muted-foreground">Jira</dt>
                    <dd className="font-medium">{jiraOption.replace(/_/g, " ")}</dd>
                  </div>
                  <div>
                    <dt className="text-muted-foreground">Documentation</dt>
                    <dd className="font-medium">{documentationTarget.replace(/_/g, " ")}</dd>
                  </div>
                  <div className="sm:col-span-2">
                    <dt className="mb-1 text-muted-foreground">Coding standards</dt>
                    <dd className="flex flex-wrap gap-1">
                      {codingStandards.filter((s) => s.title.trim()).length > 0 ? (
                        codingStandards.filter((s) => s.title.trim()).map((s) => <Badge key={s.title} variant="outline">{s.title}</Badge>)
                      ) : (
                        <span className="text-muted-foreground">None</span>
                      )}
                    </dd>
                  </div>
                  <div className="sm:col-span-2">
                    <dt className="mb-1 text-muted-foreground">Knowledge base</dt>
                    <dd className="flex flex-wrap gap-1">
                      {knowledgeItems.filter((k) => k.title.trim() && k.content.trim()).length > 0 ? (
                        knowledgeItems
                          .filter((k) => k.title.trim() && k.content.trim())
                          .map((k) => <Badge key={k.title} variant="outline">{k.title}</Badge>)
                      ) : (
                        <span className="text-muted-foreground">None</span>
                      )}
                    </dd>
                  </div>
                  <div className="sm:col-span-2">
                    <dt className="mb-1 text-muted-foreground">Guardrails</dt>
                    <dd className="flex flex-col gap-0.5">
                      {guardrails.filter((g) => g.trim()).length > 0 ? (
                        guardrails.filter((g) => g.trim()).map((g) => <span key={g}>- {g}</span>)
                      ) : (
                        <span className="text-muted-foreground">None</span>
                      )}
                    </dd>
                  </div>
                  <div className="sm:col-span-2">
                    <dt className="text-muted-foreground">Build/test commands</dt>
                    <dd className="font-medium">
                      {[buildCommand, ...testCommandsText.split("\n").map((c) => c.trim()).filter(Boolean), lintCommand].filter(Boolean).join(", ") || "None"}
                    </dd>
                  </div>
                </dl>
                {submitError ? <p className="text-sm text-destructive">{submitError}</p> : null}
              </div>
            ) : null}

            <div className="mt-2 flex justify-between gap-2">
              <Button type="button" variant="outline" onClick={step === 0 ? () => router.push("/projects") : goBack}>
                {step === 0 ? "Cancel" : "Back"}
              </Button>
              {step < STEPS.length - 1 ? (
                <Button type="button" onClick={goNext}>
                  Next
                </Button>
              ) : (
                <Button type="submit" disabled={submitting}>
                  {submitting ? "Creating…" : "Create project"}
                </Button>
              )}
            </div>
          </form>
        </CardContent>
      </Card>
    </div>
  );
}
