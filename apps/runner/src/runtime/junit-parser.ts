/**
 * A minimal, dependency-free JUnit XML parser — turns a real `<testsuite>`
 * report into TestEvidence rows. Deliberately narrow (regex-based
 * attribute extraction, not a full XML DOM): JUnit reports from real test
 * tooling (pytest --junit-xml, jest/vitest --reporter=junit, go test with
 * gotestsum) are a small, well-known dialect, and adding a full XML
 * parsing dependency for that one dialect isn't worth it — same "no
 * diffing dependency" reasoning apps/api/app/services/implementation_agent.py's
 * module docstring already applies to using stdlib difflib over a
 * third-party diff library.
 *
 * HONESTY: a report that can't be found or parsed returns an empty
 * array — never a fabricated PASS. Every returned TestEvidence carries
 * `real_execution: true` (this parser is only ever called after a real
 * command actually ran — see check-executor.ts).
 */

import { readFile } from "node:fs/promises";

import type { TestEvidence, TestResult } from "../contracts/execution-result.js";

const TESTCASE_RE = /<testcase\b([^>]*?)(\/>|>([\s\S]*?)<\/testcase>)/g;
const ATTR_RE = /(\w[\w:-]*)="([^"]*)"/g;

function parseAttrs(raw: string): Record<string, string> {
  const attrs: Record<string, string> = {};
  let match: RegExpExecArray | null;
  ATTR_RE.lastIndex = 0;
  while ((match = ATTR_RE.exec(raw)) !== null) {
    // ATTR_RE's two capture groups are both non-optional in the pattern
    // itself (a `key="value"` match cannot exist without both) —
    // noUncheckedIndexedAccess types RegExpExecArray elements as possibly
    // undefined regardless, so this narrows a fact the regex already
    // guarantees, same reasoning as spawnBounded's own argv[0] assertion.
    attrs[match[1]!] = match[2]!;
  }
  return attrs;
}

function resultFor(body: string | undefined): TestResult {
  if (!body) return "PASS";
  if (/<failure\b/.test(body)) return "FAIL";
  if (/<error\b/.test(body)) return "FAIL"; // Python's own TestResult has no ERROR value — see execution-result.ts's docstring on this drift
  if (/<skipped\b/.test(body)) return "SKIPPED";
  return "PASS";
}

/** Parses `xmlPath` if it exists and is well-formed; returns `[]`
 * otherwise (a missing/corrupt report is not itself a test failure to
 * report — the check's own exit code, recorded separately by
 * check-executor.ts, already carries that). */
export async function parseJUnitXml(xmlPath: string): Promise<TestEvidence[]> {
  let xml: string;
  try {
    xml = await readFile(xmlPath, "utf-8");
  } catch {
    return [];
  }
  return parseJUnitXmlContent(xml);
}

export function parseJUnitXmlContent(xml: string): TestEvidence[] {
  const evidence: TestEvidence[] = [];
  let match: RegExpExecArray | null;
  TESTCASE_RE.lastIndex = 0;
  while ((match = TESTCASE_RE.exec(xml)) !== null) {
    // Group 1 (`([^>]*?)`) is not itself optional in the pattern — it
    // always captures (possibly empty) whenever the outer match succeeds.
    // Group 3 (the testcase body) genuinely IS optional (a self-closing
    // `<testcase .../>` has none) — resultFor's own `string | undefined`
    // parameter already handles that correctly, unassisted.
    const attrs = parseAttrs(match[1]!);
    const body = match[3];
    const name = attrs.classname ? `${attrs.classname}.${attrs.name ?? "unnamed"}` : attrs.name ?? "unnamed";
    const durationSeconds = attrs.time !== undefined ? Number(attrs.time) : undefined;
    evidence.push({
      name,
      result: resultFor(body),
      real_execution: true,
      duration_seconds: Number.isFinite(durationSeconds) ? durationSeconds : undefined,
    });
  }
  return evidence;
}
