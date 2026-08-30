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
  const [apiVersions, users] = await Promise.all([
    api.prompts.list({ agent_key: params.agentKey }),
    api.users.list(),
  ]);
  if (apiVersions.length === 0) notFound();

  const agent = toAgentDefinitionSummary(apiAgent);
  const versions = apiVersions.map(toAgentPromptVersion);
  // Prompt updates are Admin-only (see apps/api/app/services/permissions.py)
  // — no login exists yet, so this defaults to the first seeded user, same
  // convention used everywhere "created by"/"acting as" is needed without
  // auth. That user happens to be the seeded Admin.
  const currentUserId = users[0]?.id ?? null;

  return <PromptLibraryDetail agent={agent} initialVersions={versions} currentUserId={currentUserId} />;
}
