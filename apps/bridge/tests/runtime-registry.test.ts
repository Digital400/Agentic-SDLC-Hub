import { describe, expect, it } from "vitest";

import { LocalRuntimeNotApprovedError, LocalRuntimeRegistry, parseApprovedLocalRuntimes } from "../src/runtime-registry.js";

describe("parseApprovedLocalRuntimes", () => {
  it("returns an empty list when unset", () => {
    expect(parseApprovedLocalRuntimes(undefined)).toEqual([]);
  });

  it("returns an empty list for malformed JSON, never throws", () => {
    expect(() => parseApprovedLocalRuntimes("{not json")).not.toThrow();
    expect(parseApprovedLocalRuntimes("{not json")).toEqual([]);
  });

  it("defaults protocol to acp and enabled to true", () => {
    const [runtime] = parseApprovedLocalRuntimes(JSON.stringify([{ key: "a", executablePath: "/bin/a" }]));
    expect(runtime!.protocol).toBe("acp");
    expect(runtime!.enabled).toBe(true);
  });

  it("skips an entry missing executablePath", () => {
    const runtimes = parseApprovedLocalRuntimes(JSON.stringify([{ key: "bad" }, { key: "good", executablePath: "/bin/good" }]));
    expect(runtimes).toHaveLength(1);
    expect(runtimes[0]!.key).toBe("good");
  });
});

describe("LocalRuntimeRegistry", () => {
  it("resolves an approved, enabled runtime", () => {
    const registry = new LocalRuntimeRegistry([{ key: "a", executablePath: "/bin/a", args: [], protocol: "acp", enabled: true }]);
    expect(registry.resolve("a").executablePath).toBe("/bin/a");
  });

  it("refuses an unknown key and a disabled key identically", () => {
    const registry = new LocalRuntimeRegistry([{ key: "disabled", executablePath: "/bin/a", args: [], protocol: "acp", enabled: false }]);
    expect(() => registry.resolve("unknown")).toThrow(LocalRuntimeNotApprovedError);
    expect(() => registry.resolve("disabled")).toThrow(LocalRuntimeNotApprovedError);
  });
});
