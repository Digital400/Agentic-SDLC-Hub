import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { REVIEW_CHECKLIST_ITEMS } from "@/lib/mock-data";

// 3. Reviewer checklist. Approval is gated on every box being checked
// (enforced by the parent, which owns `checked`) — a deliberate
// process-control measure: the reviewer must affirmatively confirm each
// criterion rather than the UI just trusting a single "Approve" click.
export function ReviewerChecklist({
  checked,
  onToggle,
}: {
  checked: Record<string, boolean>;
  onToggle: (id: string) => void;
}) {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-sm">Reviewer checklist</CardTitle>
        <CardDescription>Confirm each item before approving.</CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-2.5">
        {REVIEW_CHECKLIST_ITEMS.map((item) => (
          <label key={item.id} className="flex items-start gap-2 text-sm">
            <input
              type="checkbox"
              checked={checked[item.id] ?? false}
              onChange={() => onToggle(item.id)}
              className="mt-0.5 h-4 w-4 rounded border-input"
            />
            {item.label}
          </label>
        ))}
      </CardContent>
    </Card>
  );
}
