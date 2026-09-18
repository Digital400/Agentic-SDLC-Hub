/**
 * "Display tool and command approval requests locally" — the developer,
 * not the platform, is the approver for anything the local runtime wants
 * to run. Injectable so tests never touch a real TTY.
 */

export interface ApprovalRequest {
  kind: "tool" | "command";
  description: string;
}

export interface ApprovalPrompter {
  (request: ApprovalRequest): Promise<boolean>;
}

/** Real stdin-based prompter, used only outside tests. */
export function createStdinApprovalPrompter(): ApprovalPrompter {
  return async (request) => {
    const readline = await import("node:readline/promises");
    const rl = readline.createInterface({ input: process.stdin, output: process.stdout });
    try {
      const label = request.kind === "tool" ? "Tool request" : "Command";
      const answer = await rl.question(`${label}: ${request.description}\nApprove? [y/N] `);
      return answer.trim().toLowerCase() === "y";
    } finally {
      rl.close();
    }
  };
}
