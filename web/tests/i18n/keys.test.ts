import { describe, expect, it } from "vitest";

import { en } from "@/i18n/en";
import { tr } from "@/i18n/tr";

describe("dictionary keys", () => {
  it("tr and en have the same keys", () => {
    expect(Object.keys(en).sort()).toEqual(Object.keys(tr).sort());
  });

  it("no value is empty", () => {
    for (const dictionary of [tr, en])
      for (const [key, value] of Object.entries(dictionary))
        expect(value.trim(), key).not.toBe("");
  });
});
