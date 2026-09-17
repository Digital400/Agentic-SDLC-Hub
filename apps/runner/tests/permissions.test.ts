import { describe, expect, it } from "vitest";

import { loadRunnerConfig } from "../src/config.js";
import type { ProjectExecutionProfile, ToolPolicy } from "../src/contracts/work-packet.js";
import { buildOpenCodeConfig, ModelGatewayNotConfiguredError } from "../src/security/permissions.js";

function sampleProfile(overrides: Partial<ProjectExecutionProfile> = {}): ProjectExecutionProfile {
  return {
    id: "p1",
    project_id: "proj1",
    version: 1,
    status: "APPROVED",
    working_directories: ["."],
    package_manager: "npm",
    install_command: "npm install",
    lint_command: "npm run lint",
    format_check_command: null,
    type_check_command: null,
    unit_test_command: "npm test",
    integration_test_command: null,
    build_command: null,
    approved_security_scan_commands: [],
    allowed_command_patterns: [],
    denied_command_patterns: ["npm run deploy*"],
    allowed_paths: ["src/**"],
    denied_paths: [],
    network_policy: { default: "DENY" },
    environment_variable_names: [],
    required_coding_skills: [],
    required_testing_skills: [],
    data_classification: "INTERNAL",
    ...overrides,
  };
}

const toolPolicy: ToolPolicy = { allowed_tools: ["file_write"], denied_tools: [], deny_by_default: true, require_dry_run_first: false };

describe("buildOpenCodeConfig — 'Run OpenCode with explicit permissions'", () => {
  it("refuses to build a config when the model gateway is not configured (fail-closed)", () => {
    const runnerConfig = loadRunnerConfig({});
    expect(() =>
      buildOpenCodeConfig({ runnerConfig, profile: sampleProfile(), toolPolicy, modelAlias: "coding-standard" }),
    ).toThrow(ModelGatewayNotConfiguredError);
  });

  it("denies network access by default", () => {
    const runnerConfig = loadRunnerConfig({ MODEL_GATEWAY_BASE_URL: "https://gateway.internal" });
    const config = buildOpenCodeConfig({ runnerConfig, profile: sampleProfile(), toolPolicy, modelAlias: "coding-standard" });
    expect(config.permission?.webfetch).toBe("deny");
  });

  it("denies external directories", () => {
    const runnerConfig = loadRunnerConfig({ MODEL_GATEWAY_BASE_URL: "https://gateway.internal" });
    const config = buildOpenCodeConfig({ runnerConfig, profile: sampleProfile(), toolPolicy, modelAlias: "coding-standard" });
    expect(config.permission?.external_directory).toBe("deny");
  });

  it("configures no MCP servers (deny unapproved MCP tools)", () => {
    const runnerConfig = loadRunnerConfig({ MODEL_GATEWAY_BASE_URL: "https://gateway.internal" });
    const config = buildOpenCodeConfig({ runnerConfig, profile: sampleProfile(), toolPolicy, modelAlias: "coding-standard" });
    expect(config.mcp).toEqual({});
  });

  it("routes the model through the company gateway's baseURL, never a public provider", () => {
    const runnerConfig = loadRunnerConfig({ MODEL_GATEWAY_BASE_URL: "https://gateway.internal", MODEL_GATEWAY_API_KEY: "secret-key" });
    const config = buildOpenCodeConfig({ runnerConfig, profile: sampleProfile(), toolPolicy, modelAlias: "coding-standard" });
    const provider = config.provider?.["company-model-gateway"];
    expect(provider?.options?.baseURL).toBe("https://gateway.internal");
    expect(config.model).toBe("company-model-gateway/coding-standard");
  });

  it("only allows bash commands the ProjectExecutionProfile explicitly approved, denying everything else by default", () => {
    const runnerConfig = loadRunnerConfig({ MODEL_GATEWAY_BASE_URL: "https://gateway.internal" });
    const config = buildOpenCodeConfig({ runnerConfig, profile: sampleProfile(), toolPolicy, modelAlias: "coding-standard" });
    const bash = config.permission?.bash as Record<string, string>;
    expect(bash["*"]).toBe("deny");
    expect(bash["npm install"]).toBe("allow");
    expect(bash["npm test"]).toBe("allow");
    expect(bash["npm run deploy*"]).toBe("deny");
  });
});
