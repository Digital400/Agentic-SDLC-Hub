/**
 * Machine-verified check execution — Phase 09's "extend the runner to
 * execute only checks approved in ProjectExecutionProfile" (mirrored here
 * as WorkPacket.required_checks[].command — see work-packet.ts's
 * RequiredCheck, already carrying exactly that: name, severity, and an
 * optional command).
 *
 * MACHINE EVIDENCE, NOT INTERPRETATION: this module only ever runs a
 * command and records exactly what happened — exit code, timing, bounded
 * output. It never summarizes, explains, or judges a failure; an LLM may
 * do that AFTER execution, from this module's own CommandEvidence/
 * TestEvidence output, never before or instead of it (Phase 09's own "an
 * LLM may summarize failures only after execution" rule).
 *
 * HONESTY: every CommandEvidence/TestEvidence this module produces for a
 * command that actually ran carries `real_execution: true` — this runner
 * (unlike the rest of this codebase, per the Phase 00 baseline's
 * confirmed no-sandbox finding) genuinely spawns the command inside the
 * real ephemeral workspace. A check that never ran at all (no `command`
 * configured) is reported NOT_RUN, not silently omitted.
 *
 * "Never invent coverage values": this module does not parse or
 * summarize coverage reports at all — only their file path, if
 * configured, is carried through as a reference (see CheckResult.
 * coverageReportPath). A real number belongs to a future phase's real
 * parser, not a guess here.
 */

import { spawn } from "node:child_process";

import { CommandDeniedError, assertCommandAllowed } from "../security/command-guard.js";
import type { CommandEvidence, TestEvidence } from "../contracts/execution-result.js";
import type { RequiredCheck } from "../contracts/work-packet.js";
import { parseJUnitXml } from "./junit-parser.js";

export type CheckStatus = "NOT_RUN" | "PASSED" | "FAILED" | "TIMED_OUT" | "INFRA_ERROR";

export interface CheckResult {
  check: RequiredCheck;
  status: CheckStatus;
  command: CommandEvidence;
  testEvidence: TestEvidence[];
  /** A reference only — see module docstring's "never invent coverage values." */
  coverageReportPath?: string | null;
}

export interface CheckExecutionOptions {
  cwd: string;
  /** ProjectExecutionProfile.denied_command_patterns — see command-guard.ts. */
  deniedCommandPatterns: string[];
  timeoutSecondsPerCheck: number;
  /** Optional: where to look for a JUnit XML report this check may have
   * produced, if any — the caller (which knows the project's own test
   * tooling conventions) decides the path; this module never guesses one. */
  junitReportPath?: (check: RequiredCheck) => string | null;
  coverageReportPath?: (check: RequiredCheck) => string | null;
}

// Mirrors apps/api/app/agent_runtime/execution_result.py's
// CommandEvidence.stdout_excerpt/stderr_excerpt `max_length=8000` — kept
// identical so a bounded excerpt means the same thing on both sides.
const MAX_OUTPUT_EXCERPT_CHARS = 8000;

/** Runs every approved check in order, stopping at none of them — a
 * failed BLOCKING check does not prevent a later check from also
 * running; the caller (which knows RequiredCheck.severity) decides what
 * an overall FAILED status means for the WorkPacket as a whole. */
export async function executeRequiredChecks(checks: RequiredCheck[], opts: CheckExecutionOptions): Promise<CheckResult[]> {
  const results: CheckResult[] = [];
  for (const check of checks) {
    results.push(await runOneCheck(check, opts));
  }
  return results;
}

async function runOneCheck(check: RequiredCheck, opts: CheckExecutionOptions): Promise<CheckResult> {
  if (!check.command) {
    return {
      check, status: "NOT_RUN",
      command: { command: "(no command configured for this check)", real_execution: false },
      testEvidence: [],
    };
  }

  try {
    assertCommandAllowed(check.command, opts.deniedCommandPatterns);
  } catch (err) {
    if (err instanceof CommandDeniedError) {
      return {
        check, status: "INFRA_ERROR",
        command: { command: check.command, real_execution: false, stderr_excerpt: bound(err.message) },
        testEvidence: [],
      };
    }
    throw err;
  }

  const argv = tokenize(check.command);
  if (argv.length === 0) {
    return {
      check, status: "INFRA_ERROR",
      command: { command: check.command, real_execution: false, stderr_excerpt: "Command tokenized to nothing." },
      testEvidence: [],
    };
  }

  const startedAtMs = Date.now();
  const outcome = await spawnBounded(argv, opts.cwd, opts.timeoutSecondsPerCheck);
  const durationSeconds = (Date.now() - startedAtMs) / 1000;

  const commandEvidence: CommandEvidence = {
    command: check.command,
    real_execution: true,
    exit_code: outcome.kind === "exited" ? outcome.exitCode : null,
    duration_seconds: durationSeconds,
    stdout_excerpt: bound(outcome.stdout),
    stderr_excerpt: bound(outcome.stderr),
  };

  if (outcome.kind === "spawn_error") {
    return { check, status: "INFRA_ERROR", command: { ...commandEvidence, stderr_excerpt: bound(outcome.message) }, testEvidence: [] };
  }
  if (outcome.kind === "timed_out") {
    return { check, status: "TIMED_OUT", command: commandEvidence, testEvidence: [] };
  }

  const status: CheckStatus = outcome.exitCode === 0 ? "PASSED" : "FAILED";
  const junitPath = opts.junitReportPath?.(check) ?? null;
  const testEvidence = junitPath ? await parseJUnitXml(junitPath) : [];
  const coverageReportPath = opts.coverageReportPath?.(check) ?? null;

  return { check, status, command: commandEvidence, testEvidence, coverageReportPath };
}

// Matches one "word": a double-quoted run, a single-quoted run, or a bare
// run of non-whitespace characters — in that priority order, left to
// right. Not a full shell grammar (no escape-sequence handling inside
// quotes, no nesting) but real quoted-argument splitting, not a naive
// whitespace split.
const TOKEN_RE = /"([^"]*)"|'([^']*)'|(\S+)/g;

/** A real argv tokenizer (quote-aware), not a shell — mirrors
 * apps/api/app/services/code_runner.py's own `shlex.split` +
 * `subprocess.run(argv, shell=False)` security posture: a
 * project-configured command (never raw end-user input) is split into a
 * real argv list and spawned directly, so nothing here ever lets a shell
 * interpret a string as syntax. Handles a quoted argument containing
 * spaces (e.g. `node -e "console.log('a b')"`) correctly — a naive
 * `.split(/\s+/)` would shatter the quoted segment into multiple broken
 * tokens, or leave literal quote characters inside a token, silently
 * changing what actually runs; this doesn't. */
function tokenize(command: string): string[] {
  const tokens: string[] = [];
  let match: RegExpExecArray | null;
  TOKEN_RE.lastIndex = 0;
  while ((match = TOKEN_RE.exec(command)) !== null) {
    // Exactly one alternative always matches whenever the outer match
    // succeeds (the third, `\S+`, matches any non-whitespace run) — the
    // assertion narrows that guarantee, same reasoning as this module's
    // other regex-group assertions above.
    tokens.push((match[1] ?? match[2] ?? match[3])!);
  }
  return tokens;
}

function bound(text: string | undefined | null): string | undefined {
  if (!text) return undefined;
  return text.length > MAX_OUTPUT_EXCERPT_CHARS ? text.slice(0, MAX_OUTPUT_EXCERPT_CHARS) + "\n…(truncated)" : text;
}

type SpawnOutcome =
  | { kind: "exited"; exitCode: number; stdout: string; stderr: string }
  | { kind: "timed_out"; stdout: string; stderr: string }
  | { kind: "spawn_error"; message: string; stdout: string; stderr: string };

function spawnBounded(argv: string[], cwd: string, timeoutSeconds: number): Promise<SpawnOutcome> {
  return new Promise((resolve) => {
    // Callers only ever reach here after runOneCheck's own `argv.length
    // === 0` check, so argv[0] is always present — the assertion below
    // is narrowing a fact already established, not asserting past a real
    // unknown (noUncheckedIndexedAccess otherwise types this `string |
    // undefined`, which collapses spawn()'s overload resolution to `never`).
    const command = argv[0]!;
    const args = argv.slice(1);
    let stdout = "";
    let stderr = "";
    let settled = false;

    const child = spawn(command, args, { cwd, shell: false, windowsHide: true });

    const timer = setTimeout(() => {
      if (settled) return;
      settled = true;
      // Kill the whole tree as best-effort (POSIX: negative pid targets
      // the process group only when spawned with detached: true, which
      // this call intentionally omits to keep behavior identical on
      // Windows — a plain kill() on the child is the portable baseline).
      child.kill("SIGKILL");
      resolve({ kind: "timed_out", stdout, stderr });
    }, Math.max(1, timeoutSeconds) * 1000);

    child.stdout?.on("data", (chunk) => {
      stdout += chunk.toString();
    });
    child.stderr?.on("data", (chunk) => {
      stderr += chunk.toString();
    });

    child.on("error", (err) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      resolve({ kind: "spawn_error", message: err.message, stdout, stderr });
    });

    child.on("close", (exitCode) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      resolve({ kind: "exited", exitCode: exitCode ?? -1, stdout, stderr });
    });
  });
}
