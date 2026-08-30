# TMS v1.2 工程私有、量产共享与重复分析完成报告

- 完成日期：2026-08-30
- 实施基线：`TMS_Business_Requirements_v0.3.md`
- 方案基线：`TMS_Data_Visibility_Duplicate_Upload_First_Principles_Assessment_2026-08-30.md`
- 开发计划：`TMS_Data_Visibility_Duplicate_Upload_Development_Plan_v1.2_2026-08-30.md`
- 数据库版本：`TMS_G0_DEV / sql2014_0019`
- 本轮定位：功能与性能优先的 v1.2 Functional RC；安全专项、目标服务器和生产发布不在本轮完成声明内

## 1. 结论

本轮功能性目标已经形成可运行闭环：

1. 工程数据由 SQL 侧限定为普通用户只读本人上传及其正式结果；
2. 量产 `Current + PUBLISHED` 正式结果向所有具备 `DATASET_READ` 的用户开放查询和分析；
3. 原始文件、失败补录详情、Draft/历史事实、补录、导出、重处理和归档继续限定为 Owner 或 `SYSTEM_ADMIN`；
4. 同一文件内容可由不同人员重复上传，保留一个不可变 Source；每次成功入队生成独立 Receipt、Batch、Job，每次成功分析再生成独立 Run 和 Dataset；
5. Current 唯一边界从 Source 修正为 Dataset，同源的两个独立 Dataset 可以同时保持 Current；
6. 前端按后端逐动作 capability 显示按钮，登录、退出和认证失效都会先取消在途查询并清空缓存；
7. 开发库、随机空库、浏览器、全量回归、性能探针和双构建发布包均已取得证据。

本轮可以签发“本机功能发布候选”，不能据此宣称目标 Windows Server、正式 SQL Server、真实用户灰度或生产安全上线完成。

## 2. 第一性原理与奥卡姆剃刀的落地

### 2.1 正确的数据身份

| 层级 | 含义 | v1.2 边界 |
|---|---|---|
| Source | 内容相同的文件 | SHA-256 复用 |
| Receipt | 某用户某次收到该内容 | 每次上传独立，保存本次快照路径 |
| Batch | 一次业务提交 | 每次上传独立 |
| Job/Run | 一次处理和分析 | 每次分析独立 |
| Dataset | 本次上传形成的数据资产 | 每个 Batch 独立 |
| Dataset Version | 同一数据资产的重处理版本 | 仅 Dataset 内 Current 唯一 |

采用的最小方案没有增加微服务、Redis、搜索引擎或自动复用旧分析结果。系统继续复用成熟 Cleaner 与 Canonical 写入链，只修正 Current、可见性、动作能力和并发登记四个错误边界。

### 2.2 前端核心需求

- 工程页显示“工程数据仅上传人本人可见”；
- 量产页显示“量产正式结果面向全员共享查询”；
- 重复来源、上传账号、上传人、Batch 和时间可辨认；
- 非 Owner 的共享量产行保留查询和分析，隐藏不可执行的管理动作；
- 产品补录、导出、重处理、归档分别使用 `can_edit_product`、`can_export`、`can_reprocess`、`can_archive`，不互相代用；
- 重复上传成功后创建独立任务，不覆盖其他人的分析结果；
- 身份变化后旧工程数据和管理按钮不残留。

## 3. 做了什么

### 3.1 Schema 与 Current

- 新增 SQL Server 2014 兼容 Migration `sql2014_0019`；
- 删除 Source 级过滤唯一索引 `UX_processing_run_current`；
- 新增非唯一查询索引 `IX_processing_run_source_state`；
- 保留 Dataset Version 级 `UX_dataset_version_current`；
- Atomic Finalizer 和手工 Publish 只切换同一 Dataset 的上一 Version/Run；
- 当一个 Run 仍被其他 Current Dataset 引用时，不会被错误 Supersede；
- repair、初始一致性检查和 Windows 维护检查均改为 Dataset 双向对齐规则。

### 3.2 域感知可见性

- 集中实现 Batch、Current Dataset 和正式结果的 SQL 可见性谓词；
- Stage 上传/结果、Current Catalog、Dataset Gate/Summary/Chart/Detail、Job、质量摘要、管理查询、补录和输入请求统一使用 Principal；
- 工程非 Owner 的列表结果被过滤；直接访问 Dataset gate 返回 403，Stage/Input Request 等隐私型对象采用 404 失败关闭；
- 量产上传摘要面向普通查询用户共享，包括原始文件名、Source 标识、上传人、状态和安全化错误摘要；本次快照路径、原始文件下载、失败补录详情和管理动作仍只允许 Owner/Admin；
- 量产非 Owner 对正式分析结果只读取 `Current + PUBLISHED`，Job 只返回脱敏摘要；
- 失败处理中间 input request 的文件名、Source、prompt 和处理细节只允许 Owner/Admin 读取；
- 原始文件下载、补录、重处理、归档和写操作保持 Owner/Admin。

### 3.3 重复上传与追溯

- Source SHA 查询使用 `UPDLOCK,HOLDLOCK`，避免并发“先查后插”竞态；
- 每次成功入队创建新的 Batch、Receipt、Job；只有处理成功后才创建并发布该次分析独立的 Run、Dataset；
- Job 幂等键继续以 Batch 为边界；
- Receipt 元数据保存本次 `receipt_storage_uri`，下载和 Worker 优先使用本次快照；
- 数据库登记失败只删除本次新建快照目录，不触碰既有 Source 或其他用户目录；
- `is_duplicate_receipt` 仅作用户提示，不改变处理语义。

### 3.4 前端可用性

- Stage 和 Current Catalog 显示上传人、重复来源及服务端 capability；
- 管理动作按逐行动作 capability 决定，不再根据登录名或单一归档能力猜测；
- React Query 在登录成功、退出和认证失效时取消查询并清空缓存；
- 服务端分页、筛选和 Owner/Domain 范围保持下推 SQL。

## 4. 需求验收

| 需求 | 状态 | 主要证据 |
|---|---|---|
| 工程普通用户只见本人 | PASS | 服务层/API 单测、真实 SQL Owner/non-owner 矩阵 |
| 量产正式 Current 全员可查 | PASS | Catalog、Summary、Chart、Detail 和质量查询矩阵 |
| 量产非 Owner 不可管理 | PASS | 下载、input request、补录、重处理、归档均拒绝；逐动作按钮隐藏 |
| 同 SHA 顺序/并发登记 | PASS | 顺序 service E2E（工程/量产各一批）与两连接并发锁竞争 E2E：均为 1 Source、2 Batch、2 Receipt |
| 不同用户重复上传形成独立正式身份链 | PASS | Upload-to-Current E2E：2 Job、2 Run、2 Dataset 均 Current+PUBLISHED |
| 同 Dataset 重处理只切自己的 Current | PASS | Atomic Finalizer、重处理和 Dataset-scoped Current E2E |
| 本次 Receipt 路径独立 | PASS | 两个真实文件快照路径不同且可读，精确清理成功 |
| 身份切换无旧缓存 | PASS | AuthContext 测试；强制鉴权重载后只显示登录页，无旧 Dataset/动作 |
| 开发规模关键查询 | 部分 PASS | 6 个已有数据探针达标；2 个 8-Dataset 探针因样本不足 SKIP |

## 5. 一线开发/工程用户视角

### 做得较好的地方

- 工程/量产、CP/FT 四个固定入口清晰，不要求用户理解 Dataset/Run 内部编号；
- 重复上传不再被“去重”误伤，用户可以保留各自条件、时间和结论；
- 上传、排队、失败、补录、分析形成同一工作流，减少在文件夹、脚本和报表之间切换；
- 同一正式数据可直接进入服务端统计、图表和 Unit 明细；
- 失败关闭比“猜格式后给出看似合理结果”更适合测试数据。

### 后续体验优化

- 建立 8 个以上同阶段、同 Spec 的 Golden Dataset，补齐多批次比较真实验收；
- 将大体积 ECharts/主包继续拆分，降低首次打开时间；
- 为失败任务增加面向用户的原因分类、建议动作和处理时限，不暴露底层敏感细节；
- 在正式环境测量深页分页、并发上传、并发分析和大 Lot 响应，而不是外推开发库结果；
- 增加上传到正式结果的端到端进度和预计完成时间。

## 6. 领导视角与新洁能业务适配

新洁能官方 2025 年半年度报告显示，公司产品覆盖 MOSFET、IGBT、SiC/GaN、功率模块、驱动/电源 IC、IPM 和 MCU，细分型号近 4000 款，电压覆盖 12V～1700V；经营链同时包含外部晶圆代工、外部封测、子公司自主封测和功率模块产线。来源：[新洁能 2025 年半年度报告](https://www.ncepower.com/Upload/ueditor/files/2025-09-29/605111_20250820_%E6%96%B0%E6%B4%81%E8%83%BD2025%E5%8D%8A%E5%B9%B4%E6%8A%A5-713ee35ad82d4ab5ad62c99060441090.pdf)。

因此，TMS 下一阶段应优先服务以下管理问题：

1. 建立 SAP-B1 物料、内部产品、晶圆厂 Product、封测厂 Program/Package 的受控 Crosswalk；
2. 形成产品平台 × 晶圆厂 × 封测厂 × CP/FT × Spec Version 的质量基线；
3. 把量产良率、失效 Bin、返工、测试时长和委外费用逐步关联到成本与毛利分析；
4. 建立从异常 Lot 到晶圆厂、封测厂、程序版本、Spec、客户应用的追溯链；
5. 用固定 KPI 看板管理数据完整率、首轮通过率、重处理率、异常关闭周期和各供应商波动；
6. 在功能稳定后单独开展安全、合规、审计留存和灾备专项，不把本轮功能性权限等同于完整安全体系。

## 7. 确定的、不确定的、下一步

### 确定的

- 代码、Migration、开发库和随机空库均为唯一 head `sql2014_0019`；
- 重复上传的 HTTP 上传、SQL Worker 租约、原子发布身份链、Owner/共享矩阵、浏览器基础流程和发布包可复现性均已验证；
- 本轮没有修改原始业务数据；E2E 夹具和临时文件均精确清理；
- 3 个既有 `NEEDS_INPUT` Job（64、66、68）保持原状，未被本轮自动修补。

### 不确定或未关闭

- 当前开发库只有 3 个兼容量产 Current Dataset，不能验证 8-Dataset 比较性能；
- 浏览器没有传输测试账户密码，因此没有执行真实两账户网页登录切换；两用户矩阵由 API/service/真实 SQL E2E 覆盖；
- 页面基础点击反馈的 300 ms 门槛没有可排除浏览器控制工具开销的可信测量；
- 重复上传专项 E2E 使用最小 synthetic Canonical staging 来隔离身份和 Current 语义，没有再次执行真实 Cleaner 解析；真实 Cleaner/Canonical 由既有 CP/FT Route A 回归覆盖；
- SQL Server 实例为 `12.0.5000.0`，低于目标 SP3 基线；
- Windows Integrated Security 健康脚本不能连接当前远程 SQL 登录面，不能替代目标服务器预检；
- 安全专项、目标服务器备份恢复、真实用户灰度和生产峰值性能尚未执行。

### 下一步

1. 由业务负责人准备脱敏 Golden Dataset 和 8 组兼容 Compare 数据；
2. 在目标 SQL Server 2014 SP3+ TEST 上执行备份、空库、增量 Migration 和 restore drill；
3. 组织工程 A、工程 B、量产查询用户、管理员四类真实账户做 G3 UAT；
4. 再单独冻结安全专项需求和威胁模型；
5. 只有 G3/G4 门禁通过后才允许生产上线。

## 8. 测试工具开发中的透明记录

- 并发清理验证器首次运行暴露动态参数占位符错误；已依据唯一随机 SHA 精确核验并清理 2 Batch、2 Receipt、1 Source，修正后重跑无残留；
- 随机空库第一次后置断言沿用了旧设计 schema 名称，Migration 已成功但检查误报；随机库仍被精确删除，改用实际 `ingestion`/`dataset` 对象名后重跑 PASS；
- 两项都属于验收工具自身问题，没有修改业务数据，也没有被包装成产品成功证据。
