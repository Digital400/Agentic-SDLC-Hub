"use client";

import { useState, type FormEvent } from "react";
import { useRouter } from "next/navigation";

import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import { api, ApiError } from "@/lib/api";

interface FormErrors {
  name?: string;
  businessOwner?: string;
  submit?: string;
}

// Calls the real `POST /projects` (see apps/api/app/api/routes/projects.py)
// — the workflow graph is generated server-side, with the first node
// ("Requirement Intake") set READY and every other node LOCKED.
export function CreateProjectForm({ createdById }: { createdById: string | null }) {
  const router = useRouter();
  const [name, setName] = useState("");
  const [businessOwner, setBusinessOwner] = useState("");
  const [description, setDescription] = useState("");
  const [errors, setErrors] = useState<FormErrors>({});
  const [submitting, setSubmitting] = useState(false);

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();

    const nextErrors: FormErrors = {};
    if (name.trim() === "") nextErrors.name = "Project name is required.";
    if (businessOwner.trim() === "") nextErrors.businessOwner = "Business owner is required.";
    if (createdById === null) nextErrors.submit = "No users exist yet to attribute this project to.";
    setErrors(nextErrors);
    if (Object.keys(nextErrors).length > 0) return;

    setSubmitting(true);
    try {
      const project = await api.projects.create({
        name: name.trim(),
        business_owner: businessOwner.trim(),
        description: description.trim() || undefined,
        created_by_id: createdById as string,
      });
      router.push(`/projects/${project.id}`);
      router.refresh();
    } catch (err) {
      setErrors({ submit: err instanceof ApiError ? err.message : "Failed to create project." });
      setSubmitting(false);
    }
  }

  return (
    <Card className="max-w-xl">
      <CardContent className="p-6">
        <form onSubmit={handleSubmit} className="flex flex-col gap-4" noValidate>
          <div>
            <label htmlFor="name" className="mb-1 block text-sm font-medium">
              Project name <span className="text-destructive">*</span>
            </label>
            <Input
              id="name"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="e.g. Customer Loyalty Rewards Platform"
              aria-invalid={Boolean(errors.name)}
            />
            {errors.name ? <p className="mt-1 text-xs text-destructive">{errors.name}</p> : null}
          </div>

          <div>
            <label htmlFor="businessOwner" className="mb-1 block text-sm font-medium">
              Business owner <span className="text-destructive">*</span>
            </label>
            <Input
              id="businessOwner"
              value={businessOwner}
              onChange={(e) => setBusinessOwner(e.target.value)}
              placeholder="e.g. Marketing — Jordan Lee"
              aria-invalid={Boolean(errors.businessOwner)}
            />
            {errors.businessOwner ? <p className="mt-1 text-xs text-destructive">{errors.businessOwner}</p> : null}
            <p className="mt-1 text-xs text-muted-foreground">
              The accountable stakeholder on the business side — not necessarily a platform user.
            </p>
          </div>

          <div>
            <label htmlFor="description" className="mb-1 block text-sm font-medium">
              Description
            </label>
            <Textarea
              id="description"
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              placeholder="What is this project for?"
              rows={4}
            />
          </div>

          <div>
            <label htmlFor="workflowTemplate" className="mb-1 block text-sm font-medium">
              Workflow template
            </label>
            <Select id="workflowTemplate" value="default-sdlc-workflow" disabled>
              <option value="default-sdlc-workflow">Default SDLC Workflow (11 stages)</option>
            </Select>
            <p className="mt-1 text-xs text-muted-foreground">
              Only one workflow template exists for now — every project starts at Requirement Intake.
            </p>
          </div>

          {errors.submit ? <p className="text-sm text-destructive">{errors.submit}</p> : null}

          <div className="mt-2 flex justify-end gap-2">
            <Button type="button" variant="outline" onClick={() => router.push("/projects")}>
              Cancel
            </Button>
            <Button type="submit" disabled={submitting}>
              {submitting ? "Creating…" : "Create project"}
            </Button>
          </div>
        </form>
      </CardContent>
    </Card>
  );
}
