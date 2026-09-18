/**
 * "Never upload the user's runtime credentials" (Phase 13's own literal
 * requirement) made structural: before any payload leaves this process,
 * it is scanned for known credential-shaped keys and values, and refused
 * rather than silently stripped — a stripped-and-sent payload could hide
 * a real bug; refusing forces the caller to fix what's building the
 * payload.
 */

const SINGLE_WORD_CREDENTIAL_TERMS = new Set(["token", "secret", "password", "credential", "authorization"]);
const COMPOUND_CREDENTIAL_TERMS = [["api", "key"], ["private", "key"]];

/** Splits a snake_case/camelCase/kebab-case key into lowercase words, so
 * "total_tokens" (plural, a metric) is never confused with "access_token"
 * (singular, a credential) the way a naive substring/regex match would. */
function splitIntoWords(key: string): string[] {
  return key
    .replace(/([a-z0-9])([A-Z])/g, "$1_$2")
    .split(/[^a-zA-Z0-9]+/)
    .filter(Boolean)
    .map((w) => w.toLowerCase());
}

function keyLooksCredentialShaped(key: string): boolean {
  const words = splitIntoWords(key);
  if (words.some((w) => SINGLE_WORD_CREDENTIAL_TERMS.has(w))) return true;
  return COMPOUND_CREDENTIAL_TERMS.some(([a, b]) => {
    for (let i = 0; i < words.length - 1; i++) {
      if (words[i] === a && words[i + 1] === b) return true;
    }
    return false;
  });
}
// Recognizable shapes of common credential values, even under an innocuous key name.
const CREDENTIAL_VALUE_PATTERNS = [
  /^ghp_[A-Za-z0-9]{36}$/, // GitHub personal access token
  /^sk-[A-Za-z0-9]{20,}$/, // OpenAI-style secret key
  /^Bearer\s+\S+/i,
];

export class CredentialLeakError extends Error {}

function looksLikeCredentialValue(value: string): boolean {
  return CREDENTIAL_VALUE_PATTERNS.some((p) => p.test(value));
}

/** Recursively scans a JSON-serializable payload; throws on the first suspected credential field. */
export function assertNoCredentialFields(payload: unknown, path = "$"): void {
  if (payload === null || payload === undefined) return;
  if (typeof payload === "string") {
    if (looksLikeCredentialValue(payload)) {
      throw new CredentialLeakError(`Refusing to upload ${path}: value matches a known credential shape.`);
    }
    return;
  }
  if (Array.isArray(payload)) {
    payload.forEach((item, i) => assertNoCredentialFields(item, `${path}[${i}]`));
    return;
  }
  if (typeof payload === "object") {
    for (const [key, value] of Object.entries(payload as Record<string, unknown>)) {
      if (keyLooksCredentialShaped(key)) {
        throw new CredentialLeakError(`Refusing to upload ${path}.${key}: field name looks credential-shaped.`);
      }
      assertNoCredentialFields(value, `${path}.${key}`);
    }
  }
}
