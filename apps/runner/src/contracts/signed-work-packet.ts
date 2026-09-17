/**
 * Verification for SignedWorkPacket (see work-packet.ts's docstring for
 * why HMAC-SHA256 with a shared secret, not a JWT/asymmetric scheme, is
 * the correct mechanism here). This is this phase's step 1, "Receive a
 * signed WorkPacket" — a packet that fails verification must never reach
 * OpenCodeRuntimeAdapter.execute at all.
 */

import { createHmac, timingSafeEqual } from "node:crypto";

import type { SignedWorkPacket, WorkPacket } from "./work-packet.js";

export class WorkPacketSignatureError extends Error {}

/**
 * Canonical JSON: keys sorted recursively so the same logical packet
 * always signs to the same bytes regardless of key insertion order on
 * either side of the wire (this runner's JSON.parse output order is not
 * guaranteed to match the Python side's serialization order).
 */
export function canonicalize(value: unknown): string {
  return JSON.stringify(sortKeysDeep(value));
}

function sortKeysDeep(value: unknown): unknown {
  if (Array.isArray(value)) {
    return value.map(sortKeysDeep);
  }
  if (value !== null && typeof value === "object") {
    const sorted: Record<string, unknown> = {};
    for (const key of Object.keys(value as Record<string, unknown>).sort()) {
      sorted[key] = sortKeysDeep((value as Record<string, unknown>)[key]);
    }
    return sorted;
  }
  return value;
}

export function signWorkPacket(packet: WorkPacket, secret: string): string {
  return createHmac("sha256", secret).update(canonicalize(packet)).digest("hex");
}

/**
 * Verifies `signed.signature` against `signed.packet` using `secret`.
 * Throws WorkPacketSignatureError (never returns false) so a caller can
 * never accidentally fall through a boolean check — fail-closed, matching
 * Phase 07's ScopePolicy/ToolPolicy deny-by-default convention.
 */
export function verifySignedWorkPacket(signed: SignedWorkPacket, secret: string): WorkPacket {
  const expected = signWorkPacket(signed.packet, secret);
  const expectedBuf = Buffer.from(expected, "hex");
  const actualBuf = Buffer.from(signed.signature, "hex");
  if (expectedBuf.length !== actualBuf.length || !timingSafeEqual(expectedBuf, actualBuf)) {
    throw new WorkPacketSignatureError("WorkPacket signature verification failed — refusing to execute an unverified packet.");
  }
  return signed.packet;
}
