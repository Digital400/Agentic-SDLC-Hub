import { PageHeader } from "@/components/layout/page-header";
import { IntegrationsSection } from "@/components/integrations/integrations-section";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import { Separator } from "@/components/ui/separator";
import { api } from "@/lib/api";
import { toIntegrationItem } from "@/lib/mappers";

export default async function SettingsPage() {
  const apiIntegrations = await api.integrations.list();
  const integrations = apiIntegrations.map(toIntegrationItem);

  return (
    <div>
      <PageHeader title="Settings" description="Organization defaults for how projects run." />

      <div className="max-w-2xl">
        <div className="flex flex-col gap-6">
          <Card>
            <CardHeader>
              <CardTitle className="text-base">General</CardTitle>
              <CardDescription>Basic organization details.</CardDescription>
            </CardHeader>
            <CardContent className="space-y-4">
              <div>
                <label className="mb-1 block text-sm font-medium">Organization name</label>
                <Input defaultValue="Agentic SDLC Hub" disabled />
              </div>
              <div>
                <label className="mb-1 block text-sm font-medium">Default workflow template</label>
                <Select defaultValue="default-sdlc-workflow" disabled>
                  <option value="default-sdlc-workflow">Default SDLC Workflow (11 stages)</option>
                </Select>
                <p className="mt-1 text-xs text-muted-foreground">
                  Multiple workflow templates are not supported yet — see docs/mvp-plan.md.
                </p>
              </div>
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle className="text-base">Notifications</CardTitle>
              <CardDescription>What you get notified about — not wired up yet, preview only.</CardDescription>
            </CardHeader>
            <CardContent>
              <div className="flex flex-col gap-3">
                {[
                  { label: "A document is waiting for my review", defaultChecked: true },
                  { label: "A project I own moves to a new stage", defaultChecked: true },
                  { label: "An agent run fails", defaultChecked: false },
                ].map((item) => (
                  <label key={item.label} className="flex items-center gap-2 text-sm">
                    <input type="checkbox" defaultChecked={item.defaultChecked} className="h-4 w-4 rounded border-input" />
                    {item.label}
                  </label>
                ))}
              </div>
            </CardContent>
          </Card>

          <Separator />

          <div className="flex justify-end">
            <Button disabled>Save changes</Button>
          </div>
        </div>
      </div>

      <Separator className="my-8" />

      <div>
        <h2 className="text-base font-semibold">Integrations</h2>
        <p className="mb-4 mt-1 text-sm text-muted-foreground">
          External systems agents will be able to reach through MCP tools — see docs/architecture.md. GitHub is the
          first real connection (read-only repository scan); the rest don&apos;t connect yet.
        </p>
        <IntegrationsSection integrations={integrations} />
      </div>
    </div>
  );
}
