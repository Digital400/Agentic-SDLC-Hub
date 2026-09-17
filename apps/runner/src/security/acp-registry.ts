/**
 * The admin-approved ACP agent registry — this phase's "Support admin-
 * approved ACP agents only" and "Do not allow users to provide arbitrary
 * executable paths" requirements, made structural: the ONLY way an
 * executable path enters this runner is through
 * ACP_APPROVED_AGENTS_JSON (an operator-set environment variable, parsed
 * once at startup), never a field on a WorkPacket or any other per-
 * request input. A WorkPacket may only ever name an agent by its
 * registry KEY (a short string like "my-approved-agent"); resolving that
 * key to a real executable path happens exclusively here.
 */

export interface ApprovedAcpAgent {
  /** The registry key a WorkPacket/request names — never the path itself. */
  key: string;
  /** Absolute path to the approved executable — operator-configured only. */
  executablePath: string;
  args: string[];
  /** Pins which ACP protocol version this agent was validated against — "pin adapter/runtime versions." */
  protocolVersion: number;
  enabled: boolean;
}

export class AcpAgentNotApprovedError extends Error {}

/** Parses ACP_APPROVED_AGENTS_JSON — a JSON array of ApprovedAcpAgent-
 * shaped objects. Malformed/missing config is treated as "no agents
 * approved" (fail closed), never as "allow anything." */
export function parseApprovedAcpAgents(json: string | undefined): ApprovedAcpAgent[] {
  if (!json) return [];
  let parsed: unknown;
  try {
    parsed = JSON.parse(json);
  } catch {
    return [];
  }
  if (!Array.isArray(parsed)) return [];
  const agents: ApprovedAcpAgent[] = [];
  for (const entry of parsed) {
    if (
      typeof entry === "object" && entry !== null &&
      typeof (entry as Record<string, unknown>).key === "string" &&
      typeof (entry as Record<string, unknown>).executablePath === "string" &&
      typeof (entry as Record<string, unknown>).protocolVersion === "number"
    ) {
      const e = entry as Record<string, unknown>;
      agents.push({
        key: e.key as string,
        executablePath: e.executablePath as string,
        args: Array.isArray(e.args) ? (e.args as string[]) : [],
        protocolVersion: e.protocolVersion as number,
        enabled: e.enabled !== false,
      });
    }
  }
  return agents;
}

export class AcpAgentRegistry {
  private readonly agents = new Map<string, ApprovedAcpAgent>();

  constructor(agents: ApprovedAcpAgent[]) {
    for (const agent of agents) this.agents.set(agent.key, agent);
  }

  list(): ApprovedAcpAgent[] {
    return [...this.agents.values()];
  }

  /** Resolves a registry key to its approved executable — throws
   * AcpAgentNotApprovedError for an unknown key OR a known-but-disabled
   * one (both fail closed identically; a caller never learns whether a
   * key is "unknown" vs. "known but disabled," which would leak registry
   * contents to an unauthorized caller). */
  resolve(key: string): ApprovedAcpAgent {
    const agent = this.agents.get(key);
    if (!agent || !agent.enabled) {
      throw new AcpAgentNotApprovedError(`"${key}" is not an approved, enabled ACP agent.`);
    }
    return agent;
  }
}
