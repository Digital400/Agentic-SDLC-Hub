#!/usr/bin/env node
// A minimal, real ACP-shaped JSON-RPC peer, for
// tests/acp-adapter.integration.test.ts only — never used in production.
// Speaks newline-delimited JSON-RPC 2.0 over stdio, exactly like
// AcpCodingRuntimeAdapter expects a real agent to. On "session/prompt",
// emits one session/update (a message chunk) and one tool_call_update
// carrying a file diff, then answers the prompt request itself.

import readline from "node:readline";

const rl = readline.createInterface({ input: process.stdin, terminal: false });

function send(message) {
  process.stdout.write(JSON.stringify(message) + "\n");
}

function notify(method, params) {
  send({ jsonrpc: "2.0", method, params });
}

rl.on("line", (line) => {
  const trimmed = line.trim();
  if (!trimmed) return;
  const msg = JSON.parse(trimmed);
  if (msg.method === "initialize") {
    send({ jsonrpc: "2.0", id: msg.id, result: { protocolVersion: 1, agentCapabilities: { loadSession: false } } });
    return;
  }
  if (msg.method === "session/new") {
    send({ jsonrpc: "2.0", id: msg.id, result: { sessionId: "fake-session-1" } });
    return;
  }
  if (msg.method === "session/prompt") {
    notify("session/update", {
      sessionId: "fake-session-1",
      update: { sessionUpdate: "agent_message_chunk", content: { type: "text", text: "Working on it." } },
    });
    notify("session/update", {
      sessionId: "fake-session-1",
      update: {
        sessionUpdate: "tool_call_update", toolCallId: "call-1", status: "completed",
        content: [{ type: "diff", path: "README.md", diff: "--- a/README.md\n+++ b/README.md\n+fake change\n" }],
      },
    });
    send({ jsonrpc: "2.0", id: msg.id, result: { stopReason: "end_turn" } });
    return;
  }
  // Unrecognized method — respond with a real JSON-RPC error, same as a
  // real agent would for a method it doesn't support.
  send({ jsonrpc: "2.0", id: msg.id, error: { code: -32601, message: `Method not found: ${msg.method}` } });
});
