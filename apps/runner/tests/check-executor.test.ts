import { mkdtemp, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";

import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { executeRequiredChecks, type CheckExecutionOptions, type CheckResult } from "../src/runtime/check-executor.js";
import type { RequiredCheck } from "../src/contracts/work-packet.js";

function check(overrides: Partial<RequiredCheck>): RequiredCheck {
  return { name: "test-check", description: "A check.", severity: "BLOCKING", command: null, ...overrides };
}

/** A single-check convenience wrapper — executeRequiredChecks always
 * returns an array (it runs a whole list), but every test below except
 * the last exercises exactly one check; this keeps each test's own
 * result access a plain, non-optional value instead of an array index
 * every call site would otherwise need to assert past. */
async function runOne(reqCheck: RequiredCheck, opts: CheckExecutionOptions): Promise<CheckResult> {
  const results = await executeRequiredChecks([reqCheck], opts);
  if (results.length !== 1) throw new Error(`Expected exactly one result, got ${results.length}.`);
  return results[0]!;
}

describe("executeRequiredChecks — real, machine-verified execution", () => {
  let cwd: string;

  beforeEach(async () => {
    cwd = await mkdtemp(path.join(tmpdir(), "runner-check-exec-"));
  });

  afterEach(async () => {
    // Windows-specific: a just-SIGKILLed child process can hold its cwd
    // briefly after the "close" event fires (async OS-level teardown,
    // not this module's own bug — spawnBounded already correctly
    // resolved TIMED_OUT before this runs) — retry past the transient
    // EBUSY/ENOTEMPTY instead of asserting cleanup within the callee's
    // own control.
    await rm(cwd, { recursive: true, force: true, maxRetries: 5, retryDelay: 200 });
  });

  it("reports NOT_RUN when a check has no command configured", async () => {
    const result = await runOne(check({ command: null }), { cwd, deniedCommandPatterns: [], timeoutSecondsPerCheck: 5 });
    expect(result.status).toBe("NOT_RUN");
    expect(result.command.real_execution).toBe(false);
  });

  it("runs a real command and reports PASSED with real_execution: true", async () => {
    // node -e is portable across the CI/dev machines this repo already
    // runs Node on — no reliance on any project-specific tool being
    // installed, unlike npm/pytest, which this same executor also
    // supports (see command-guard.test.ts's own "npm test" example).
    const result = await runOne(check({ command: `node -e "console.log('ok')"` }), { cwd, deniedCommandPatterns: [], timeoutSecondsPerCheck: 10 });
    expect(result.status).toBe("PASSED");
    expect(result.command.real_execution).toBe(true);
    expect(result.command.exit_code).toBe(0);
    expect(result.command.duration_seconds).toBeGreaterThanOrEqual(0);
  });

  it("reports FAILED with a non-zero real exit code, never fabricated", async () => {
    const result = await runOne(check({ command: `node -e "process.exit(7)"` }), { cwd, deniedCommandPatterns: [], timeoutSecondsPerCheck: 10 });
    expect(result.status).toBe("FAILED");
    expect(result.command.exit_code).toBe(7);
  });

  it("reports TIMED_OUT when a check exceeds its own timeout, and kills the process", async () => {
    const result = await runOne(check({ command: `node -e "setTimeout(() => {}, 30000)"` }), { cwd, deniedCommandPatterns: [], timeoutSecondsPerCheck: 1 });
    expect(result.status).toBe("TIMED_OUT");
  }, 10000);

  it("reports INFRA_ERROR for a genuinely unspawnable command, not FAILED", async () => {
    const result = await runOne(check({ command: "this-binary-does-not-exist-anywhere --flag" }), { cwd, deniedCommandPatterns: [], timeoutSecondsPerCheck: 5 });
    expect(result.status).toBe("INFRA_ERROR");
  });

  it("refuses a denied command as INFRA_ERROR, never even attempting to run it", async () => {
    const result = await runOne(check({ command: "sudo rm -rf /" }), { cwd, deniedCommandPatterns: [], timeoutSecondsPerCheck: 5 });
    expect(result.status).toBe("INFRA_ERROR");
    expect(result.command.real_execution).toBe(false);
  });

  it("denies a ProjectExecutionProfile-configured pattern too", async () => {
    const result = await runOne(check({ command: "npm run deploy:prod" }), { cwd, deniedCommandPatterns: ["deploy:prod"], timeoutSecondsPerCheck: 5 });
    expect(result.status).toBe("INFRA_ERROR");
  });

  it("bounds a very large stdout to the same max-length Python's own CommandEvidence enforces", async () => {
    const result = await runOne(check({ command: `node -e "process.stdout.write('x'.repeat(20000))"` }), { cwd, deniedCommandPatterns: [], timeoutSecondsPerCheck: 10 });
    expect(result.command.stdout_excerpt!.length).toBeLessThanOrEqual(8000 + "\n…(truncated)".length);
  });

  it("parses a real JUnit report when the caller supplies its path", async () => {
    const reportPath = path.join(cwd, "junit.xml");
    await writeFile(
      reportPath,
      `<testsuite><testcase classname="a.b" name="ok" time="0.01"/><testcase classname="a.b" name="broken" time="0.02"><failure message="x"/></testcase></testsuite>`,
    );
    const result = await runOne(check({ command: `node -e "process.exit(0)"` }), {
      cwd, deniedCommandPatterns: [], timeoutSecondsPerCheck: 10, junitReportPath: () => reportPath,
    });
    expect(result.testEvidence).toHaveLength(2);
    expect(result.testEvidence.find((t) => t.name === "a.b.ok")!.result).toBe("PASS");
    expect(result.testEvidence.find((t) => t.name === "a.b.broken")!.result).toBe("FAIL");
    expect(result.testEvidence.every((t) => t.real_execution)).toBe(true);
  });

  it("never runs checks after this one just because an earlier check failed", async () => {
    const results = await executeRequiredChecks(
      [check({ name: "first", command: `node -e "process.exit(1)"` }), check({ name: "second", command: `node -e "process.exit(0)"` })],
      { cwd, deniedCommandPatterns: [], timeoutSecondsPerCheck: 5 },
    );
    expect(results.map((r) => r.status)).toEqual(["FAILED", "PASSED"]);
  });
});
