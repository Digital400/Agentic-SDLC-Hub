import { notFound } from "next/navigation";

import { PromptLibraryDetail } from "@/components/prompts/prompt-library-detail";
import { api, ApiError } from "@/lib/api";
import { toAgentDefinitionSummary, toAgentPromptVersion } from "@/lib/mappers";

export default async function AgentPromptPage({ params }: { params: { agentKey: string } }) {
  let apiAgent;
  try {
    apiAgent = await api.agentDefinitions.get(params.agentKey);
  } catch (err) {
    if (err instanceof ApiError && err.status === 404) notFound();
    throw err;
  }

  // Every version in this agent's (draft-role) lineage — see
  // apps/api/app/api/routes/prompts.py's list endpoint.
  const apiVersions = await api.prompts.list({ agent_key: params.agentKey });
  if (apiVersions.length === 0) notFound();

  const agent = toAgentDefinitionSummary(apiAgent);
  const versions = apiVersions.map(toAgentPromptVersion);

  return <PromptLibraryDetail agent={agent} initialVersions={versions} />;
}
