import { mkdtemp, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";

import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { parseJUnitXml, parseJUnitXmlContent } from "../src/runtime/junit-parser.js";

describe("parseJUnitXmlContent", () => {
  it("returns PASS for a self-closing testcase with no failure/error/skipped child", () => {
    const evidence = parseJUnitXmlContent(`<testsuite><testcase name="works" time="0.5"/></testsuite>`);
    expect(evidence).toEqual([{ name: "works", result: "PASS", real_execution: true, duration_seconds: 0.5 }]);
  });

  it("returns FAIL for a testcase with a <failure> child", () => {
    const evidence = parseJUnitXmlContent(`<testsuite><testcase name="broken"><failure message="boom">trace</failure></testcase></testsuite>`);
    expect(evidence[0]!.result).toBe("FAIL");
  });

  it("returns FAIL for a testcase with an <error> child", () => {
    const evidence = parseJUnitXmlContent(`<testsuite><testcase name="errored"><error message="boom"/></testcase></testsuite>`);
    expect(evidence[0]!.result).toBe("FAIL");
  });

  it("returns SKIPPED for a testcase with a <skipped> child", () => {
    const evidence = parseJUnitXmlContent(`<testsuite><testcase name="skipped-one"><skipped/></testcase></testsuite>`);
    expect(evidence[0]!.result).toBe("SKIPPED");
  });

  it("qualifies the name with classname when present", () => {
    const evidence = parseJUnitXmlContent(`<testsuite><testcase classname="pkg.Mod" name="it_works"/></testsuite>`);
    expect(evidence[0]!.name).toBe("pkg.Mod.it_works");
  });

  it("returns an empty array for content with no testcases", () => {
    expect(parseJUnitXmlContent(`<testsuite></testsuite>`)).toEqual([]);
  });

  it("never fabricates a result for malformed XML — returns whatever it can, never throws", () => {
    expect(() => parseJUnitXmlContent("not xml at all")).not.toThrow();
    expect(parseJUnitXmlContent("not xml at all")).toEqual([]);
  });
});

describe("parseJUnitXml (file-backed)", () => {
  let dir: string;

  beforeEach(async () => {
    dir = await mkdtemp(path.join(tmpdir(), "junit-parser-"));
  });

  afterEach(async () => {
    await rm(dir, { recursive: true, force: true });
  });

  it("returns an empty array, not an error, when the report file doesn't exist", async () => {
    expect(await parseJUnitXml(path.join(dir, "missing.xml"))).toEqual([]);
  });

  it("parses a real file on disk", async () => {
    const file = path.join(dir, "junit.xml");
    await writeFile(file, `<testsuite><testcase name="real" time="1"/></testsuite>`);
    const evidence = await parseJUnitXml(file);
    expect(evidence).toEqual([{ name: "real", result: "PASS", real_execution: true, duration_seconds: 1 }]);
  });
});
