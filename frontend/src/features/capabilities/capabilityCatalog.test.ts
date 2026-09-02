import { describe, expect, it } from "vitest";

import { factoryInputs, formalFactoryOptions, isFormalFactory } from "./capabilityCatalog";

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
      "dianji",
    ]);
    expect(isFormalFactory("CP", "guoyu")).toBe(false);
    expect(isFormalFactory("FT", "ASE")).toBe(false);
    expect(isFormalFactory("FT", "riyueguang")).toBe(true);
    expect(isFormalFactory("FT", "dianji")).toBe(true);
  });

  it("publishes the approved Dianji v2.19 input contract", () => {
    expect(factoryInputs.dianji.accept).toBe(".xls,.xlsx");
    expect(factoryInputs.dianji.hint).toContain("v2.20.0");
    expect(factoryInputs.dianji.hint).toContain("PowerTECH");
    expect(factoryInputs.dianji.hint).toContain("CONT");
  });
});
