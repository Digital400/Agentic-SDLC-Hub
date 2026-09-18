/**
 * Device authorization flow — the standard OAuth 2.0 Device Authorization
 * Grant (RFC 8628) shape: the developer's machine has no browser-redirect
 * surface of its own, so it requests a device+user code pair, shows the
 * developer a URL and code to approve elsewhere, then polls for a token.
 * This is a real, publicly documented protocol shape, not invented here —
 * what's new is only this client's use of it against this platform's
 * (not-yet-built) authorization server.
 */

export interface DeviceAuthorizationResponse {
  device_code: string;
  user_code: string;
  verification_uri: string;
  expires_in: number;
  interval: number;
}

export interface DeviceTokenResponse {
  access_token: string;
  token_type: string;
  expires_in: number;
}

export class DeviceAuthorizationDeniedError extends Error {}
export class DeviceAuthorizationExpiredError extends Error {}

export interface DeviceAuthHttpClient {
  requestDeviceCode(clientId: string): Promise<DeviceAuthorizationResponse>;
  pollToken(clientId: string, deviceCode: string): Promise<{ status: "pending" | "denied" | "approved"; token?: DeviceTokenResponse }>;
}

export interface DeviceAuthWaiter {
  (ms: number): Promise<void>;
}

const defaultWaiter: DeviceAuthWaiter = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

/**
 * Runs the full device flow: request a code, invoke onCodeReady so the
 * caller can display it, then poll until approved/denied/expired.
 */
export async function runDeviceAuthorization(
  client: DeviceAuthHttpClient,
  clientId: string,
  onCodeReady: (auth: DeviceAuthorizationResponse) => void,
  waiter: DeviceAuthWaiter = defaultWaiter,
): Promise<DeviceTokenResponse> {
  const auth = await client.requestDeviceCode(clientId);
  onCodeReady(auth);

  const deadline = Date.now() + auth.expires_in * 1000;
  while (Date.now() < deadline) {
    await waiter(auth.interval * 1000);
    const result = await client.pollToken(clientId, auth.device_code);
    if (result.status === "approved") {
      if (!result.token) throw new Error("Server reported approved with no token — protocol violation.");
      return result.token;
    }
    if (result.status === "denied") {
      throw new DeviceAuthorizationDeniedError("Device authorization was denied.");
    }
    // "pending" — keep polling.
  }
  throw new DeviceAuthorizationExpiredError("Device authorization expired before approval.");
}
