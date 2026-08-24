# TMS Route A A2 华虹 CP 跑通报告（2026-08-24）

## 结论

华虹 CP 已从“工程/量产 CP 上传”跑通到 Cleaner、Canonical 入库、Dataset Current、数据结果和分析图表。CP 与 FT 仍是两个独立程序；四个上传入口直接确定工程/量产及 CP/FT，不增加统一识别层。

## 本次完成

1. 新增 `sql2014_0011`，为 Dataset Version 绑定 Spec Set，并让处理结果关联 Dataset/Version。
2. 新增 `CP_CSV_TRIPLET_V1` Writer，严格读取现有 Cleaner 的 cleaned、yield、spec CSV。
3. 将 CP 数据写入唯一 Canonical：`test.test_run`、`test.unit_result`、`test.measurement`。
4. 按业务规则使用排序后第一批次 Spec，并记录 `FIRST_BATCH` 规则；不设计多批次 Spec 合并。
5. 首次成功后自动发布 Dataset Current，结果页可一键进入对应版本的数据分析。
6. “重新处理”也改为排队并复用同一条 Worker/Writer 链路。

## 真实数据验收

已有工程 CP 批次 10 已回填并保留为 Dataset 9 Version 1：

```text
Product=NCETEN30CAC
Lot=FA5X-2565
Wafer=25
Die=3,875
Pass=3,775
Fail=100
Yield=97.419355%
Test Items=14
Measurements=54,250
Spec Set=1
Dataset Status=PUBLISHED / Current=True
```

界面实测：从工程 CP 结果点击“数据分析”后自动载入 Dataset 9 Version 1；选择 Lot `FA5X-2565`、Wafer 1 后显示 155 Die、152 Pass、3 Fail、Yield 98.064%，Yield 趋势、Bin Pareto、Bin Map 和 Wafer Map 均正常渲染。

## 验证结果

```text
backend unit tests=74 passed
frontend tests=13 passed
frontend production build=PASS
route_a_schema=PASS
route_a_cleaner_registry=PASS
route_a_initial_worker=PASS
route_a_worker_lease_recovery=PASS
targeted ruff（忽略既有 FastAPI B008 基线）=PASS
```

前端大包告警按已冻结决定留到功能完成后优化，不阻塞本次跑通。

## 下一步

下一条链路是日月新 FT 正式结构化入库。FT 继续使用独立 Cleaner、独立 Output Adapter 和 FT 明细语义，不与 CP 强行统一。
