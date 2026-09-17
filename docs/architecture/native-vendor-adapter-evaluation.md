# Native Vendor SDK Adapter Evaluation (Phase 12)

**Status:** Analysis and decision only — no new adapter code. Disclosed
blocker, not a skipped phase: see §3.

## 1. What this phase asked for

Phase 12 of the Universal Agent Runtime Baseline migration asks for a
native, vendor-SDK-backed `CodingRuntimeAdapter` for one or more of the
major coding-agent vendors (Codex, Claude Code, Antigravity), to compare
against the generic protocol-based adapters already built in this
migration (Phase 08's OpenCode adapter over `@opencode-ai/sdk`, Phase 11's
ACP adapter over the open Agent Client Protocol).

## 2. Why a generic protocol adapter was preferred so far

Every adapter built in this migration up to this point deliberately avoids
a vendor-proprietary SDK unless one was already a verified, installed
dependency (Phase 08's `@opencode-ai/sdk@1.18.31` — installed and
inspected directly, every call site checked against a real type
declaration). Two protocol-shaped adapters now exist:

| Adapter | Transport | Coupling |
|---|---|---|
| `OpenCodeRuntimeAdapter` (Phase 08) | OpenCode's own HTTP/SSE SDK | One vendor, but the SDK itself is a real, verified dependency |
| `AcpCodingRuntimeAdapter` (Phase 11) | Agent Client Protocol (ACP) — an open, vendor-neutral JSON-RPC protocol | Any agent that speaks ACP, not tied to one vendor's SDK |

This is the same strangler-pattern reasoning applied throughout the
migration (`app/agent_runtime`'s WorkPacket/ExecutionResult contracts are
explicitly vendor-neutral by hard rule): prefer a protocol both sides
publish and can be verified against, over an adapter that only works
because it happens to match one vendor's current, unverified SDK shape.

## 3. Why no native Codex/Claude Code/Antigravity SDK adapter was built here — disclosed blocker

This is a genuine capability gap in this working session, not a scope
decision:

- This environment has **no network access** and **no installed SDK
  package** for Codex's, Claude Code's, or Antigravity's respective
  official agent SDKs. Every other adapter in this migration was built
  against a real, installed, inspectable dependency (OpenCode's SDK) or a
  real, spawnable process speaking a publicly documented protocol (ACP).
  Writing a "native SDK adapter" for any of these three vendors right now
  would mean guessing at method names, request/response shapes, and
  authentication flows from memory rather than from a verified source —
  exactly the kind of fabrication this migration's own hard rules
  (WorkPacket/ExecutionResult §"no vendor-mandatory fields," and this
  session's own standing "never fabricate, disclose genuine blockers"
  practice) forbid.
- Concretely: no `package.json` dependency, no vendored type declarations,
  and no reachable documentation endpoint exist in this repository or
  environment for any of the three vendors' native SDKs as of this
  migration pass.

## 4. Decision

**Defer a native vendor SDK adapter.** The ACP adapter (Phase 11) already
gives this platform a working, protocol-verified path to run coding agents
without waiting on any single vendor's SDK, and covers the "run a
third-party coding agent under this platform's security policy" use case
Phase 12 is ultimately in service of. A native adapter for a specific
vendor should be built in a future phase, at the point where:

1. That vendor's SDK is an approved, installed dependency this repository
   can actually build against and inspect (mirroring exactly how Phase 08
   proceeded only after `@opencode-ai/sdk` was installed and verified), or
2. That vendor ships (or already supports) ACP itself, in which case no
   new adapter is needed at all — the existing `AcpCodingRuntimeAdapter`,
   configured with that vendor's ACP-compatible executable via
   `ACP_APPROVED_AGENTS_JSON` (see `docs/architecture/acp-runtime.md` §1),
   already covers it with zero new code.

No code changes accompany this phase. This document is the phase's
deliverable: the comparison Phase 12 asked for, and an explicit,
non-fabricated account of why the native adapter itself is not built in
this pass.

## 5. Remaining risk

If a stakeholder specifically needs one vendor's native tool-calling
surface (something ACP's generic session/tool-call shape cannot express),
that gap remains open until this phase is revisited with real SDK access.
