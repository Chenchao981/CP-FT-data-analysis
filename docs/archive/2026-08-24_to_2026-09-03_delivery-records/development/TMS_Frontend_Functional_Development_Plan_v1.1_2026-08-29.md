# TMS v1.1 功能闭环开发计划

- 日期：2026-08-29
- 依据：`TMS_Business_Requirements_v0.2.md`、TMS v1.0 Core 完成/回归报告、2026-08-29 第一性原理评估
- 执行顺序：分析 → 评估 → 计划冻结 → 开发 → 目标测试 → 全量回归 → 开发库灰度 → 报告
- 安全边界：本轮不新增安全项目；既有认证、RBAC、Owner、Source Catalog、Manifest、秘密扫描和失败关闭不得回退

## 1. 目标与完成定义

本计划把 v1.0 Core 从“可运行的数据治理控制台”推进为“一线用户无需内部 ID 即可完成正式数据任务，领导可以从核心指标下钻”的 v1.1 功能闭环。

“本轮完成”只表示：

- 计划范围内的仓库代码、Migration、测试、构建、真实开发库验证和本机浏览器回归通过；
- 没有未说明的功能 P0/P1 缺陷；
- 形成可供小组灰度的交付物和回退说明。

它不等于正式生产上线。生产服务器、真实服务账号、网络/证书、生产备份恢复和业务签字仍是外部门。

## 2. 事实、假设与开放门

### 2.1 已确认事实

- 唯一正式 Canonical 明细链为 `test.test_run -> test.unit_result -> test.measurement`；Quick Workspace 不得写入该链。
- 华虹 CP、日月新/日月光 FT 已有受控开发库链路；其他厂家不能因代码存在而外推为真实 Route A 已验收。
- 当前后端自动化基线为 393 passed、1 skipped；前端本轮只读审计实跑为 25 files、91 tests PASS。
- v1.0 G0-G2 已通过，G3/G4 未执行，生产未上线。
- 当前浏览器首屏和代码审计均确认分析手填 ID、管理页信息过载和 Quick Manifest 缺失。

### 2.2 本计划采用的假设

- 正式数据图表读取属于 `DATASET_READ`，创建 Quick 计算任务属于 `ANALYSIS_RUN`。
- 多 Dataset 比较首版最多选择 8 个 Current Dataset，超限明确阻止；所有计算在服务端完成。
- 明细查询单页最大 200 行，导出仍走受控 Job/Artifact，不把百万行装入浏览器。
- 多 Lot 同 Spec 的“相同”至少包含参数名、单位、测试条件、LSL/USL 的规范化一致；任一冲突即失败关闭。
- Product 补录是对任务/Dataset 的业务增强值，不改写 Cleaner 原始解析值，并保留变更历史。
- 时间筛选使用 Asia/Shanghai 业务时间，提交 API 前转换为 UTC，采用左闭右开范围。

### 2.3 开放门

- PAT/Cpk/SPC 的正式业务口径和图表优先级需业务批准；本轮不自行发明。
- 生产质量摘要性能 SLO 需用目标数据量和正式 SQL Server 实测确认；开发库候选目标只用于灰度。
- 新厂家真实验收需要样本、批准格式和 Golden 对账。

## 3. 工作包与关闭条件

### F0：计划冻结与基线留证

交付：

- 第一性原理评估；
- 奥卡姆范围裁剪；
- 本开发计划；
- 当前 Git、测试、页面和数据库事实留证。

关闭条件：评估与计划独立提交并推送，之后才允许开始功能代码修改。

### F1：功能正确性与可复现运行

任务：

1. CP Wafer 良率改为 `PASS/(PASS+FAIL)`，增加 unknown_count，零已知分母返回空值；
2. 建立统一 UTF-8 JSON 状态读取函数，修复 start/status/stop 跨 PowerShell 5.1/7；
3. 修复 `REPROCESS_BATCH` 前后端动作合同；
4. 分析查看权限统一为 `DATASET_READ`；
5. `/`、`/engineering`、`/production` 进入当前角色第一个可访问叶子页，显式越权仍 403。

测试：后端口径单元测试、API Schema 测试、前端权限/路由/动作测试、PowerShell 双版本交叉读写和正常停启。

关闭条件：五个已确认缺陷均有失败用例、修复和回归证据。

### F2：一线正式任务闭环

任务：

1. 提交成功后自动打开返回的 Job，并突出刚提交批次；
2. Stage 工作台的 Tab、筛选、页码和 Job 深链写入 URL；
3. 时间输入替换为上海本地日期范围和常用预设；
4. 错误反馈保留 HTTP 状态、错误码、可重试性和建议动作；
5. 宽表首屏保留 Product、Lot、厂家、状态、时间、下一步，其余进入详情/技术信息；
6. Product 缺失在当前 Job/Dataset 详情中可填写或跳过，人工值与 Cleaner 值分离并可追溯；
7. 同 Spec 多 Lot CP 通过严格兼容性校验后入库，不同 Spec 负向用例继续失败关闭。

关闭条件：一线用户无需 SQL 和内部 ID 即可完成提交、待补录、失败恢复、成功结果和 Product 补录。

### F3：历史正式数据与二次分析

任务：

1. Dataset Current 更名为“历史正式数据”，以业务列为主并提供详情抽屉/路由；
2. 查询补充上传任务、Wafer、Cleaner、Owner（管理员）、Product 和其他已保存字段；
3. 表格支持最多 8 个 Dataset 多选并进入分析；
4. 新增受控服务端查询合同：Dataset refs、Lot 列表、Wafer 列表、Bin 列表、参数列表、分页；
5. 返回能力/兼容性信息，Spec 不兼容、无 Bin、无坐标或无 PASS/FAIL 时明确说明；
6. CP/FT 保持独立图表，增加真实结构化明细分页；
7. URL 保存 Dataset 选择和分析筛选，刷新、后退、深链可恢复；
8. 移除用户手填 Dataset ID 的主路径，顶层分析入口合并到历史数据和结果上下文。

关闭条件：用户按业务字段找到数据，多选后按 Lot/Wafer/Bin/参数由数据库重算，并查看分页明细；网络响应和导出不依赖前端隐藏行。

### F4：Quick PAT 与领导视图简化

Quick PAT：

1. 运行前调用 Source Catalog 构建递归 Manifest 预览；
2. 显示文件数、总字节、识别工具和不支持原因；
3. 会话使用服务端分页和状态/日期筛选；
4. 新任务置顶，下载失败/过期转为可见错误和恢复动作。

领导视图：

1. 首屏只突出良率、UNKNOWN、异常、数据新鲜度等核心决策指标；
2. 使用现有趋势数据绘制真实趋势图；
3. 方法代码和厂家/状态值转为中文业务语言；
4. 方法说明、次级统计和分解表默认折叠或按 Tab 懒加载；
5. 分析和 Job 下钻使用当前查看权限，不出现合法用户 403。

关闭条件：Quick 用户提交前知道实际处理范围；领导首屏能回答四个核心问题并下钻。

### F5：功能债务收口

任务：

- 确认并移除不再使用的 `StageIntakeWorkbench`、`DatasetReview`、`JobWorkbench`、`HuaHongInspector` 及仅为它们服务的旧样式/测试/API；
- 合并“能力中心”说明到正式入口帮助；
- SHA、Release、Intent、Worker、血缘和生命周期方法默认渐进披露；
- 不进行 UI 框架、状态框架或微前端重写。

关闭条件：生产构建和路由不再携带已替代的人工发布/手填内部 ID 业务入口；必要的底层 API 若仍有后台用途则保留并登记。

### F6：测试、灰度、报告与交付

执行顺序：

1. 目标单元/组件/API 合同测试；
2. 前端全量测试、TypeScript、生产构建；
3. 后端全量测试和 Migration head 校验；
4. `TMS_G0_DEV` 真实 SQL 对账；
5. 本机免登录任务型浏览器 UAT；
6. 一名管理员和一名工程师认证模式非回归冒烟，不扩展安全范围；
7. 开发库小范围功能灰度、回退演练和报告；
8. 构建交付包，检查归档内容和启动入口；
9. 安全提交并推送源代码、Migration、测试、文档和发布物清单。

## 4. API 与数据合同

### 4.1 良率

```text
pass_count    = count(overall_result = PASS)
fail_count    = count(overall_result = FAIL)
unknown_count = total_count - pass_count - fail_count
known_count   = pass_count + fail_count
yield_rate    = null if known_count = 0 else pass_count / known_count
```

### 4.2 正式分析查询

请求至少包含：

```text
dataset_refs[] = {dataset_id, version_no}
lot_ids[]
wafer_ids[]
bin_codes[]
parameters[]
page, page_size
```

约束：

- Dataset 数量 1–8；
- 参数数量采用显式上限；
- 所有 Owner/RBAC 和 Current 约束由服务端重新验证；
- 图表聚合与明细使用同一筛选合同；
- 不兼容 Spec 不返回看似有效的合并结果。

### 4.3 Product 增强

- 保存 Cleaner 原值、人工有效值、修改前后、操作人和时间；
- Current 查询使用有效值；
- 重处理时仅在仍适用时继承，不覆盖 Cleaner 新提供的明确值；
- 跳过 Product 不阻止已有 Lot/Wafer/参数能力。

### 4.4 Quick Manifest

- 只接受 Source Catalog 的 `source_root_code + relative_path`；
- 预览与实际 Job 使用同一 Manifest 构建规则；
- 前端不自行递归文件系统；
- 预览返回文件数、字节数、识别能力和拒绝原因，不泄露根外路径。

## 5. 测试与回归矩阵

| 层次 | 场景 | 必须断言 |
|---|---|---|
| 后端单元 | CP Yield | UNKNOWN/ABORT 不进入 FAIL 和良率分母；零已知分母为 null |
| Writer | 华虹多 Lot | 同 Spec 通过并保留每行 Lot；任一参数/单位/条件/上下限冲突失败关闭 |
| API | 正式分析 | 多 Dataset/Lot/Wafer/Bin/参数与分页合同；Owner/Current/上限失败关闭 |
| API | Quick | Manifest 与实际 Job 一致；分页、筛选、过期下载 |
| 前端组件 | 路由权限 | 各角色默认首页；父路由；显式越权 403；管理查看可分析 |
| 前端组件 | 正式主线 | 提交自动打开 Job；URL 恢复；补录/重试/结果 |
| 前端组件 | 历史分析 | 业务筛选、多选、兼容性、明细分页、无内部 ID |
| 前端组件 | 管理/Quick | KPI 优先、趋势图、Manifest 确认、下载失败反馈 |
| PowerShell | 本机环境 | PS5 写/PS7 读、PS7 写/PS5 读、启动/状态/正常停止 |
| 真实 SQL | 数据口径 | 管理/CP/FT/明细总数、PASS/FAIL/UNKNOWN 相互对账 |
| 浏览器 | 工程师 | 入口→提交→Job→补录/恢复→正式数据→分析/明细 |
| 浏览器 | 领导 | 时间/Product/Lot/厂家→KPI/趋势→Dataset/Job 下钻 |
| 浏览器 | 恢复性 | 刷新、后退、深链、API 失败、下载过期后页面可恢复 |

## 6. 功能灰度 Gate

| Gate | 环境 | 通过条件 | 回退条件 |
|---|---|---|---|
| G0 | 静态/自动化 | 目标测试、全量前后端、TypeScript、Build、Migration 全绿 | 任一 P0/P1 回归、口径不一致、构建失败 |
| G1 | 本机免登录 | 四入口、Job、补录、历史分析、Quick、管理页任务型 UAT | 页面死链、必须手填内部 ID、下载/错误无反馈 |
| G2 | `TMS_G0_DEV` | 真实 CP/FT/Quick/质量摘要对账；正常启停；无永久破坏 | Canonical/Current 计数漂移、错误 Current 切换、状态无法恢复 |
| G2.5 | 本机认证冒烟 | 管理员、工程师合法路径可用，既有拒绝行为不回退 | 合法角色 403 或跨 Owner 可见 |
| G3 | 业务小组 | 选定产品/批次任务 UAT、性能 SLO、回退阈值和业务签字 | 数据口径、任务成功率、性能未达签字阈值 |
| G4 | 正式生产 | 生产部署、备份恢复、监控、发布窗口和最终签字 | 外部门不具备或回退演练失败 |

本轮可以完成 G0-G2.5 的仓库/开发库证据；没有新洁能正式环境和业务签字，不得宣称 G3/G4 完成。

## 7. 候选性能目标

- 普通交互反馈：≤ 300 ms；
- 常规目录/详情查询：开发库热查询 ≤ 3 s；
- 正式图表：开发库 ≤ 3 s；
- 质量摘要：开发库热查询 ≤ 3 s、冷查询候选 ≤ 5 s；
- 超过目标时先记录执行计划和逻辑读，再评估索引或一次性 scoped materialization；本轮不先建设复杂持久汇总/缓存层。

## 8. 变更控制

- F0 计划提交完成后才能进入 F1 开发；
- 每个功能缺陷先补失败测试，再修改实现；
- 未知 Spec、单位、身份、Owner、Source 或 Cleaner 语义继续失败关闭；
- 不修改原始数据；真实数据库破坏性动作仅做事务回滚或使用专用可恢复夹具；
- `.remember/`、原始数据、生成报告、日志、缓存、账号和秘密不得进入提交；
- 每个里程碑报告区分“做了什么、确定的、不确定的、验证、下一步”。

## 9. 最终交付物

- 本评估与开发计划；
- 功能代码、Migration、自动化测试；
- `TMS_Frontend_Functional_Completion_Report_2026-08-29.md`；
- `TMS_Frontend_Functional_Regression_Test_Report_2026-08-29.md`；
- 更新后的 README/使用指南/API 合同；
- 可复现启动/停止入口和经过归档检查的交付物；
- Git 提交与远端分支。
