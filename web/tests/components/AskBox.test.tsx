import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { AskBox, QUESTION_MAX } from "@/components/AskBox";
import { en } from "@/i18n/en";
import { tr } from "@/i18n/tr";

function setup(props: Partial<Parameters<typeof AskBox>[0]> = {}) {
  const onAsk = vi.fn();
  render(<AskBox t={tr} onAsk={onAsk} {...props} />);
  const field = screen.getByLabelText((props.t ?? tr).askLabel);
  return { onAsk, field };
}

describe("AskBox", () => {
  it("submits the trimmed question with the key", () => {
    const { onAsk, field } = setup();
    fireEvent.change(field, { target: { value: "  THYAO net kâr?  " } });
    fireEvent.click(screen.getByRole("button", { name: tr.askSubmit }));
    expect(onAsk).toHaveBeenCalledWith("THYAO net kâr?");
  });

  it("submits on Enter but not on Shift+Enter", () => {
    const { onAsk, field } = setup();
    fireEvent.change(field, { target: { value: "THYAO net kâr?" } });
    fireEvent.keyDown(field, { key: "Enter", shiftKey: true });
    expect(onAsk).not.toHaveBeenCalled();
    fireEvent.keyDown(field, { key: "Enter" });
    expect(onAsk).toHaveBeenCalledOnce();
  });

  it("rejects questions under 3 characters", () => {
    const { onAsk, field } = setup();
    fireEvent.change(field, { target: { value: " ab " } });
    fireEvent.click(screen.getByRole("button", { name: tr.askSubmit }));
    expect(onAsk).not.toHaveBeenCalled();
    expect(screen.getByRole("alert").textContent).toBe(tr.askTooShort);
    expect(field.getAttribute("aria-invalid")).toBe("true");
  });

  it("shows the counter, busy key and errors", () => {
    const { field } = setup({ busy: true, t: en, error: en.errorQuota });
    expect(field.getAttribute("maxLength")).toBe(String(QUESTION_MAX));
    expect(
      (screen.getByRole("button", { name: en.askSending }) as HTMLButtonElement)
        .disabled,
    ).toBe(true);
    expect(screen.getByRole("alert").textContent).toBe(en.errorQuota);
  });

  it("counts characters", () => {
    const { field } = setup();
    fireEvent.change(field, { target: { value: "abcd" } });
    expect(screen.getByText(`4/${QUESTION_MAX} ${tr.askCount}`)).toBeTruthy();
  });
});
