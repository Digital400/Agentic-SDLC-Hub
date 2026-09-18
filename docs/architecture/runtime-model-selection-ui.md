# Runtime and Model Selection UI (Phase 14)

**Status:** New, additive frontend module (`apps/web/lib/runtime/`,
`apps/web/components/runtime/`) plus new test infrastructure for
`apps/web` (Vitest + React Testing Library — this workspace had none
before this phase). No existing page, route, or component was modified.

## 1. What this phase built

- **`lib/runtime/runtime-options.ts`** — dependency-free domain types and
  pure selection logic: `RuntimeOption`, `RuntimeSelectionState`,
  `resolveSelection()`, `resolveAutoSelection()`,
  `estimatedCostExceedsBudget()`. Kept free of React/fetch so every rule
  (what's disabled and why, whether a confirmation is required, what
  "Auto" resolves to) is unit-testable without rendering anything.
- **`components/runtime/runtime-model-selector.tsx`** — the presentational
  `RuntimeModelSelector` component, built entirely from this codebase's
  existing design system (`components/ui/select.tsx`, `card.tsx`,
  `badge.tsx`, `button.tsx` — no new UI primitives introduced).

## 2. Requirement-by-requirement mapping

| Requirement | Where |
|---|---|
| Runtime options (Auto/OpenCode/Codex/Claude Code/Antigravity/Connected local agent) | `RUNTIME_LABELS` in the component; `RuntimeKey` in the domain module |
| Execution location (Company sandbox / My connected development machine) | `EXECUTION_LOCATION_LABELS`; `ExecutionLocation` |
| Model source shown only when the runtime supports it | `selectedOption.supportsModelSelection` gates the model-source `<Select>`'s rendering |
| Per-option: availability, connection status, required capabilities, execution/data location, cost owner, estimated max cost, approval requirement, known limitations, disabled-selection reason | The `<dl>` detail panel, driven directly by `RuntimeOption`'s fields |
| Always shown: requested runtime, actual selected runtime, reason, budget, feature-flag status | The summary block, driven by `resolveSelection()`'s `RuntimeSelectionResolution` |
| Confirmation before a company-paid premium runtime | `pendingPremiumConfirm` state + `role="alertdialog"` panel; `resolveSelection()`'s `requiresConfirmation` field drives whether it appears |
| Accessible loading/disconnected/denied/timeout/failure states | `isLoading` → `role="status"`; `loadError` → `role="alert"`; per-option `connectionStatus` badges cover disconnected/denied/timeout inline in the detail panel |
| Component tests | `lib/runtime/runtime-options.test.ts` (15 tests, pure logic) + `components/runtime/runtime-model-selector.test.tsx` (8 tests, rendering/interaction) |

## 3. New test infrastructure for `apps/web`

This workspace had zero test tooling before this phase (confirmed —
no Jest/Vitest/Playwright config or dependency anywhere under
`apps/web`). Added:

- `vitest.config.ts` + `vitest.setup.ts` (jsdom environment,
  `@testing-library/jest-dom` matchers, the same `@/*` path alias
  `tsconfig.json` already defines).
- New devDependencies: `vitest`, `vite`, `@vitejs/plugin-react`,
  `@testing-library/react`, `@testing-library/dom`,
  `@testing-library/jest-dom`, `@testing-library/user-event`, `jsdom`.
- **One Yarn PnP fix required**: `@testing-library/jest-dom`'s optional
  Vitest integration (`dist/vitest.mjs`) requires `vitest` at runtime
  without declaring it as its own dependency — under Yarn PnP's strict
  resolution this is "ambiguous/unsound" and fails outright. Fixed with a
  `packageExtensions` entry in `.yarnrc.yml`, the exact same class of fix
  already documented there for `eslint-config-next`/`next` — declaring the
  missing dependency ourselves rather than loosening PnP resolution
  globally.

## 4. Scope reductions, disclosed

- **No browser end-to-end suite (Playwright) was added.** "Component and
  end-to-end tests" is interpreted here as component-level (Vitest +
  RTL) plus the pure-logic unit tests — a full Playwright browser suite is
  a larger, separate infrastructure investment (browser binaries, a
  running dev server, page-object scaffolding) not undertaken in this
  pass. The component's behavior is still fully exercised via RTL
  interaction tests (selecting options, triggering the premium
  confirmation flow, checking disabled/loading/error states).
- **`RuntimeModelSelector` is not yet wired into a real trigger page**
  (e.g. the "start implementation run" flow). It is a complete,
  independently tested component ready to be dropped into such a flow,
  but no existing page was modified to embed it in this pass — doing so
  would require deciding which specific action (implementation run,
  document generation, ...) it attaches to, which is a product decision
  this phase's own prompt doesn't specify.
- **No backend endpoint yet supplies `RuntimeOption[]`.** The component
  takes options as a prop; the natural future source is a new endpoint
  aggregating Phase 08/09/11's existing `build_coding_runtime_registry`-
  style registries (OpenCode, ACP) into this shape, plus Codex/Claude
  Code/Antigravity entries once Phase 12's disclosed native-adapter
  blocker is resolved. Not built here — it's a backend integration task,
  not a UI one.
- **"Auto" selection reason is an explicitly disclosed placeholder.**
  `resolveAutoSelection()` deterministically picks the first available,
  non-premium option and says so in its reason string, since Phase 17's
  real `RuntimeCostRouter` (cost/complexity-aware selection) does not
  exist yet. The call site is isolated to one function specifically so
  swapping in Phase 17's real logic later is a one-function change.

## 5. Tests executed and results

```
apps/web> yarn vitest run          → 2 files, 23 tests, all passing
apps/web> yarn exec tsc --noEmit   → 0 errors
```

## 6. Remaining risks / next phase

- Wire `RuntimeModelSelector` into a real trigger flow once a specific
  one is chosen.
- Build the backend endpoint that supplies real `RuntimeOption[]` data
  (availability, connection status, live cost estimates) instead of
  caller-supplied props.
- Replace `resolveAutoSelection()`'s placeholder policy with Phase 17's
  real `RuntimeCostRouter` once built.
- Consider adding a genuine Playwright e2e suite once a dev-server-backed
  CI job exists to run it against.
