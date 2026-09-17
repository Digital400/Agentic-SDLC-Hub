import { PassThrough } from "node:stream";

import { describe, expect, it, vi } from "vitest";

import { JsonRpcClosedError, JsonRpcConnection, JsonRpcPeerError, JsonRpcTimeoutError } from "../src/runtime/acp-json-rpc.js";

/** Builds a connected pair: `client` writes land on `peerIn` (readable
 * by the test as "what the client sent"); writing to `peerOut` delivers
 * data to `client` (simulating "what the peer sent back"). */
function makePair() {
  const toClient = new PassThrough();
  const fromClient = new PassThrough();
  const client = new JsonRpcConnection(toClient, fromClient);
  return { client, toClient, fromClient };
}

function readWrittenLines(stream: PassThrough): string[] {
  const raw = stream.read();
  if (!raw) return [];
  return raw
    .toString("utf-8")
    .split("\n")
    .map((l: string) => l.trim())
    .filter((l: string) => l.length > 0);
}

describe("JsonRpcConnection — request/response", () => {
  it("sends a well-formed JSON-RPC request and resolves on a matching response", async () => {
    const { client, fromClient, toClient } = makePair();
    const pending = client.request("initialize", { protocolVersion: 1 }, 5000);

    const [sent] = readWrittenLines(fromClient);
    const parsed = JSON.parse(sent!);
    expect(parsed.jsonrpc).toBe("2.0");
    expect(parsed.method).toBe("initialize");
    expect(parsed.params).toEqual({ protocolVersion: 1 });
    expect(typeof parsed.id).toBe("string");

    toClient.write(JSON.stringify({ jsonrpc: "2.0", id: parsed.id, result: { protocolVersion: 1 } }) + "\n");

    await expect(pending).resolves.toEqual({ protocolVersion: 1 });
  });

  it("rejects with JsonRpcPeerError when the peer returns a JSON-RPC error", async () => {
    const { client, toClient, fromClient } = makePair();
    const pending = client.request("initialize", {}, 5000);
    const [sent] = readWrittenLines(fromClient);
    const { id } = JSON.parse(sent!);

    toClient.write(JSON.stringify({ jsonrpc: "2.0", id, error: { code: -32601, message: "Method not found" } }) + "\n");

    await expect(pending).rejects.toBeInstanceOf(JsonRpcPeerError);
    await expect(pending).rejects.toThrow("Method not found");
  });

  it("times out a request the peer never answers", async () => {
    const { client } = makePair();
    await expect(client.request("initialize", {}, 20)).rejects.toBeInstanceOf(JsonRpcTimeoutError);
  });

  it("correlates multiple concurrent in-flight requests independently", async () => {
    const { client, toClient, fromClient } = makePair();
    const first = client.request("a", {}, 5000);
    const second = client.request("b", {}, 5000);
    const [sentFirst, sentSecond] = readWrittenLines(fromClient);
    const idA = JSON.parse(sentFirst!).id;
    const idB = JSON.parse(sentSecond!).id;

    // Answer out of order — the second request first.
    toClient.write(JSON.stringify({ jsonrpc: "2.0", id: idB, result: "B" }) + "\n");
    toClient.write(JSON.stringify({ jsonrpc: "2.0", id: idA, result: "A" }) + "\n");

    await expect(first).resolves.toBe("A");
    await expect(second).resolves.toBe("B");
  });

  it("ignores a malformed line via the onMalformedLine hook, without crashing pending requests", async () => {
    const toClient = new PassThrough();
    const fromClient = new PassThrough();
    const onMalformedLine = vi.fn();
    const client = new JsonRpcConnection(toClient, fromClient, { onMalformedLine });
    const pending = client.request("initialize", {}, 5000);
    const [sent] = readWrittenLines(fromClient);
    const { id } = JSON.parse(sent!);

    toClient.write("not valid json at all\n");
    toClient.write(JSON.stringify({ jsonrpc: "2.0", id, result: "ok" }) + "\n");

    await expect(pending).resolves.toBe("ok");
    expect(onMalformedLine).toHaveBeenCalledWith("not valid json at all", expect.anything());
  });

  it("rejects every pending request when the connection is closed", async () => {
    const { client } = makePair();
    const pending = client.request("initialize", {}, 5000);
    client.close(new JsonRpcClosedError("Shutting down."));
    await expect(pending).rejects.toBeInstanceOf(JsonRpcClosedError);
  });

  it("refuses to send a new request once closed", async () => {
    const { client } = makePair();
    client.close();
    await expect(client.request("initialize", {}, 5000)).rejects.toBeInstanceOf(JsonRpcClosedError);
  });
});

describe("JsonRpcConnection — notifications", () => {
  it("delivers a one-way notification from the peer to a registered handler", () => {
    const { toClient } = makePair();
    // Re-create with a real handler bound before any data arrives.
    const fromClient = new PassThrough();
    const client2 = new JsonRpcConnection(toClient, fromClient);
    const received: unknown[] = [];
    client2.onNotification("session/update", (_method, params) => received.push(params));

    toClient.write(JSON.stringify({ jsonrpc: "2.0", method: "session/update", params: { sessionId: "s1" } }) + "\n");

    expect(received).toEqual([{ sessionId: "s1" }]);
  });

  it("a notification never triggers a response write", () => {
    const toClient = new PassThrough();
    const fromClient = new PassThrough();
    new JsonRpcConnection(toClient, fromClient);
    toClient.write(JSON.stringify({ jsonrpc: "2.0", method: "session/update", params: {} }) + "\n");
    expect(readWrittenLines(fromClient)).toEqual([]);
  });
});

describe("JsonRpcConnection — incoming requests from the peer", () => {
  it("answers an incoming request via the registered handler", async () => {
    const toClient = new PassThrough();
    const fromClient = new PassThrough();
    const client = new JsonRpcConnection(toClient, fromClient);
    client.onIncomingRequest(async (method, params) => {
      expect(method).toBe("session/request_permission");
      expect(params).toEqual({ toolCallId: "t1" });
      return { outcome: { outcome: "selected", optionId: "allow_once" } };
    });

    toClient.write(JSON.stringify({ jsonrpc: "2.0", id: "req-1", method: "session/request_permission", params: { toolCallId: "t1" } }) + "\n");
    await new Promise((r) => setImmediate(r));

    const [sent] = readWrittenLines(fromClient);
    const response = JSON.parse(sent!);
    expect(response).toEqual({ jsonrpc: "2.0", id: "req-1", result: { outcome: { outcome: "selected", optionId: "allow_once" } } });
  });

  it("returns a method-not-found error when no handler is registered", async () => {
    const toClient = new PassThrough();
    const fromClient = new PassThrough();
    new JsonRpcConnection(toClient, fromClient);

    toClient.write(JSON.stringify({ jsonrpc: "2.0", id: "req-1", method: "session/request_permission", params: {} }) + "\n");
    await new Promise((r) => setImmediate(r));

    const [sent] = readWrittenLines(fromClient);
    const response = JSON.parse(sent!);
    expect(response.error.code).toBe(-32601);
  });

  it("returns an error response when the handler itself throws", async () => {
    const toClient = new PassThrough();
    const fromClient = new PassThrough();
    const client = new JsonRpcConnection(toClient, fromClient);
    client.onIncomingRequest(async () => {
      throw new Error("boom");
    });

    toClient.write(JSON.stringify({ jsonrpc: "2.0", id: "req-1", method: "x", params: {} }) + "\n");
    await new Promise((r) => setImmediate(r));

    const [sent] = readWrittenLines(fromClient);
    const response = JSON.parse(sent!);
    expect(response.error.message).toBe("boom");
  });
});
