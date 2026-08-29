// The default prompt content itself lives in ../agents/*.md — one file per
// agent, each a human-readable source of truth for that agent's system
// prompt, output format, and validation checklist (mirrored into the
// database via apps/api/app/db/seed.py's RICH_DEFAULT_PROMPTS today).
//
// This file will export typed loaders/parsers for those markdown files
// once the agent engine (LangGraph) is introduced and actually needs to
// read them at runtime. Intentionally empty until then.

export {};
