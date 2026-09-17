/**
 * A minimal JSON-RPC 2.0 client over a pair of Node streams (typically a
 * spawned agent process's stdin/stdout) — newline-delimited JSON messages,
 * one per line. This is the one piece of AcpCodingRuntimeAdapter that is
 * genuinely, mechanically verified by real tests without needing a live
 * ACP agent binary (see contracts/acp.ts's own HONESTY note on the rest of
 * the protocol layer) — request/response correlation, concurrent in-flight
 * requests, per-request timeouts, and malformed-line handling are all real
 * behavior here, tested against plain in-memory streams and a real spawned
 * fake-agent fixture process (see tests/acp-adapter.integration.test.ts).
 *
 * Deliberately dependency-free (no `vscode-jsonrpc`/`json-rpc-2.0` package)
 * — the surface this runner actually needs (request/response, one-way
 * notifications, incoming requests FROM the peer) is small enough that a
 * new dependency isn't worth it, same reasoning junit-parser.ts's own
 * module docstring gives for not adding an XML library.
 */

import { randomUUID } from "node:crypto";
import type { Readable, Writable } from "node:stream";

export class JsonRpcTimeoutError extends Error {}
export class JsonRpcPeerError extends Error {
  constructor(message: string, public readonly code: number, public readonly data?: unknown) {
    super(message);
  }
}
export class JsonRpcClosedError extends Error {}

interface JsonRpcRequestMessage {
  jsonrpc: "2.0";
  id: string | number;
  method: string;
  params?: unknown;
}
interface JsonRpcNotificationMessage {
  jsonrpc: "2.0";
  method: string;
  params?: unknown;
}
interface JsonRpcResponseMessage {
  jsonrpc: "2.0";
  id: string | number;
  result?: unknown;
  error?: { code: number; message: string; data?: unknown };
}

type IncomingMessage = JsonRpcRequestMessage | JsonRpcNotificationMessage | JsonRpcResponseMessage;

function isResponse(msg: IncomingMessage): msg is JsonRpcResponseMessage {
  return "id" in msg && !("method" in msg);
}
function isRequest(msg: IncomingMessage): msg is JsonRpcRequestMessage {
  return "id" in msg && "method" in msg;
}

export type IncomingRequestHandler = (method: string, params: unknown) => Promise<unknown>;
export type NotificationHandler = (method: string, params: unknown) => void;

/**
 * `input`/`output` are named from THIS client's perspective — `output`
 * is what this client writes TO the peer (typically the peer's stdin),
 * `input` is what this client reads FROM the peer (typically the peer's
 * stdout). Kept as plain Node streams, not a ChildProcess, so this class
 * is fully testable against a PassThrough pair with no process spawned
 * at all.
 */
export class JsonRpcConnection {
  private buffer = "";
  private closed = false;
  private readonly pending = new Map<string, { resolve: (v: unknown) => void; reject: (e: Error) => void; timer: NodeJS.Timeout }>();
  private incomingRequestHandler: IncomingRequestHandler | null = null;
  private readonly notificationHandlers = new Map<string, NotificationHandler[]>();
  private readonly onMalformedLine: (line: string, error: unknown) => void;

  constructor(
    private readonly input: Readable,
    private readonly output: Writable,
    opts: { onMalformedLine?: (line: string, error: unknown) => void } = {},
  ) {
    this.onMalformedLine = opts.onMalformedLine ?? (() => {});
    this.input.on("data", (chunk: Buffer) => this.onData(chunk));
    this.input.on("close", () => this.close(new JsonRpcClosedError("Peer closed its output stream.")));
    this.input.on("error", () => this.close(new JsonRpcClosedError("Peer stream errored.")));
  }

  /** Handles a request FROM the peer (e.g. ACP's session/request_permission) — at most one handler; a second call replaces it. */
  onIncomingRequest(handler: IncomingRequestHandler): void {
    this.incomingRequestHandler = handler;
  }

  onNotification(method: string, handler: NotificationHandler): void {
    const existing = this.notificationHandlers.get(method) ?? [];
    existing.push(handler);
    this.notificationHandlers.set(method, existing);
  }

  request<T = unknown>(method: string, params: unknown, timeoutMs: number): Promise<T> {
    if (this.closed) return Promise.reject(new JsonRpcClosedError("Connection is closed."));
    const id = randomUUID();
    const message: JsonRpcRequestMessage = { jsonrpc: "2.0", id, method, params };
    return new Promise<T>((resolve, reject) => {
      const timer = setTimeout(() => {
        this.pending.delete(id);
        reject(new JsonRpcTimeoutError(`Request "${method}" timed out after ${timeoutMs}ms.`));
      }, timeoutMs);
      this.pending.set(id, { resolve: resolve as (v: unknown) => void, reject, timer });
      this.write(message);
    });
  }

  notify(method: string, params: unknown): void {
    if (this.closed) return;
    const message: JsonRpcNotificationMessage = { jsonrpc: "2.0", method, params };
    this.write(message);
  }

  close(reason: Error = new JsonRpcClosedError("Connection closed.")): void {
    if (this.closed) return;
    this.closed = true;
    for (const { reject, timer } of this.pending.values()) {
      clearTimeout(timer);
      reject(reason);
    }
    this.pending.clear();
  }

  private write(message: JsonRpcRequestMessage | JsonRpcNotificationMessage): void {
    this.output.write(JSON.stringify(message) + "\n");
  }

  private onData(chunk: Buffer): void {
    this.buffer += chunk.toString("utf-8");
    let newlineIndex: number;
    while ((newlineIndex = this.buffer.indexOf("\n")) !== -1) {
      const line = this.buffer.slice(0, newlineIndex).trim();
      this.buffer = this.buffer.slice(newlineIndex + 1);
      if (line.length === 0) continue;
      this.handleLine(line);
    }
  }

  private handleLine(line: string): void {
    let message: IncomingMessage;
    try {
      message = JSON.parse(line) as IncomingMessage;
    } catch (error) {
      this.onMalformedLine(line, error);
      return;
    }

    if (isResponse(message)) {
      const pending = this.pending.get(String(message.id));
      if (!pending) return; // a response to a request we no longer track (already timed out) — drop it
      clearTimeout(pending.timer);
      this.pending.delete(String(message.id));
      if (message.error) {
        pending.reject(new JsonRpcPeerError(message.error.message, message.error.code, message.error.data));
      } else {
        pending.resolve(message.result);
      }
      return;
    }

    if (isRequest(message)) {
      if (!this.incomingRequestHandler) {
        this.write({ jsonrpc: "2.0", id: message.id, error: { code: -32601, message: `No handler registered for ${message.method}` } } as never);
        return;
      }
      this.incomingRequestHandler(message.method, message.params)
        .then((result) => this.output.write(JSON.stringify({ jsonrpc: "2.0", id: message.id, result }) + "\n"))
        .catch((error: Error) =>
          this.output.write(JSON.stringify({ jsonrpc: "2.0", id: message.id, error: { code: -32000, message: error.message } }) + "\n"),
        );
      return;
    }

    // Notification from the peer (e.g. session/update).
    for (const handler of this.notificationHandlers.get(message.method) ?? []) {
      handler(message.method, message.params);
    }
  }
}
