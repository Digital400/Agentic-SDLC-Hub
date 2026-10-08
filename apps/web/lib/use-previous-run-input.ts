"use client";

import { useEffect, useState } from "react";

import { api } from "@/lib/api";

export interface PreviousRunInput {
  loading: boolean;
  /** The freeform inputs (e.g. the stakeholder request) of this stage's most
   * recent run, exactly as the user typed them. Empty when none is found. */
  values: Record<string, string>;
  /** Answers already given in earlier clarification rounds. */
  priorAnswers: string;
}

// A clarification round is a brand-new agent run that doesn't inherit the
// previous run's input server-side, so the UI reads it back from that run and
// shows/re-sends it — the user should never have to retype what they already
// provided.
export function usePreviousRunInput(projectId: string, workflowNodeId: string, freeformKeys: string[]): PreviousRunInput {
  const [state, setState] = useState<PreviousRunInput>({ loading: true, values: {}, priorAnswers: "" });
  const keysSignature = freeformKeys.join("|");

  useEffect(() => {
    let cancelled = false;
    api.projects
      .agentRuns(projectId)
      .then((runs) => {
        if (cancelled) return;
        const forNode = runs
          .filter((r) => r.workflow_node_id === workflowNodeId && r.action === "draft")
          .sort((a, b) => (a.created_at < b.created_at ? 1 : -1));
        const latest = forNode.find((r) =>
          freeformKeys.every((k) => typeof r.input_context?.[k] === "string" && (r.input_context[k] as string).trim() !== ""),
        );
        const values: Record<string, string> = {};
        if (latest) for (const k of freeformKeys) values[k] = latest.input_context[k] as string;
        const answers = latest?.input_context?.["clarification_answers"];
        setState({ loading: false, values, priorAnswers: typeof answers === "string" ? answers : "" });
      })
      .catch(() => {
        if (!cancelled) setState({ loading: false, values: {}, priorAnswers: "" });
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- keysSignature stands in for freeformKeys
  }, [projectId, workflowNodeId, keysSignature]);

  return state;
}
