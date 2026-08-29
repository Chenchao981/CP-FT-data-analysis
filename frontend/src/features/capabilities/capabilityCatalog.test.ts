import { describe, expect, it } from "vitest";

import { formalFactoryOptions, isFormalFactory } from "./capabilityCatalog";

describe("capability catalog", () => {
  it("keeps formal selectors limited to end-to-end validated factories", () => {
    expect(formalFactoryOptions.CP.map((item) => item.value)).toEqual([
      "huahong",
      "jetech",
      "lion",
    ]);
    expect(formalFactoryOptions.FT.map((item) => item.value)).toEqual([
      "riyuexin",
      "riyueguang",
    ]);
    expect(isFormalFactory("CP", "guoyu")).toBe(false);
    expect(isFormalFactory("FT", "ASE")).toBe(false);
    expect(isFormalFactory("FT", "riyueguang")).toBe(true);
  });
});
