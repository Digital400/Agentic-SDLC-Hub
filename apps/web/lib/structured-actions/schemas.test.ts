import { describe, expect, it } from "vitest";

import {
  ADDITIONAL_CONTEXT_KEY,
  ADDITIONAL_CONTEXT_MAX_LENGTH,
  buildInputContext,
  getStageSchema,
  hasStructuredSchema,
  missingRequiredFields,
  STAGE_FIELD_SCHEMAS,
} from "./schemas";

describe("getStageSchema / hasStructuredSchema", () => {
  it("returns a schema for each of the six migrated stages", () => {
    for (const key of ["requirement_intake", "story_crafting", "lld", "implementation", "testing", "pr_review"]) {
      expect(hasStructuredSchema(key)).toBe(true);
      expect(getStageSchema(key)).not.toBeNull();
    }
  });

  it("returns null for a stage with no structured schema yet (falls back to freeform)", () => {
    expect(getStageSchema("hld")).toBeNull();
    expect(hasStructuredSchema("problem_discovery")).toBe(false);
  });
});

describe("buildInputContext", () => {
  const schema = STAGE_FIELD_SCHEMAS.requirement_intake!;

  it("includes only non-empty field values, keyed exactly as the field schema specifies", () => {
    const context = buildInputContext(schema, { business_objective: "Grow revenue", users: "" }, "");
    expect(context).toEqual({ business_objective: "Grow revenue" });
  });

  it("adds the additional_context key only when non-empty", () => {
    const context = buildInputContext(schema, {}, "Some extra note");
    expect(context[ADDITIONAL_CONTEXT_KEY]).toBe("Some extra note");
  });

  it("omits additional_context entirely when blank", () => {
    const context = buildInputContext(schema, {}, "");
    expect(ADDITIONAL_CONTEXT_KEY in context).toBe(false);
  });

  it("truncates additional_context to the documented max length rather than sending unbounded free text", () => {
    const longText = "x".repeat(ADDITIONAL_CONTEXT_MAX_LENGTH + 100);
    const context = buildInputContext(schema, {}, longText);
    expect(context[ADDITIONAL_CONTEXT_KEY]!.length).toBe(ADDITIONAL_CONTEXT_MAX_LENGTH);
  });
});

describe("missingRequiredFields", () => {
  const schema = STAGE_FIELD_SCHEMAS.lld!;

  it("flags a required field left blank", () => {
    const missing = missingRequiredFields(schema, { selected_story: "" });
    expect(missing.map((f) => f.key)).toContain("selected_story");
  });

  it("returns an empty list once every required field is filled", () => {
    const missing = missingRequiredFields(schema, { selected_story: "Password Reset" });
    expect(missing).toHaveLength(0);
  });

  it("never flags an optional field", () => {
    const missing = missingRequiredFields(schema, { selected_story: "Password Reset" });
    expect(missing.some((f) => f.key === "technical_constraints")).toBe(false);
  });
});
