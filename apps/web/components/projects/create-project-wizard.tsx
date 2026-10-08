"use client";

import { useEffect, useRef, useState, type FormEvent } from "react";
import { useRouter } from "next/navigation";
import { Check, Loader2, Plus, Trash2 } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import {
  APPLICATION_TYPE_OPTIONS,
  BACKEND_FRAMEWORK_OPTIONS,
  CLOUD_PROVIDER_OPTIONS,
  DATABASE_OPTIONS,
  FRONTEND_FRAMEWORK_OPTIONS,
  TechStackSelect,
} from "@/components/projects/tech-stack-select";
import { api, ApiCodingStandardCategory, ApiError } from "@/lib/api";
import { toGitHubRepoOption, toGithubConnectionItem, toJiraConnectionItem } from "@/lib/mappers";
import type { GitHubRepoOption, GithubConnectionItem, JiraConnectionItem, WorkType } from "@/lib/types";

const STEPS = [
  "Basic Info",
  "Technology Stack",
  "GitHub Setup",
  "Jira Setup",
  "Coding Standards",
  "Knowledge Base",
  "Agent Guardrails",
  "Documentation Flow",
  "Review & Create",
] as const;

const WORK_TYPE_OPTIONS: { value: WorkType; label: string }[] = [
  { value: "NEW_PROJECT", label: "New Project" },
  { value: "EXISTING_PROJECT_FEATURE", label: "Existing Project — Feature" },
];

interface CodingStandardDraft {
  title: string;
  content: string;
  category: ApiCodingStandardCategory;
  sourceUrl: string;
}

interface KnowledgeItemDraft {
  title: string;
  category: string;
  content: string;
  sourceUrl: string;
}

const CODING_STANDARD_CATEGORY_OPTIONS: {
  value: ApiCodingStandardCategory;
  label: string;
}[] = [
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
export function CreateProjectWizard({
  createdById,
}: {
  createdById: string | null;
}) {
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
  // GitHub is mandatory — there is deliberately no "skip" option. The
  // repository is connected (or created) for real when the project is
  // created; see handleCreate.
  const [githubOption, setGithubOption] = useState<
    "CONNECT_EXISTING_REPO" | "CREATE_NEW_REPO"
  >("CONNECT_EXISTING_REPO");
  const [githubConnections, setGithubConnections] = useState<
    GithubConnectionItem[]
  >([]);
  const [connectionsLoading, setConnectionsLoading] = useState(false);
  const [connectionId, setConnectionId] = useState("");
  const [repoOptions, setRepoOptions] = useState<GitHubRepoOption[]>([]);
  const [repoOptionsLoading, setRepoOptionsLoading] = useState(false);
  const [repoOptionsError, setRepoOptionsError] = useState<string | null>(null);
  const [selectedRepoFullName, setSelectedRepoFullName] = useState("");
  const [newRepoDescription, setNewRepoDescription] = useState("");
  const [newRepoPrivate, setNewRepoPrivate] = useState(true);
  const [step3Error, setStep3Error] = useState<string | null>(null);
  // Live "does this repository name already exist?" check for
  // "Create a new repository".
  const [nameCheck, setNameCheck] = useState<
    | { state: "idle" }
    | { state: "checking" }
    | { state: "available"; name: string }
    | { state: "taken"; name: string }
    | { state: "error"; message: string }
  >({ state: "idle" });
  // Progress across a partially-failed submit, so pressing "Create Project"
  // again resumes instead of creating a duplicate project / GitHub repo.
  const progress = useRef<{
    projectId: string | null;
    setupSaved: boolean;
    remote: { owner: string; name: string } | null;
    repoLinked: boolean;
    jiraLinked: boolean;
    knowledgeDone: number;
  }>({ projectId: null, setupSaved: false, remote: null, repoLinked: false, jiraLinked: false, knowledgeDone: 0 });
  const [newRepoName, setNewRepoName] = useState("");
  const [branchNamingPattern, setBranchNamingPattern] = useState(
    "agent/{task}-{run_id}",
  );
  const [targetBranch, setTargetBranch] = useState("main");

  // Step 4
  // Uses a Jira account already connected in Settings → Integrations → Jira
  // (where the API token is entered); this step only links a Jira project
  // by key, the same as Settings does. "Field mapping only" was removed —
  // it recorded nothing usable and still blocked Jira Sync.
  const [jiraOption, setJiraOption] = useState<
    "CONNECT_EXISTING_PROJECT" | "SKIP_FOR_NOW"
  >("CONNECT_EXISTING_PROJECT");
  const [jiraConnections, setJiraConnections] = useState<JiraConnectionItem[]>(
    [],
  );
  const [jiraConnectionsLoading, setJiraConnectionsLoading] = useState(false);
  const [jiraConnectionId, setJiraConnectionId] = useState("");
  const [jiraProjectOptions, setJiraProjectOptions] = useState<
    { key: string; name: string }[]
  >([]);
  const [jiraProjectOptionsLoading, setJiraProjectOptionsLoading] =
    useState(false);
  const [jiraProjectOptionsError, setJiraProjectOptionsError] = useState<
    string | null
  >(null);
  const [jiraProjectKey, setJiraProjectKey] = useState("");
  // The picker covers the normal case; "Enter it manually" falls back to a
  // typed key when a project isn't listed (e.g. the account can't see it
  // yet) or the picker call itself failed.
  const [jiraManualEntry, setJiraManualEntry] = useState(false);
  const [step4Error, setStep4Error] = useState<string | null>(null);

  // Step 5
  const [codingStandards, setCodingStandards] = useState<CodingStandardDraft[]>(
    [],
  );

  // Step 6 — Knowledge Base: content is pasted directly here; sourceUrl
  // (e.g. a Confluence link) is recorded purely as a citation label —
  // nothing is fetched from it. Submitted after project creation via
  // api.knowledgeSources.createFromText, scoped to the new project (see
  // app/models/knowledge.py's KnowledgeSource.project_id).
  const [knowledgeItems, setKnowledgeItems] = useState<KnowledgeItemDraft[]>(
    [],
  );

  // Step 7
  const [guardrails, setGuardrails] = useState<string[]>([]);

  // Step 7
  const [documentationTarget, setDocumentationTarget] = useState<
    "INTERNAL_ONLY" | "CONFLUENCE" | "REPO_MARKDOWN" | "CONFLUENCE_AND_REPO"
  >("INTERNAL_ONLY");


  const [step1Error, setStep1Error] = useState<string | null>(null);
  const [step2Error, setStep2Error] = useState<string | null>(null);

  // Connected GitHub accounts come from Settings → Integrations → GitHub
  // (where the access token is entered); this step only uses them.
  function loadConnections() {
    setConnectionsLoading(true);
    api.github
      .listConnections()
      .then((rows) => {
        const connected = rows
          .map(toGithubConnectionItem)
          .filter((c) => c.status === "CONNECTED");
        setGithubConnections(connected);
        setConnectionId((prev) =>
          connected.some((c) => c.id === prev) ? prev : connected[0]?.id || "",
        );
      })
      .catch(() => setGithubConnections([]))
      .finally(() => setConnectionsLoading(false));
  }

  function loadJiraConnections() {
    setJiraConnectionsLoading(true);
    api.jira
      .listConnections()
      .then((rows) => {
        const connected = rows
          .map(toJiraConnectionItem)
          .filter((c) => c.status === "CONNECTED");
        setJiraConnections(connected);
        setJiraConnectionId((prev) =>
          connected.some((c) => c.id === prev) ? prev : connected[0]?.id || "",
        );
      })
      .catch(() => setJiraConnections([]))
      .finally(() => setJiraConnectionsLoading(false));
  }

  useEffect(() => {
    if (step === 2) loadConnections();
    if (step === 3) loadJiraConnections();
  }, [step]);

  // The chosen account's own repositories, for "Connect an existing repository".
  useEffect(() => {
    if (step !== 2 || githubOption !== "CONNECT_EXISTING_REPO" || !connectionId) {
      return;
    }
    let cancelled = false;
    setRepoOptionsLoading(true);
    setRepoOptionsError(null);
    api.github
      .listRepositoryOptions(connectionId)
      .then((rows) => {
        if (!cancelled) setRepoOptions(rows.map(toGitHubRepoOption));
      })
      .catch((err) => {
        if (cancelled) return;
        setRepoOptions([]);
        setRepoOptionsError(
          err instanceof ApiError
            ? err.message
            : "Failed to load this account's repositories.",
        );
      })
      .finally(() => {
        if (!cancelled) setRepoOptionsLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [step, githubOption, connectionId]);

  // The chosen Jira account's own projects, for the project picker.
  useEffect(() => {
    if (
      step !== 3 ||
      jiraOption !== "CONNECT_EXISTING_PROJECT" ||
      !jiraConnectionId
    ) {
      return;
    }
    let cancelled = false;
    setJiraProjectOptionsLoading(true);
    setJiraProjectOptionsError(null);
    api.jira
      .listProjectOptions(jiraConnectionId)
      .then((rows) => {
        if (cancelled) return;
        setJiraProjectOptions(rows);
        if (rows.length === 0) setJiraManualEntry(true);
      })
      .catch((err) => {
        if (cancelled) return;
        setJiraProjectOptions([]);
        setJiraManualEntry(true);
        setJiraProjectOptionsError(
          err instanceof ApiError
            ? err.message
            : "Failed to load this account's Jira projects — enter the project key manually.",
        );
      })
      .finally(() => {
        if (!cancelled) setJiraProjectOptionsLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [step, jiraOption, jiraConnectionId]);

  const newRepoNameValid = /^[A-Za-z0-9._-]{1,100}$/.test(newRepoName.trim());

  useEffect(() => {
    if (
      step !== 2 ||
      githubOption !== "CREATE_NEW_REPO" ||
      !connectionId ||
      !newRepoNameValid
    ) {
      setNameCheck({ state: "idle" });
      return;
    }
    const candidate = newRepoName.trim();
    let cancelled = false;
    setNameCheck({ state: "checking" });
    const timer = setTimeout(() => {
      api.github
        .remoteRepositoryExists(connectionId, candidate)
        .then((r) => {
          if (!cancelled)
            setNameCheck({
              state: r.exists ? "taken" : "available",
              name: candidate,
            });
        })
        .catch((err) => {
          if (!cancelled)
            setNameCheck({
              state: "error",
              message:
                err instanceof ApiError
                  ? err.message
                  : "Could not check GitHub for this name.",
            });
        });
    }, 500);
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [step, githubOption, connectionId, newRepoName, newRepoNameValid]);

  async function goNext() {
    if (step === 0) {
      if (name.trim() === "" || businessOwner.trim() === "") {
        setStep1Error("Project name and business owner are both required.");
        return;
      }
      setStep1Error(null);
    }
    if (step === 1) {
      if (applicationType.trim() === "" || primaryLanguage.trim() === "") {
        setStep2Error(
          "Application type and primary language are both required.",
        );
        return;
      }
      setStep2Error(null);
    }
    if (step === 2) {
      if (!connectionId) {
        setStep3Error("Connect a GitHub account in Settings, then refresh, to continue.");
        return;
      }
      if (githubOption === "CONNECT_EXISTING_REPO" && !selectedRepoFullName) {
        setStep3Error("Choose the repository to connect.");
        return;
      }
      if (
        githubOption === "CREATE_NEW_REPO" &&
        !/^[A-Za-z0-9._-]{1,100}$/.test(newRepoName.trim())
      ) {
        setStep3Error(
          "Enter a repository name (letters, numbers, '.', '-' and '_' only).",
        );
        return;
      }
      if (githubOption === "CREATE_NEW_REPO") {
        // Authoritative re-check right before moving on, whatever the live
        // check last showed.
        try {
          const r = await api.github.remoteRepositoryExists(
            connectionId,
            newRepoName.trim(),
          );
          if (r.exists) {
            setNameCheck({ state: "taken", name: newRepoName.trim() });
            setStep3Error(
              `A repository named "${newRepoName.trim()}" already exists on your GitHub account — choose a different name, or connect the existing one.`,
            );
            return;
          }
        } catch (err) {
          setStep3Error(
            err instanceof ApiError
              ? err.message
              : "Could not check GitHub for this repository name.",
          );
          return;
        }
      }
      setStep3Error(null);
    }
    if (step === 3 && jiraOption === "CONNECT_EXISTING_PROJECT") {
      if (!jiraConnectionId) {
        setStep4Error(
          "Connect Jira in Settings and refresh — or choose Skip for now.",
        );
        return;
      }
      if (!/^[A-Za-z][A-Za-z0-9_]*$/.test(jiraProjectKey.trim())) {
        setStep4Error("Enter the Jira project key, e.g. LOYAL.");
        return;
      }
      setStep4Error(null);
    }
    setStep((s) => Math.min(s + 1, STEPS.length - 1));
  }

  function goBack() {
    setStep((s) => Math.max(s - 1, 0));
  }

  function addCodingStandard() {
    setCodingStandards((prev) => [
      ...prev,
      { title: "", content: "", category: "GENERAL", sourceUrl: "" },
    ]);
  }

  function updateCodingStandard(
    index: number,
    patch: Partial<CodingStandardDraft>,
  ) {
    setCodingStandards((prev) =>
      prev.map((s, i) => (i === index ? { ...s, ...patch } : s)),
    );
  }

  function removeCodingStandard(index: number) {
    setCodingStandards((prev) => prev.filter((_, i) => i !== index));
  }

  function addKnowledgeItem() {
    setKnowledgeItems((prev) => [
      ...prev,
      { title: "", category: "", content: "", sourceUrl: "" },
    ]);
  }

  function updateKnowledgeItem(
    index: number,
    patch: Partial<KnowledgeItemDraft>,
  ) {
    setKnowledgeItems((prev) =>
      prev.map((k, i) => (i === index ? { ...k, ...patch } : k)),
    );
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
      const prog = progress.current;
      if (prog.projectId === null) {
        const created = await api.projects.create({
          name: name.trim(),
          business_owner: businessOwner.trim(),
          description: description.trim() || undefined,
          created_by_id: createdById,
          work_type: workType,
        });
        prog.projectId = created.id;
      }
      const project = { id: prog.projectId };

      const validStandards = codingStandards.filter(
        (s) => s.title.trim() && s.content.trim(),
      );
      const validGuardrails = guardrails
        .map((g) => g.trim())
        .filter((g) => g.length > 0);

      if (!prog.setupSaved) {
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
            new_repo_name:
              githubOption === "CREATE_NEW_REPO"
                ? newRepoName.trim() || null
                : null,
            branch_naming_pattern:
              branchNamingPattern.trim() || "agent/{task}-{run_id}",
            target_branch: targetBranch.trim() || "main",
          },
          jira: { option: jiraOption },
          coding_standards: validStandards.map((s) => ({
            title: s.title.trim(),
            content: s.content.trim(),
            category: s.category,
            source_url: s.sourceUrl.trim() || null,
          })),
          guardrails: validGuardrails.map((rule_text) => ({ rule_text })),
          documentation: { target: documentationTarget },
          // No build/test commands are collected at project creation; they
          // can still be set later from the project's engineering setup.
          commands: {
            build_command: null,
            test_commands: [],
            lint_command: null,
          },
        });
        prog.setupSaved = true;
      }

      // GitHub: create the repository first when asked to, then connect it
      // to the project and link it into the engineering setup.
      if (!prog.repoLinked) {
        if (githubOption === "CREATE_NEW_REPO") {
          if (prog.remote === null) {
            const remote = await api.github.createRemoteRepository(
              connectionId,
              {
                name: newRepoName.trim(),
                description: newRepoDescription.trim() || null,
                private: newRepoPrivate,
                actor_user_id: createdById,
              },
            );
            prog.remote = { owner: remote.owner, name: remote.name };
          }
        } else {
          const [owner, ...rest] = selectedRepoFullName.split("/");
          prog.remote = { owner, name: rest.join("/") };
        }
        const repository = await api.github.saveRepository({
          project_id: project.id,
          connection_id: connectionId,
          owner: prog.remote.owner,
          name: prog.remote.name,
        });
        await api.engineeringSetup.linkRepository(project.id, repository.id);
        prog.repoLinked = true;
      }

      if (jiraOption === "CONNECT_EXISTING_PROJECT" && !prog.jiraLinked) {
        const jiraLink = await api.jira.saveProject({
          project_id: project.id,
          connection_id: jiraConnectionId,
          jira_project_key: jiraProjectKey.trim().toUpperCase(),
        });
        await api.engineeringSetup.linkJiraProject(project.id, jiraLink.id);
        prog.jiraLinked = true;
      }

      const validKnowledgeItems = knowledgeItems.filter(
        (k) => k.title.trim() && k.content.trim(),
      );
      for (const item of validKnowledgeItems.slice(prog.knowledgeDone)) {
        await api.knowledgeSources.createFromText({
          title: item.title.trim(),
          category: item.category.trim() || "General",
          content: item.content.trim(),
          uploaded_by_id: createdById,
          project_id: project.id,
          file_url: item.sourceUrl.trim() || null,
        });
        prog.knowledgeDone += 1;
      }

      router.push(`/projects/${project.id}`);
      router.refresh();
    } catch (err) {
      setSubmitError(
        err instanceof ApiError ? err.message : "Failed to create the project.",
      );
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
            {i < STEPS.length - 1 ? (
              <span className="text-muted-foreground">→</span>
            ) : null}
          </div>
        ))}
      </div>

      <Card className="max-w-2xl">
        <CardContent className="p-6">
          <form
            onSubmit={handleCreate}
            className="flex flex-col gap-4"
            noValidate
          >
            {step === 0 ? (
              <div className="flex flex-col gap-4">
                <div>
                  <h2 className="text-sm font-semibold">Step 1: Basic Info</h2>
                </div>
                <div>
                  <label className="mb-1 block text-sm font-medium">
                    Project name <span className="text-destructive">*</span>
                  </label>
                  <Input
                    value={name}
                    onChange={(e) => setName(e.target.value)}
                    placeholder="e.g. Customer Loyalty Rewards Platform"
                  />
                </div>
                <div>
                  <label className="mb-1 block text-sm font-medium">
                    Business owner <span className="text-destructive">*</span>
                  </label>
                  <Input
                    value={businessOwner}
                    onChange={(e) => setBusinessOwner(e.target.value)}
                    placeholder="e.g. Marketing — Jordan Lee"
                  />
                </div>
                <div>
                  <label className="mb-1 block text-sm font-medium">
                    Description
                  </label>
                  <Textarea
                    value={description}
                    onChange={(e) => setDescription(e.target.value)}
                    rows={3}
                    placeholder="What is this project for?"
                  />
                </div>
                <div>
                  <label className="mb-1 block text-sm font-medium">
                    Work type <span className="text-destructive">*</span>
                  </label>
                  <Select
                    value={workType}
                    onChange={(e) => setWorkType(e.target.value as WorkType)}
                  >
                    {WORK_TYPE_OPTIONS.map((opt) => (
                      <option key={opt.value} value={opt.value}>
                        {opt.label}
                      </option>
                    ))}
                  </Select>
                </div>
                {step1Error ? (
                  <p className="text-xs text-destructive">{step1Error}</p>
                ) : null}
              </div>
            ) : null}

            {step === 1 ? (
              <div className="flex flex-col gap-4">
                <h2 className="text-sm font-semibold">
                  Step 2: Technology Stack
                </h2>
                <div>
                  <label className="mb-1 block text-sm font-medium">
                    Application type <span className="text-destructive">*</span>
                  </label>
                  <TechStackSelect
                    value={applicationType}
                    onChange={setApplicationType}
                    options={APPLICATION_TYPE_OPTIONS}
                    placeholder="Select application type…"
                  />
                </div>
                <div>
                  <label className="mb-1 block text-sm font-medium">
                    Primary languages{" "}
                    <span className="text-destructive">*</span>
                  </label>
                  <Textarea
                    value={primaryLanguage}
                    onChange={(e) => setPrimaryLanguage(e.target.value)}
                    placeholder="e.g. TypeScript, C#, HTML, CSS, SQL"
                    rows={3}
                    maxLength={100}
                  />
                </div>
                <div className="grid grid-cols-2 gap-3">
                  <div>
                    <label className="mb-1 block text-sm font-medium">
                      Frontend framework
                    </label>
                    <TechStackSelect
                    value={frontendFramework}
                    onChange={setFrontendFramework}
                    options={FRONTEND_FRAMEWORK_OPTIONS}
                    placeholder="Select frontend framework…"
                  />
                  </div>
                  <div>
                    <label className="mb-1 block text-sm font-medium">
                      Backend framework
                    </label>
                    <TechStackSelect
                    value={backendFramework}
                    onChange={setBackendFramework}
                    options={BACKEND_FRAMEWORK_OPTIONS}
                    placeholder="Select backend framework…"
                  />
                  </div>
                  <div>
                    <label className="mb-1 block text-sm font-medium">
                      Database
                    </label>
                    <TechStackSelect
                    value={database}
                    onChange={setDatabase}
                    options={DATABASE_OPTIONS}
                    placeholder="Select database…"
                  />
                  </div>
                  <div>
                    <label className="mb-1 block text-sm font-medium">
                      Cloud provider
                    </label>
                    <TechStackSelect
                    value={cloudProvider}
                    onChange={setCloudProvider}
                    options={CLOUD_PROVIDER_OPTIONS}
                    placeholder="Select cloud provider…"
                  />
                  </div>
                </div>
                {step2Error ? (
                  <p className="text-xs text-destructive">{step2Error}</p>
                ) : null}
              </div>
            ) : null}

            {step === 2 ? (
              <div className="flex flex-col gap-4">
                <h2 className="text-sm font-semibold">Step 3: GitHub Setup</h2>
                <p className="text-xs text-muted-foreground">
                  A GitHub repository is required. Using your connected GitHub
                  account, either pick an existing repository or create a new
                  one — it is linked to the project when you press Create
                  Project.
                </p>

                {connectionsLoading ? (
                  <p className="flex items-center gap-2 text-xs text-muted-foreground">
                    <Loader2 className="h-3 w-3 animate-spin" /> Loading
                    connected GitHub accounts…
                  </p>
                ) : null}

                {!connectionsLoading && githubConnections.length === 0 ? (
                  <div className="flex flex-col gap-2 rounded-md border p-3">
                    <p className="text-sm font-medium">
                      No GitHub account is connected yet
                    </p>
                    <p className="text-xs text-muted-foreground">
                      Connect your GitHub account with a personal access token
                      in Settings → Integrations → GitHub, then come back and
                      refresh. Your answers on this page are kept.
                    </p>
                    <div className="flex gap-2">
                      <a
                        href="/settings/integrations/github"
                        target="_blank"
                        rel="noreferrer"
                        className="inline-flex h-8 items-center rounded-md bg-primary px-3 text-xs font-medium text-primary-foreground"
                      >
                        Open GitHub settings
                      </a>
                      <Button
                        type="button"
                        size="sm"
                        variant="outline"
                        onClick={loadConnections}
                      >
                        Refresh
                      </Button>
                    </div>
                  </div>
                ) : null}

                {githubConnections.length > 0 ? (
                  <div>
                    <label className="mb-1 block text-sm font-medium">
                      Connected GitHub account
                    </label>
                    <Select
                      value={connectionId}
                      onChange={(e) => {
                        setConnectionId(e.target.value);
                        setSelectedRepoFullName("");
                      }}
                    >
                      {githubConnections.map((c) => (
                        <option key={c.id} value={c.id}>
                          {c.githubUsername ?? "GitHub account"} ({c.tokenHint})
                        </option>
                      ))}
                    </Select>
                  </div>
                ) : null}

                {connectionId ? (
                  <>
                    <div>
                      <Select
                        value={githubOption}
                        onChange={(e) => {
                          setGithubOption(e.target.value as typeof githubOption);
                          setStep3Error(null);
                        }}
                      >
                        <option value="CONNECT_EXISTING_REPO">
                          Connect an existing repository
                        </option>
                        <option value="CREATE_NEW_REPO">
                          Create a new repository
                        </option>
                      </Select>
                    </div>

                    {githubOption === "CONNECT_EXISTING_REPO" ? (
                      <div>
                        <label className="mb-1 block text-sm font-medium">
                          Repository <span className="text-destructive">*</span>
                        </label>
                        {repoOptionsLoading ? (
                          <p className="flex items-center gap-2 text-xs text-muted-foreground">
                            <Loader2 className="h-3 w-3 animate-spin" /> Loading
                            repositories…
                          </p>
                        ) : (
                          <Select
                            value={selectedRepoFullName}
                            onChange={(e) =>
                              setSelectedRepoFullName(e.target.value)
                            }
                          >
                            <option value="">Select a repository…</option>
                            {repoOptions.map((r) => (
                              <option key={r.fullName} value={r.fullName}>
                                {r.fullName}
                                {r.isPrivate ? " (private)" : ""}
                              </option>
                            ))}
                          </Select>
                        )}
                        {repoOptionsError ? (
                          <p className="mt-1 text-xs text-destructive">
                            {repoOptionsError}
                          </p>
                        ) : null}
                      </div>
                    ) : (
                      <div className="flex flex-col gap-3">
                        <div>
                          <label className="mb-1 block text-sm font-medium">
                            New repository name{" "}
                            <span className="text-destructive">*</span>
                          </label>
                          <Input
                            value={newRepoName}
                            onChange={(e) => setNewRepoName(e.target.value)}
                            placeholder="e.g. loyalty-rewards-api"
                          />
                          {newRepoName.trim() !== "" && !newRepoNameValid ? (
                            <p className="mt-1 text-xs text-destructive">
                              Use letters, numbers, &quot;.&quot;, &quot;-&quot; and &quot;_&quot; only.
                            </p>
                          ) : nameCheck.state === "checking" ? (
                            <p className="mt-1 flex items-center gap-1 text-xs text-muted-foreground">
                              <Loader2 className="h-3 w-3 animate-spin" />{" "}
                              Checking GitHub…
                            </p>
                          ) : nameCheck.state === "taken" ? (
                            <p className="mt-1 text-xs text-destructive">
                              &quot;{nameCheck.name}&quot; already exists on
                              your GitHub account. Choose a different name, or
                              switch to &quot;Connect an existing
                              repository&quot;.
                            </p>
                          ) : nameCheck.state === "available" ? (
                            <p className="mt-1 text-xs text-green-600">
                              &quot;{nameCheck.name}&quot; is available.
                            </p>
                          ) : nameCheck.state === "error" ? (
                            <p className="mt-1 text-xs text-destructive">
                              {nameCheck.message}
                            </p>
                          ) : null}
                        </div>
                        <div>
                          <label className="mb-1 block text-sm font-medium">
                            Description
                          </label>
                          <Input
                            value={newRepoDescription}
                            onChange={(e) =>
                              setNewRepoDescription(e.target.value)
                            }
                            placeholder="Optional"
                            maxLength={350}
                          />
                        </div>
                        <label className="flex items-center gap-2 text-sm">
                          <input
                            type="checkbox"
                            checked={newRepoPrivate}
                            onChange={(e) => setNewRepoPrivate(e.target.checked)}
                          />
                          Private repository
                        </label>
                        <p className="text-xs text-muted-foreground">
                          The repository is created empty on your GitHub
                          account when you press Create Project.
                        </p>
                      </div>
                    )}

                    <div className="grid grid-cols-2 gap-3">
                      <div>
                        <label className="mb-1 block text-sm font-medium">
                          Branch naming pattern
                        </label>
                        <Input
                          value={branchNamingPattern}
                          onChange={(e) => setBranchNamingPattern(e.target.value)}
                        />
                        <p className="mt-1 text-xs text-muted-foreground">
                          Supports {"{task}"}, {"{run_id}"}, {"{story}"}.
                        </p>
                      </div>
                      <div>
                        <label className="mb-1 block text-sm font-medium">
                          Target branch
                        </label>
                        <Input
                          value={targetBranch}
                          onChange={(e) => setTargetBranch(e.target.value)}
                        />
                      </div>
                    </div>
                  </>
                ) : null}

                {step3Error ? (
                  <p className="text-xs text-destructive">{step3Error}</p>
                ) : null}
              </div>
            ) : null}

            {step === 3 ? (
              <div className="flex flex-col gap-4">
                <h2 className="text-sm font-semibold">Step 4: Jira Setup</h2>
                <p className="text-xs text-muted-foreground">
                  Link this project to a Jira project using the Jira account
                  connected in Settings. The link is made when you press Create
                  Project.
                </p>
                <Select
                  value={jiraOption}
                  onChange={(e) => {
                    setJiraOption(e.target.value as typeof jiraOption);
                    setStep4Error(null);
                  }}
                >
                  <option value="CONNECT_EXISTING_PROJECT">
                    Connect a Jira project
                  </option>
                  <option value="SKIP_FOR_NOW">Skip for now</option>
                </Select>

                {jiraOption === "CONNECT_EXISTING_PROJECT" ? (
                  <>
                    {jiraConnectionsLoading ? (
                      <p className="flex items-center gap-2 text-xs text-muted-foreground">
                        <Loader2 className="h-3 w-3 animate-spin" /> Loading
                        connected Jira accounts…
                      </p>
                    ) : null}

                    {!jiraConnectionsLoading && jiraConnections.length === 0 ? (
                      <div className="flex flex-col gap-2 rounded-md border p-3">
                        <p className="text-sm font-medium">
                          No Jira account is connected yet
                        </p>
                        <p className="text-xs text-muted-foreground">
                          Connect Jira with your API token in Settings →
                          Integrations → Jira, then come back and refresh. Your
                          answers on this page are kept.
                        </p>
                        <div className="flex gap-2">
                          <a
                            href="/settings/integrations/jira"
                            target="_blank"
                            rel="noreferrer"
                            className="inline-flex h-8 items-center rounded-md bg-primary px-3 text-xs font-medium text-primary-foreground"
                          >
                            Open Jira settings
                          </a>
                          <Button
                            type="button"
                            size="sm"
                            variant="outline"
                            onClick={loadJiraConnections}
                          >
                            Refresh
                          </Button>
                        </div>
                      </div>
                    ) : null}

                    {jiraConnections.length > 0 ? (
                      <>
                        <div>
                          <label className="mb-1 block text-sm font-medium">
                            Connected Jira account
                          </label>
                          <Select
                            value={jiraConnectionId}
                            onChange={(e) => {
                              setJiraConnectionId(e.target.value);
                              setJiraProjectKey("");
                              setJiraProjectOptions([]);
                              setJiraManualEntry(false);
                            }}
                          >
                            {jiraConnections.map((c) => (
                              <option key={c.id} value={c.id}>
                                {c.email} — {c.baseUrl}
                              </option>
                            ))}
                          </Select>
                        </div>
                        <div>
                          <label className="mb-1 block text-sm font-medium">
                            Jira project{" "}
                            <span className="text-destructive">*</span>
                          </label>
                          {jiraProjectOptionsLoading ? (
                            <p className="flex items-center gap-2 text-xs text-muted-foreground">
                              <Loader2 className="h-3 w-3 animate-spin" />{" "}
                              Loading Jira projects…
                            </p>
                          ) : !jiraManualEntry ? (
                            <Select
                              value={jiraProjectKey}
                              onChange={(e) =>
                                setJiraProjectKey(e.target.value)
                              }
                            >
                              <option value="">Select a project…</option>
                              {jiraProjectOptions.map((p) => (
                                <option key={p.key} value={p.key}>
                                  {p.name} ({p.key})
                                </option>
                              ))}
                            </Select>
                          ) : (
                            <Input
                              value={jiraProjectKey}
                              onChange={(e) =>
                                setJiraProjectKey(e.target.value)
                              }
                              placeholder="e.g. LOYAL"
                            />
                          )}
                          {jiraProjectOptionsError ? (
                            <p className="mt-1 text-xs text-destructive">
                              {jiraProjectOptionsError}
                            </p>
                          ) : null}
                          {!jiraProjectOptionsLoading &&
                          jiraProjectOptions.length > 0 ? (
                            <button
                              type="button"
                              className="mt-1 text-xs text-primary underline"
                              onClick={() =>
                                setJiraManualEntry((v) => !v)
                              }
                            >
                              {jiraManualEntry
                                ? "Choose from the list instead"
                                : "Enter the project key manually"}
                            </button>
                          ) : null}
                          <p className="mt-1 text-xs text-muted-foreground">
                            The short prefix on your Jira issues (LOYAL-123 →
                            LOYAL). It is checked against Jira when the project
                            is created.
                          </p>
                        </div>
                      </>
                    ) : null}
                  </>
                ) : (
                  <p className="text-xs text-muted-foreground">
                    Jira Sync will stay unavailable until you link a Jira
                    project later in Settings → Integrations → Jira.
                  </p>
                )}

                {step4Error ? (
                  <p className="text-xs text-destructive">{step4Error}</p>
                ) : null}
              </div>
            ) : null}

            {step === 4 ? (
              <div className="flex flex-col gap-4">
                <h2 className="text-sm font-semibold">
                  Step 5: Coding Standards
                </h2>
                <p className="text-xs text-muted-foreground">
                  Injected into every agent&apos;s context (summarized
                  automatically if long) — not just semantically retrieved.
                  Categorize a standard to group it under the matching rule
                  section (architecture, security, testing, Git, documentation)
                  an agent sees.
                </p>
                {codingStandards.map((s, i) => (
                  <div
                    key={i}
                    className="flex flex-col gap-2 rounded-md border border-border p-3"
                  >
                    <div className="flex items-center gap-2">
                      <Input
                        value={s.title}
                        onChange={(e) =>
                          updateCodingStandard(i, { title: e.target.value })
                        }
                        placeholder="e.g. Naming conventions"
                        className="flex-1"
                      />
                      <Select
                        value={s.category}
                        onChange={(e) =>
                          updateCodingStandard(i, {
                            category: e.target
                              .value as ApiCodingStandardCategory,
                          })
                        }
                        className="w-40"
                      >
                        {CODING_STANDARD_CATEGORY_OPTIONS.map((opt) => (
                          <option key={opt.value} value={opt.value}>
                            {opt.label}
                          </option>
                        ))}
                      </Select>
                      <Button
                        type="button"
                        variant="ghost"
                        size="icon"
                        onClick={() => removeCodingStandard(i)}
                      >
                        <Trash2 className="h-3.5 w-3.5" />
                      </Button>
                    </div>
                    <Textarea
                      value={s.content}
                      onChange={(e) =>
                        updateCodingStandard(i, { content: e.target.value })
                      }
                      placeholder="e.g. Use camelCase for variables, PascalCase for classes..."
                      rows={2}
                    />
                    <Input
                      value={s.sourceUrl}
                      onChange={(e) =>
                        updateCodingStandard(i, { sourceUrl: e.target.value })
                      }
                      placeholder="Confluence or SharePoint link (optional) — https://…"
                    />
                    {s.sourceUrl.trim() !== "" &&
                    !/^https?:\/\//i.test(s.sourceUrl.trim()) ? (
                      <p className="text-xs text-destructive">
                        The link must start with http:// or https://
                      </p>
                    ) : null}
                  </div>
                ))}
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  className="w-fit"
                  onClick={addCodingStandard}
                >
                  <Plus className="h-3.5 w-3.5" />
                  Add coding standard
                </Button>
              </div>
            ) : null}

            {step === 5 ? (
              <div className="flex flex-col gap-4">
                <h2 className="text-sm font-semibold">
                  Step 6: Knowledge Base
                </h2>
                <p className="text-xs text-muted-foreground">
                  Optional context for agents drafting this project&apos;s
                  artifacts — pasted directly, scoped to just this project (on
                  top of whatever&apos;s already shared org-wide). Note a source
                  link (e.g. a Confluence page) if you want it cited as a
                  reference — nothing is fetched from that link.
                </p>
                {knowledgeItems.map((k, i) => (
                  <div
                    key={i}
                    className="flex flex-col gap-2 rounded-md border border-border p-3"
                  >
                    <div className="flex items-center gap-2">
                      <Input
                        value={k.title}
                        onChange={(e) =>
                          updateKnowledgeItem(i, { title: e.target.value })
                        }
                        placeholder="e.g. Habit streak calculation rules"
                        className="flex-1"
                      />
                      <Input
                        value={k.category}
                        onChange={(e) =>
                          updateKnowledgeItem(i, { category: e.target.value })
                        }
                        placeholder="e.g. Product Domain"
                        className="w-44"
                      />
                      <Button
                        type="button"
                        variant="ghost"
                        size="icon"
                        onClick={() => removeKnowledgeItem(i)}
                      >
                        <Trash2 className="h-3.5 w-3.5" />
                      </Button>
                    </div>
                    <Textarea
                      value={k.content}
                      onChange={(e) =>
                        updateKnowledgeItem(i, { content: e.target.value })
                      }
                      placeholder="Paste the reference content here..."
                      rows={3}
                    />
                    <Input
                      value={k.sourceUrl}
                      onChange={(e) =>
                        updateKnowledgeItem(i, { sourceUrl: e.target.value })
                      }
                      placeholder="Optional source link, e.g. a Confluence page URL"
                    />
                  </div>
                ))}
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  className="w-fit"
                  onClick={addKnowledgeItem}
                >
                  <Plus className="h-3.5 w-3.5" />
                  Add knowledge item
                </Button>
              </div>
            ) : null}

            {step === 6 ? (
              <div className="flex flex-col gap-4">
                <h2 className="text-sm font-semibold">
                  Step 7: Agent Guardrails
                </h2>
                <p className="text-xs text-muted-foreground">
                  Rules every agent must follow — e.g. &ldquo;never touch
                  payment code without human review.&rdquo;
                </p>
                {guardrails.map((g, i) => (
                  <div key={i} className="flex items-center gap-2">
                    <Input
                      value={g}
                      onChange={(e) => updateGuardrail(i, e.target.value)}
                      placeholder="e.g. Always add a test for a new endpoint"
                      className="flex-1"
                    />
                    <Button
                      type="button"
                      variant="ghost"
                      size="icon"
                      onClick={() => removeGuardrail(i)}
                    >
                      <Trash2 className="h-3.5 w-3.5" />
                    </Button>
                  </div>
                ))}
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  className="w-fit"
                  onClick={addGuardrail}
                >
                  <Plus className="h-3.5 w-3.5" />
                  Add guardrail
                </Button>
              </div>
            ) : null}

            {step === 7 ? (
              <div className="flex flex-col gap-4">
                <h2 className="text-sm font-semibold">
                  Step 8: Documentation Flow
                </h2>
                <Select
                  value={documentationTarget}
                  onChange={(e) =>
                    setDocumentationTarget(
                      e.target.value as typeof documentationTarget,
                    )
                  }
                >
                  <option value="INTERNAL_ONLY">
                    Internal only (this app)
                  </option>
                  <option value="CONFLUENCE">Publish to Confluence</option>
                  <option value="REPO_MARKDOWN">
                    Publish as Markdown in the repository
                  </option>
                  <option value="CONFLUENCE_AND_REPO">
                    Both Confluence and repository Markdown
                  </option>
                </Select>
              </div>
            ) : null}

            {step === 8 ? (
              <div className="flex flex-col gap-4">
                <h2 className="text-sm font-semibold">
                  Step 9: Review & Create
                </h2>
                <dl className="grid grid-cols-1 gap-x-4 gap-y-2 text-xs sm:grid-cols-2">
                  <div>
                    <dt className="text-muted-foreground">Project</dt>
                    <dd className="font-medium">{name || "—"}</dd>
                  </div>
                  <div>
                    <dt className="text-muted-foreground">Work type</dt>
                    <dd className="font-medium">
                      {
                        WORK_TYPE_OPTIONS.find((o) => o.value === workType)
                          ?.label
                      }
                    </dd>
                  </div>
                  <div>
                    <dt className="text-muted-foreground">Stack</dt>
                    <dd className="font-medium">
                      {[
                        applicationType,
                        primaryLanguage,
                        frontendFramework,
                        backendFramework,
                        database,
                        cloudProvider,
                      ]
                        .filter(Boolean)
                        .join(" · ") || "—"}
                    </dd>
                  </div>
                  <div>
                    <dt className="text-muted-foreground">GitHub</dt>
                    <dd className="font-medium">
                      {githubOption === "CREATE_NEW_REPO"
                        ? `Create new repository "${newRepoName.trim()}"`
                        : `Connect ${selectedRepoFullName}`}
                    </dd>
                  </div>
                  <div>
                    <dt className="text-muted-foreground">Jira</dt>
                    <dd className="font-medium">
                      {jiraOption === "CONNECT_EXISTING_PROJECT"
                        ? `Connect Jira project ${jiraProjectKey.trim().toUpperCase()}`
                        : "Skip for now"}
                    </dd>
                  </div>
                  <div>
                    <dt className="text-muted-foreground">Documentation</dt>
                    <dd className="font-medium">
                      {documentationTarget.replace(/_/g, " ")}
                    </dd>
                  </div>
                  <div className="sm:col-span-2">
                    <dt className="mb-1 text-muted-foreground">
                      Coding standards
                    </dt>
                    <dd className="flex flex-wrap gap-1">
                      {codingStandards.filter((s) => s.title.trim()).length >
                      0 ? (
                        codingStandards
                          .filter((s) => s.title.trim())
                          .map((s) => (
                            <Badge key={s.title} variant="outline">
                              {s.title}
                            </Badge>
                          ))
                      ) : (
                        <span className="text-muted-foreground">None</span>
                      )}
                    </dd>
                  </div>
                  <div className="sm:col-span-2">
                    <dt className="mb-1 text-muted-foreground">
                      Knowledge base
                    </dt>
                    <dd className="flex flex-wrap gap-1">
                      {knowledgeItems.filter(
                        (k) => k.title.trim() && k.content.trim(),
                      ).length > 0 ? (
                        knowledgeItems
                          .filter((k) => k.title.trim() && k.content.trim())
                          .map((k) => (
                            <Badge key={k.title} variant="outline">
                              {k.title}
                            </Badge>
                          ))
                      ) : (
                        <span className="text-muted-foreground">None</span>
                      )}
                    </dd>
                  </div>
                  <div className="sm:col-span-2">
                    <dt className="mb-1 text-muted-foreground">Guardrails</dt>
                    <dd className="flex flex-col gap-0.5">
                      {guardrails.filter((g) => g.trim()).length > 0 ? (
                        guardrails
                          .filter((g) => g.trim())
                          .map((g) => <span key={g}>- {g}</span>)
                      ) : (
                        <span className="text-muted-foreground">None</span>
                      )}
                    </dd>
                  </div>
                </dl>
                {submitError ? (
                  <p className="text-sm text-destructive">{submitError}</p>
                ) : null}
              </div>
            ) : null}

            <div className="mt-2 flex justify-between gap-2">
              <Button
                type="button"
                variant="outline"
                onClick={step === 0 ? () => router.push("/projects") : goBack}
              >
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
