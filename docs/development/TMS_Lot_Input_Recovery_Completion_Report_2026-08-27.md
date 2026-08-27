# TMS Lot 人工补录恢复闭环完成报告（2026-08-27）

## 1. 完成结论

TMS 已实现并用真实浏览器、真实 FT Cleaner 子进程和 SQL Server 2014 数据链验证“缺 Lot → 用户补录 → 恢复原 Cleaner Release → Canonical → Dataset Current → 前端分析”的闭环。

本次完成不是把 Lot 直接写进结果表。缺 Lot 的原 Job 保留为 `NEEDS_INPUT`，用户的确认形成独立 enrichment 和审计记录，平台创建带 `parent_job_id` 的恢复 Job，再由同一已发布 Cleaner 使用按文件限定的 override 重跑。格式、厂家、产品、Lot 冲突和 Spec 仍必须通过原 Cleaner 与 FT Writer 校验。

结论边界：功能闭环已经实现并通过受控验收，但本文第 8 节列出的四项仍是生产上线门禁。

## 2. 本次完成内容

### 2.1 FT Cleaner 与子进程协议

- 日月新和日月光 TMS Adapter 在已批准文件名结构中只缺 Lot 时，不再把完整文件名当 Lot，也不返回普通格式成功；而是发出结构化 `LOT_ID` input-required marker。
- 人工 override 以原始文件名为键，只能应用于本次登记的精确输入文件。
- 如果文件名已经解析出 Lot，人工值必须相同；冲突时失败，不能覆盖解析事实。
- 多文件任务增加完整源覆盖校验，任一登记文件无有效数据时整批拒绝，不接受部分成功。
- FT 子进程使用 UTF-8 输出环境，中文 input-required marker 能被 TMS 稳定读取。

### 2.2 TMS 状态机、事务和审计

- Job 和 Import Batch 增加 `NEEDS_INPUT` 状态。
- 新增 `ingestion.processing_input_request`，按 receipt 保存缺字段请求、证据、状态和解决记录。
- 用户解决请求时，在一个 SQL 事务内完成 enrichment `FILL`、request `RESOLVED`、恢复子 Job 创建、Batch 返回 `QUEUED` 和 audit log。
- 被阻塞 Job 保持原状态；恢复 Job 通过 `parent_job_id` 指向它，保留不可变处理尝试链。
- 重复提交相同 request/Lot 返回已存在的恢复 Job；改变已解决 Lot、部分解决请求或并发状态变化均返回冲突。
- 上传和重新处理采用 Batch 锁、状态 CAS 和 Job 创建同一事务；普通重处理、通用 enrichment 和通用 Job 状态接口不能绕过专用 Lot 恢复入口。

### 2.3 API、权限和前端

- 新增待补录查询：`GET /api/v1/{domain}/{stage}/uploads/{batch_id}/input-requests`。
- 新增保存并恢复：`POST /api/v1/{domain}/{stage}/uploads/{batch_id}/input-requests/resolve`。
- 查询要求 `DATASET_READ`，解决要求 `TASK_CREATE`；普通用户只能处理自己的 Batch，`SYSTEM_ADMIN` 保留管理范围。
- CP/FT 上传任务页显示独立的“待补录批次”状态和按 Batch 去重的“补录批次号”操作。
- 弹窗按源文件逐项输入，可明确选择“这些文件属于同一 Lot”，并强制记录确认依据。
- 保存成功后前端关闭弹窗、刷新任务与结果；无 `TASK_CREATE` 权限时不显示补录操作；切换 CP/FT 路由会清除旧弹窗上下文。
- Dataset 分析页按所选 Lot 重新限定 FT Source 和参数，避免显示其他 Lot 的 Source/Spec。

### 2.4 输入与发布保护

- 上传文件名在同一批次内按不区分大小写检查唯一性，写入使用排他创建，避免同名覆盖。
- FT Worker 在运行前核对登记 SHA256，再把精确文件复制到隔离临时目录，并在副本上再次核对 SHA256。
- Cleaner Release 使用内容寻址不可变快照，Worker 按 Release checksum 验证包内容。
- 人工 Lot 只进入 enrichment 和 Cleaner override，不修改原文件；Canonical Lot、Source Run 和 Spec 仍来自重跑后的受校验 artifacts。

## 3. 真实浏览器与 SQL 证据

本次验收使用受控临时输入目录，不使用任意服务器业务目录。浏览器完成上传、等待输入、打开补录弹窗、填写确认依据、保存并观察恢复处理与分析结果；随后以 SQL 只读查询对账状态和数量。

### 3.1 日月新正向闭环：Batch 51

| 证据点 | 实际结果 |
|---|---|
| Import Batch | 51 |
| 初始处理 Job | 66，最终保持 `NEEDS_INPUT` |
| Input Request | 7，最终 `RESOLVED` |
| 人工 enrichment | 16，`action=FILL`，Lot=`FA53-4115` |
| 恢复 Job | 67，`parent_job_id=66`，最终 `SUCCESS` |
| Dataset | 22 / Version 1，`PUBLISHED` 且 Current |
| Canonical Test Run | 186 |
| Unit | 4,962 |
| Measurement | 89,316 |
| 数量关系 | 4,962 Unit × 18 参数 = 89,316 Measurement |
| 原文件 SHA256 | `C0894974020EB652815051FADCF01D3757DFC60FC25542B157E85A6D95D74529` |
| 原文件完整性 | 补录与重跑前后 SHA256 不变 |

这条链证明：Job 66 没有被改写成成功；Request 7 与 Enrichment 16 保留人工事实；Job 67 明确继承 Job 66；正式数据由恢复 Job 重新清洗后写入 Run 186，并形成 Dataset 22/v1 Current。浏览器能够从正式结果进入该 Dataset 的分析上下文。

### 3.2 日月光正向闭环：Batch 52

第二条正向链使用真实日月光 DC 文件的字节级副本，仅从副本文件名去掉 Lot；浏览器仍完整执行上传、待补录、人工确认、重新处理和分析图表验收。

| 证据点 | 实际结果 |
|---|---|
| Import Batch | 52 |
| 初始处理 Job | 68，最终保持 `NEEDS_INPUT` |
| Input Request | 8，最终 `RESOLVED` |
| 人工 enrichment | 17，`action=FILL`，Lot=`FA54-9744` |
| 恢复 Job | 69，`parent_job_id=68`，最终 `SUCCESS` |
| Cleaner Release | 29，日月光发布快照 |
| Dataset | 23 / Version 1，`PUBLISHED` 且 Current |
| Canonical Test Run | 187 |
| Unit | 3,900 |
| Measurement | 93,600 |
| 数量关系 | 3,900 Unit × 24 参数 = 93,600 Measurement |
| 原文件 SHA256 | `C36A3E064FF980818A78868295B1410387E5EF5F6C3724B81CBBA4AE23157D92` |
| 原文件完整性 | 原件、去 Lot 文件名副本及重跑后 SHA256 一致 |

浏览器分析页显示产品 `NCEA75ED120BT(LA)-3B00`、Lot `FA54-9744`、24 个参数、3,900 个测量单元，以及对应单位、规格线和测试条件；选择 Lot 后 Source 下拉项只保留该 Lot 的源文件。

### 3.3 负向验收：Batch 50

Batch 50 故意误选厂家。系统按 Factory、Adapter 和真实文件格式合同严格拒绝，没有把厂家/格式错误误报成“缺 Lot”，没有开放人工补录绕过入口，也没有发布 Dataset。

该证据确认 `NEEDS_INPUT` 只用于可证明的 Lot 缺失；未知格式、误选厂家、产品冲突和 Spec 冲突仍失败关闭。

## 4. Lot 与 Spec 对账结论

1. 用户补录的是指定 receipt/source file 的 Lot，不是 Batch 级无条件默认值。
2. 恢复 Job 复用阻塞 Job 的 Cleaner Release，避免补录后换用未经批准的新解析规则。
3. Cleaner 把最终 Lot 同时写入 cleaned data、scatter data、scatter spec 和 manifest；FT Writer 校验四类 artifact 的 Lot、Source 和行数一致性。
4. Spec 继续按 Source Run/Lot 的规格指纹绑定。不同规格使用不同 Program Version/Spec Set，不因人工补录而共享第一个文件的 Spec。
5. Dataset 分析查询先按 Lot 限定 Source，再读取相应 Program Version 的单位、上下限和测试条件。

## 5. 自动化验证

2026-08-27 在当前共享工作树复跑结果：

| 范围 | 结果 |
|---|---|
| TMS 后端全量 | 198 passed，4 个既有 openpyxl 弃用 warning |
| TMS 前端 Vitest 全量 | 12 test files、30 tests passed |
| TMS 前端生产构建 | 通过；仅有既有大分包提示 |
| FT 全量 unittest | 109 tests passed |
| FT `tests.test_tms_ft_factory_adapters` | 10 tests passed（包含在全量 109 项中） |

FT Adapter 测试包括：缺 Lot 必须显式 override、override 不得替换已解析 Lot、未知文件名不能误报为缺 Lot、日月新两种已批准文件名方向、日月光人工 Lot 同步进入 data/spec/manifest 且原文件不变、日月光未知头部失败关闭，以及多文件部分成功拒绝。

前端测试输出仍有 jsdom 不实现 pseudo-element `getComputedStyle` 的提示，但没有失败用例。

## 6. 做得较好的地方

- 把“可恢复的缺 Lot”和“不可恢复的格式/厂家错误”分开。Batch 51、52 与 Batch 50 的正负验收共同证明失败分类没有被放宽。
- 没有改写成熟 Cleaner 的参数、单位、清洗或 Spec 逻辑；TMS 只增加显式 marker/override Adapter 和恢复编排。
- 原 Job、人工输入、恢复 Job、Canonical Run 和 Dataset Version 都有独立 ID，可以完整追溯。
- Owner、权限、文件范围、SHA256、不可变 Release 和事务幂等同时落地，不只实现一个前端输入框。
- 人工 Lot 必须经过 Cleaner 和 Writer 重验，未直接覆盖源解析事实或已发布 Measurement。

## 7. 不确定性与明确边界

- 当前真实浏览器闭环分别覆盖日月新和日月光 FT。CP 正式支持厂家在现有已批准格式中能够取得 Lot；未来若出现 CP 缺 Lot，应先让对应 CP Adapter 发出同一严格 marker，再做独立真实样本验收。
- 已解决的 Lot 请求当前不可在原 Batch 内修改。若用户输入错误，安全路径是重新上传并形成新的处理链，不能直接改 Dataset Current。
- 本次证明了受控目录中的真实链路，不代表任意服务器路径入口已经满足生产安全要求。
- Dataset Current 最终发布与 Job/Batch/结果摘要仍跨事务，极端故障窗口需要后续收敛。

## 8. 残余生产门禁

1. **任意绝对 `source_path`**：改为受管 Source Catalog 或明确允许根目录，并验证解析后路径、符号链接/联接点和下载范围。
2. **跨事务最终发布**：把 Canonical/Dataset Current、结果摘要、Batch 和 Job 最终状态改为 staged + 幂等 finalize，或提供可靠补偿与一致性对账。
3. **多文件 `processing_run` 只关联首来源**：增加 processing-run/source 映射，确保每份源文件都有数据库级 lineage 和 parsed-Lot 防覆盖证据。
4. **补录无原批次纠正**：在纠正机制完成前明确要求重传；如后续允许纠正，必须新增 superseding enrichment、恢复 Job 和 Dataset Version，不能原地改历史事实。

## 9. 下一步

1. 优先关闭绝对 `source_path` 和跨事务发布两个生产阻断项。
2. 补齐多文件 source lineage 后，增加非首源文件 parsed-Lot 防覆盖 SQL 验收。
3. 决定是否建设受审计的 Lot 纠正流程；未批准前继续执行“补错必须重传”。
4. 对未来每个新 FT 厂家分别补真实浏览器 Golden Case，不把日月新/日月光验收自动外推到新格式。
