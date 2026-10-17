import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import RootLayout from "@/app/layout";
import Home from "@/app/page";

describe("placeholder app", () => {
  it("renders the product name", () => {
    render(<Home />);
    expect(screen.getByRole("heading", { name: "Disclosly" })).toBeDefined();
  });

  it("defaults the document language to Turkish", () => {
    const html = RootLayout({ children: null, params: Promise.resolve({}) });
    expect(html.props.lang).toBe("tr");
  });
});
