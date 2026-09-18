import { describe, expect, it } from "vitest";

import { assertNoCredentialFields, CredentialLeakError } from "../src/credential-guard.js";

describe("assertNoCredentialFields", () => {
  it("allows an ordinary evidence payload", () => {
    expect(() =>
      assertNoCredentialFields({ job_id: "1", events: [{ type: "STATUS" }], usage: { total_tokens: 10, cost_usd: 0.01 } }),
    ).not.toThrow();
  });

  it("refuses a field literally named 'token'", () => {
    expect(() => assertNoCredentialFields({ token: "whatever" })).toThrow(CredentialLeakError);
  });

  it("refuses a field named 'api_key' nested deep in the payload", () => {
    expect(() => assertNoCredentialFields({ events: [{ meta: { api_key: "x" } }] })).toThrow(CredentialLeakError);
  });

  it("refuses a value shaped like a GitHub personal access token even under an innocuous key", () => {
    expect(() => assertNoCredentialFields({ note: "ghp_" + "a".repeat(36) })).toThrow(CredentialLeakError);
  });

  it("refuses an Authorization-header-shaped value", () => {
    expect(() => assertNoCredentialFields({ note: "Bearer abc.def.ghi" })).toThrow(CredentialLeakError);
  });

  it("does not false-positive on fields that merely contain 'token' glued to another word", () => {
    // Word-boundary matching: "total_tokens" and "tokenizer_version" are not
    // treated as credential-shaped, only a standalone "token" segment is.
    expect(() => assertNoCredentialFields({ total_tokens: 42, tokenizer_version: "v1" })).not.toThrow();
  });

  it("still refuses a standalone 'token' segment inside a compound key", () => {
    expect(() => assertNoCredentialFields({ access_token: "abc" })).toThrow(CredentialLeakError);
  });
});
