import { describe, expect, it } from "vitest";

import { AcpAgentNotApprovedError, AcpAgentRegistry, parseApprovedAcpAgents } from "../src/security/acp-registry.js";

describe("parseApprovedAcpAgents — fail closed on bad config", () => {
  it("returns an empty list when the env var is unset", () => {
    expect(parseApprovedAcpAgents(undefined)).toEqual([]);
  });

  it("returns an empty list for malformed JSON, never throws", () => {
    expect(() => parseApprovedAcpAgents("not json")).not.toThrow();
    expect(parseApprovedAcpAgents("not json")).toEqual([]);
  });

  it("returns an empty list when the JSON is valid but not an array", () => {
    expect(parseApprovedAcpAgents(JSON.stringify({ key: "x" }))).toEqual([]);
  });

  it("skips an entry missing a required field, keeps the rest", () => {
    const agents = parseApprovedAcpAgents(
      JSON.stringify([{ key: "good", executablePath: "/usr/bin/good-agent", protocolVersion: 1 }, { key: "missing-path" }]),
    );
    expect(agents).toHaveLength(1);
    expect(agents[0]!.key).toBe("good");
  });

  it("defaults enabled to true and args to []", () => {
    const [agent] = parseApprovedAcpAgents(JSON.stringify([{ key: "a", executablePath: "/bin/a", protocolVersion: 1 }]));
    expect(agent!.enabled).toBe(true);
    expect(agent!.args).toEqual([]);
  });

  it("respects an explicit enabled: false", () => {
    const [agent] = parseApprovedAcpAgents(JSON.stringify([{ key: "a", executablePath: "/bin/a", protocolVersion: 1, enabled: false }]));
    expect(agent!.enabled).toBe(false);
  });
});

describe("AcpAgentRegistry — 'do not allow users to provide arbitrary executable paths'", () => {
  it("resolves an approved, enabled agent by its registry key", () => {
    const registry = new AcpAgentRegistry([{ key: "a", executablePath: "/bin/a", args: [], protocolVersion: 1, enabled: true }]);
    expect(registry.resolve("a").executablePath).toBe("/bin/a");
  });

  it("refuses an unknown key", () => {
    const registry = new AcpAgentRegistry([]);
    expect(() => registry.resolve("/some/arbitrary/path")).toThrow(AcpAgentNotApprovedError);
  });

  it("refuses a known-but-disabled agent, with the SAME error as an unknown one", () => {
    const registry = new AcpAgentRegistry([{ key: "disabled-agent", executablePath: "/bin/a", args: [], protocolVersion: 1, enabled: false }]);
    let unknownMessage = "";
    let disabledMessage = "";
    try {
      registry.resolve("nope");
    } catch (err) {
      unknownMessage = (err as Error).message;
    }
    try {
      registry.resolve("disabled-agent");
    } catch (err) {
      disabledMessage = (err as Error).message;
    }
    // Same shape for both — never leaks "known but disabled" vs. "never heard of it".
    expect(unknownMessage).toContain("is not an approved, enabled ACP agent");
    expect(disabledMessage).toContain("is not an approved, enabled ACP agent");
  });

  it("list() exposes every registered agent regardless of enabled state", () => {
    const registry = new AcpAgentRegistry([
      { key: "a", executablePath: "/bin/a", args: [], protocolVersion: 1, enabled: true },
      { key: "b", executablePath: "/bin/b", args: [], protocolVersion: 1, enabled: false },
    ]);
    expect(registry.list().map((a) => a.key).sort()).toEqual(["a", "b"]);
  });
});
