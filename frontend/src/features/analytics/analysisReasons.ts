const explanations: Record<string, string> = {
  CAPABILITY_RULE_REQUIRED: "尚未配置适用的能力分析规则，请联系规则维护人员；可以继续查看描述统计。",
  FORMAL_RELEASED_SPEC_NOT_FOUND: "没有已发布的正式规格，暂不能计算超规或过程能力。",
  FORMAL_SPEC_CURRENT_EVALUATION_AMBIGUOUS: "当前规格匹配到多个结果，需要先确认唯一适用规格。",
  FORMAL_SPEC_PROVENANCE_INVALID: "规格来源记录不完整，需要先核对规格依据。",
  ANALYSIS_SPEC_INCOMPATIBLE: "所选数据的规格不兼容，请按规格分别分析。",
  ANALYSIS_CAPABILITY_UNAVAILABLE: "当前数据不具备此分析所需条件，请检查规格、规则和有效样本。",
  INSUFFICIENT_SAMPLES: "有效样本不足，请扩大筛选范围或补充数据。",
  INSUFFICIENT_SAMPLE_SIZE: "有效样本不足，请扩大筛选范围或补充数据。",
  ZERO_VARIANCE: "数据没有变化，无法进行需要非零标准差的计算。",
  NON_POSITIVE_STDDEV: "标准差为零或不可用，无法进行拟合或能力计算。",
  NO_RELATIONSHIP_POINTS: "筛选后没有可配对的测量值，请检查参数与测试条件。",
};

export function explainAnalysisReason(code: string): string {
  // Keep unknown backend reasons visible; never convert them to success.
  return explanations[code] ? `${explanations[code]}（${code}）` : code;
}
