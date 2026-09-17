import { describe, expect, it } from "vitest";

import { loadRunnerConfig } from "../src/config.js";
import { buildRuntimeRegistry, getEnabledAdapter, RuntimeDisabledError, UnknownRuntimeError } from "../src/registry.js";
import { OpenCodeRuntimeAdapter } from "../src/runtime/opencode-adapter.js";

describe("runtime registry — 'Register OpenCode as an available but feature-flagged runtime'", () => {
  it("registers opencode even when the feature flag is off", () => {
    const registry = buildRuntimeRegistry(loadRunnerConfig({}));
    expect(registry.opencode).toBeDefined();
    expect(registry.opencode!.enabled).toBe(false);
  });

  it("marks opencode enabled once OPENCODE_RUNTIME_ENABLED=true", () => {
    const registry = buildRuntimeRegistry(loadRunnerConfig({ OPENCODE_RUNTIME_ENABLED: "true" }));
    expect(registry.opencode!.enabled).toBe(true);
  });

  it("refuses to hand out a runnable adapter while the flag is off", () => {
    expect(() => getEnabledAdapter("opencode", loadRunnerConfig({}))).toThrow(RuntimeDisabledError);
  });

  it("hands out a real OpenCodeRuntimeAdapter once enabled", () => {
    const adapter = getEnabledAdapter("opencode", loadRunnerConfig({ OPENCODE_RUNTIME_ENABLED: "true" }));
    expect(adapter).toBeInstanceOf(OpenCodeRuntimeAdapter);
    expect(adapter.runtimeName).toBe("opencode");
  });

  it("throws UnknownRuntimeError for a name nothing registered", () => {
    expect(() => getEnabledAdapter("claude-code", loadRunnerConfig({}))).toThrow(UnknownRuntimeError);
  });
});
