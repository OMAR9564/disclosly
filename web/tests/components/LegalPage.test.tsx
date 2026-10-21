import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it } from "vitest";

import { LegalPage } from "@/components/LegalPage";
import { type LegalDoc, loadLegal } from "@/lib/legal/content";
import { parseBlocks, renderInline, stripComments } from "@/lib/markdown";

beforeEach(() => window.localStorage.clear());

// Statements each page must make (FR-026, spec US4), per language.
const REQUIRED: Record<LegalDoc, { tr: RegExp[]; en: RegExp[] }> = {
  privacy: {
    tr: [
      /KVKK/,
      /e-posta adresin ve görünen adın/,
      /kamuya açık/,
      /eğitmek için kullanabilir/,
      /privacy@disclosly\.com/,
    ],
    en: [
      /KVKK/,
      /email address and display name/,
      /public company disclosures/,
      /use the text they receive to train/,
      /privacy@disclosly\.com/,
    ],
  },
  terms: {
    tr: [/Kullanım koşulları/, /yatırım tavsiyesi vermez/, /soru sınırı/],
    en: [/Terms of use/, /not investment advice/, /question limit/],
  },
  disclaimer: {
    tr: [
      /Yatırım tavsiyesi değildir/,
      /lisanslı bir yatırım danışmanı değildir/,
    ],
    en: [/Not investment advice/, /not a licensed investment adviser/],
  },
};

describe.each(Object.keys(REQUIRED) as LegalDoc[])("%s page", (doc) => {
  it("renders the required statements in Turkish, then English", () => {
    render(<LegalPage content={loadLegal(doc)} />);
    const article = () => screen.getByRole("article").textContent ?? "";
    for (const pattern of REQUIRED[doc].tr) expect(article()).toMatch(pattern);
    expect(screen.getByRole("article").getAttribute("lang")).toBe("tr");

    fireEvent.click(screen.getByRole("button", { name: "English" }));
    for (const pattern of REQUIRED[doc].en) expect(article()).toMatch(pattern);
    expect(screen.getByRole("article").getAttribute("lang")).toBe("en");
  });

  it("drops owner notes and links to every legal page", () => {
    render(<LegalPage content={loadLegal(doc)} />);
    expect(document.body.textContent).not.toMatch(/OWNER|T038/);
    const hrefs = screen
      .getAllByRole("link")
      .map((link) => link.getAttribute("href"));
    for (const href of ["/privacy", "/terms", "/disclaimer"])
      expect(hrefs).toContain(href);
  });
});

describe("markdown subset", () => {
  it("parses headings, paragraphs and lists", () => {
    expect(
      parseBlocks("# T\n\nline one\nline two\n\n- a\n  more\n- b\n\n## S"),
    ).toEqual([
      { kind: "h1", text: "T" },
      { kind: "p", text: "line one line two" },
      { kind: "ul", items: ["a more", "b"] },
      { kind: "h2", text: "S" },
    ]);
  });

  it("strips notes, nested and unclosed ones included", () => {
    expect(stripComments("a<!-- x -->b<!--<!-- y -->-->c")).toBe("ab-->c");
    expect(stripComments("a<!-- open")).toBe("a");
    // Removing one note must not join "<" and "!--" into a new one.
    expect(stripComments("<<!-- -->!-- z")).toBe("");
  });

  it("links only same-site paths", () => {
    const { container } = render(
      <p>
        {renderInline("[in](/terms) [out](https://evil.example) `c` **b**")}
      </p>,
    );
    const links = container.querySelectorAll("a");
    expect(links).toHaveLength(1);
    expect(links[0].getAttribute("href")).toBe("/terms");
    expect(container.textContent).toBe("in out c b");
  });
});
