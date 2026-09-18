import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { STAGE_FIELD_SCHEMAS } from "@/lib/structured-actions/schemas";

import { StructuredActionForm } from "./structured-action-form";

describe("StructuredActionForm", () => {
  const lldSchema = STAGE_FIELD_SCHEMAS.lld!;

  it("renders a labeled field for every entry in the schema", () => {
    render(<StructuredActionForm schema={lldSchema} onSubmit={vi.fn()} />);
    for (const field of lldSchema) {
      expect(screen.getByLabelText(new RegExp(field.label, "i"))).toBeInTheDocument();
    }
  });

  it("blocks submission and shows an error when a required field is empty", () => {
    const onSubmit = vi.fn();
    render(<StructuredActionForm schema={lldSchema} onSubmit={onSubmit} />);
    fireEvent.click(screen.getByRole("button", { name: /generate workpacket/i }));
    expect(onSubmit).not.toHaveBeenCalled();
    expect(screen.getByText(/selected story is required/i)).toBeInTheDocument();
  });

  it("submits the built input_context once required fields are filled", () => {
    const onSubmit = vi.fn();
    render(<StructuredActionForm schema={lldSchema} onSubmit={onSubmit} />);
    fireEvent.change(screen.getByLabelText(/selected story/i), { target: { value: "Password Reset" } });
    fireEvent.click(screen.getByRole("button", { name: /generate workpacket/i }));
    expect(onSubmit).toHaveBeenCalledWith(expect.objectContaining({ selected_story: "Password Reset" }));
  });

  it("shows a WorkPacket preview containing the structured field values, not raw prompt text", () => {
    render(<StructuredActionForm schema={lldSchema} onSubmit={vi.fn()} />);
    fireEvent.change(screen.getByLabelText(/selected story/i), { target: { value: "Password Reset" } });
    fireEvent.click(screen.getByRole("button", { name: /preview workpacket/i }));
    const preview = screen.getByTestId("workpacket-preview");
    expect(preview.textContent).toContain("Password Reset");
    expect(preview.textContent).toContain("selected_story");
  });

  it("does not show the preview when a required field is still missing", () => {
    render(<StructuredActionForm schema={lldSchema} onSubmit={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: /preview workpacket/i }));
    expect(screen.queryByTestId("workpacket-preview")).not.toBeInTheDocument();
  });

  it("truncates the additional-context field at the documented max length via the maxLength attribute", () => {
    render(<StructuredActionForm schema={lldSchema} onSubmit={vi.fn()} />);
    const textarea = screen.getByLabelText(/additional context/i) as HTMLTextAreaElement;
    expect(textarea.maxLength).toBe(500);
  });

  it("supports a multiselect field by joining selected values with a comma", () => {
    const onSubmit = vi.fn();
    render(<StructuredActionForm schema={STAGE_FIELD_SCHEMAS.testing!} onSubmit={onSubmit} />);
    fireEvent.click(screen.getByLabelText(/^unit$/i));
    fireEvent.click(screen.getByLabelText(/^integration$/i));
    fireEvent.click(screen.getByRole("button", { name: /generate workpacket/i }));
    expect(onSubmit).toHaveBeenCalledWith(expect.objectContaining({ test_types: "unit,integration" }));
  });
});
