# Golden evaluation dataset

Read by `app/services/runtime_evaluation.py`'s `load_golden_cases()`.

**Empty by design.** This directory ships with no cases. Populating it
with real, redacted historical approved examples requires a real
redaction/anonymization pipeline over real production history — building
one, and running it over real data, is outside what this development
session has access to. Fabricating synthetic "historical" examples and
presenting them as real would violate this migration's own "never report
simulated execution as real execution" rule extended to evaluation data.

## Format

One `*.json` file per case:

```json
{
  "case_id": "requirement-intake-001",
  "task_type": "REQUIREMENT_ANALYSIS",
  "input_context": {
    "business_objective": "...",
    "users": "...",
    "current_problem": "..."
  },
  "reference_output": "## Requirement Intake\n\n... (a prior, human-approved draft, if available)"
}
```

`reference_output` is optional — omit it for a case that only exercises
routing/validation behavior rather than content-quality comparison.
