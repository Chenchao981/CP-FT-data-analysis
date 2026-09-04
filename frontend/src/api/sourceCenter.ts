import {
  listFormalSourceRoots,
  listStageUploadsPage,
  type FormalSourceRoot,
  type StageUploadRow,
  type TestStage,
} from "./stageData";

const FACTORIES: Record<TestStage, readonly string[]> = {
  CP: ["huahong", "jetech", "lion"],
  FT: ["riyuexin", "riyueguang", "dianji"],
};

export interface SourceCenterSnapshot {
  roots: FormalSourceRoot[];
  recentImports: StageUploadRow[];
  unavailableQueries: number;
}

export async function getSourceCenterSnapshot(): Promise<SourceCenterSnapshot> {
  const rootRequests = (["CP", "FT"] as const).flatMap((stage) =>
    FACTORIES[stage].flatMap((factory) => (["ENGINEERING", "PRODUCTION"] as const)
      .map((domain) => listFormalSourceRoots(domain, stage, factory))),
  );
  const [rootResults, importResults] = await Promise.all([
    Promise.allSettled(rootRequests),
    Promise.allSettled(((["CP", "FT"] as const).map((stage) => listStageUploadsPage("ALL", stage, { page: 1, page_size: 50 })))),
  ]);
  const roots = Array.from(new Map(rootResults.flatMap((result) => result.status === "fulfilled" ? result.value : [])
    .map((root) => [root.code, root])).values()).sort((left, right) => left.test_stage.localeCompare(right.test_stage) || left.name.localeCompare(right.name, "zh-CN"));
  const recentImports = importResults.flatMap((result) => result.status === "fulfilled" ? result.value.items : [])
    .filter((row) => row.source_channel === "SOURCE_CATALOG")
    .sort((left, right) => right.upload_time_utc.localeCompare(left.upload_time_utc))
    .slice(0, 30);
  return {
    roots,
    recentImports,
    unavailableQueries: rootResults.filter((result) => result.status === "rejected").length,
  };
}
