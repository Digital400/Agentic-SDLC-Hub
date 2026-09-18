import { describe, expect, it, vi } from "vitest";

import {
  DeviceAuthorizationDeniedError,
  DeviceAuthorizationExpiredError,
  runDeviceAuthorization,
  type DeviceAuthHttpClient,
} from "../src/device-auth.js";

function immediateWaiter() {
  return Promise.resolve();
}

describe("runDeviceAuthorization", () => {
  it("returns a token once the server reports approved", async () => {
    let polls = 0;
    const client: DeviceAuthHttpClient = {
      requestDeviceCode: async () => ({
        device_code: "dc1", user_code: "ABCD", verification_uri: "https://example.com/device", expires_in: 60, interval: 0,
      }),
      pollToken: async () => {
        polls += 1;
        if (polls < 3) return { status: "pending" };
        return { status: "approved", token: { access_token: "tok123", token_type: "Bearer", expires_in: 3600 } };
      },
    };
    const onCodeReady = vi.fn();
    const token = await runDeviceAuthorization(client, "cli", onCodeReady, immediateWaiter);
    expect(token.access_token).toBe("tok123");
    expect(onCodeReady).toHaveBeenCalledWith(expect.objectContaining({ user_code: "ABCD" }));
    expect(polls).toBe(3);
  });

  it("throws DeviceAuthorizationDeniedError when the server reports denied", async () => {
    const client: DeviceAuthHttpClient = {
      requestDeviceCode: async () => ({ device_code: "dc1", user_code: "X", verification_uri: "u", expires_in: 60, interval: 0 }),
      pollToken: async () => ({ status: "denied" }),
    };
    await expect(runDeviceAuthorization(client, "cli", () => {}, immediateWaiter)).rejects.toThrow(DeviceAuthorizationDeniedError);
  });

  it("throws DeviceAuthorizationExpiredError once expires_in elapses without approval", async () => {
    let now = 0;
    vi.spyOn(Date, "now").mockImplementation(() => now);
    const client: DeviceAuthHttpClient = {
      requestDeviceCode: async () => ({ device_code: "dc1", user_code: "X", verification_uri: "u", expires_in: 1, interval: 1 }),
      pollToken: async () => {
        now += 2000; // advance past expiry on the very first poll
        return { status: "pending" };
      },
    };
    await expect(runDeviceAuthorization(client, "cli", () => {}, immediateWaiter)).rejects.toThrow(DeviceAuthorizationExpiredError);
    vi.restoreAllMocks();
  });
});
