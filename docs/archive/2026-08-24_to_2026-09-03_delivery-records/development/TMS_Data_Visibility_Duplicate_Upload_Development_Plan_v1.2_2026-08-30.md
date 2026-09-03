# TMS v1.2 工程私有、量产共享与重复上传开发计划

- 日期：2026-08-30
- 输入基线：`TMS_Business_Requirements_v0.3.md`
- 顺序约束：需求与方案先冻结，再实施代码；功能与性能优先，安全专项留到最后阶段
- 发布边界：本轮只签发本机/开发库 G0-G2，不宣称 G3/G4 或生产上线

## 1. 目标

在不重写 Cleaner、Canonical 和四入口的前提下完成：

- 工程普通用户只见本人数据；
- 量产正式结果对所有 `DATASET_READ` 用户可查、可分析；
- 非 Owner 对共享量产数据只读，不出现无效管理按钮；
- 同一内容的顺序或并发重复上传均形成独立分析，互不切掉 Current；
- 登录身份切换不显示上一用户缓存；
- 关键查询达到现有开发规模的响应门槛。

## 2. 设计决定

### D1：统一读取和管理规则

```text
Batch/Current 正式数据 READ = Admin OR Owner OR business_domain=PRODUCTION
历史/Draft/原始文件/WRITE = Admin OR Owner
Quick Workspace = Admin OR Owner
```

所有分页总数、列表、直接 URL、图表、Job 和质量摘要都在 SQL 侧执行，不做前端后过滤。

### D2：Current 只属于 Dataset

- Migration 删除 `UX_processing_run_current(source_file_id)`；
- 增加非唯一 Source/Status/Current 查询索引；
- Atomic Finalizer 通过同一 Dataset 的上一 Version 找上一 Run；
- 不再按 Source 全局 Supersede 其他 Dataset；
- 手工 Publish、运行一致性检查和修复脚本同步采用 Dataset 边界。

### D3：重复上传是独立业务动作

- Source SHA 继续复用；
- Receipt/Batch/Job/Dataset 每次新建；
- Source get-or-create 使用 `UPDLOCK,HOLDLOCK` 或可恢复唯一冲突；
- 下载和 Worker 都优先使用本 Receipt 的 `receipt_storage_uri`；
- `is_duplicate_receipt` 只用于提示。

### D4：服务端决定动作能力

列表与 Job Details 返回最小能力字段：

- `can_manage`；
- `can_download_source`；
- `is_duplicate_receipt`。

前端据此显示补录、重处理和下载，不通过登录名猜测。

### D5：前端身份切换清缓存

- 登录成功、退出、认证过期时取消在途查询并清空 QueryClient；
- 身份切换先清旧缓存再装载新用户；
- 认证错误不做无意义自动重试。

## 3. 实施批次

### M1：Schema 与 Current 正确性

1. 新建 SQL Server 2014 兼容 Migration `sql2014_0019`；
2. 删除 Source 级 Current 唯一索引并增加普通查询索引；
3. 修改 Atomic Finalizer 和手工 Publish；
4. 更新 Processing Run Current 修复/一致性脚本；
5. 增加跨 Batch 同 SHA 和同 Dataset 重处理合同测试。

关闭条件：两个不同 Dataset 可以同时引用同一 Source 且保持 Current；同 Dataset 仍只有一个 Current Version。

### M2：域感知可见性

修改：

- Dataset list/assert/gate/summary；
- Stage 上传/结果分页和旧接口；
- Current Catalog、Job Details、基础 Job 读取；
- Quality Summary 和 Failed Job；
- 输入请求/补录只读与写入边界；
- 生命周期写操作保持 Owner/Admin。

关闭条件：A/B/Manager/Admin 权限矩阵全部通过，直接 URL 与列表一致。

### M3：重复上传与前端可用性

1. 并发安全 Source get-or-create；
2. Receipt 路径优先下载；
3. Stage DTO 增加上传人、重复来源、`can_manage`；
4. 量产共享提示和工程私有提示；
5. 共享行只显示可执行动作；
6. 身份切换清理前端缓存。

关闭条件：顺序/并发重复上传、浏览器 A→B 切换、共享只读动作矩阵通过。

### M4：性能、回归与灰度

- 后端与前端全量自动化；
- SQL Migration 静态链、增量开发库和随机空库；
- 真实开发库只读可见性与查询计时；
- 受控回滚事务内验证重复 Source 独立 Current；
- 浏览器覆盖工程/量产四入口、A/B 身份切换和动作可见性；
- 双构建发布包、Manifest/CRC/秘密与禁止路径扫描。

## 4. 测试矩阵

| 场景 | A | B | Manager | Admin |
|---|---|---|---|---|
| 工程 A Current | 读/分析 | 不可见 | 不可见 | 维护可见 |
| 工程 B Current | 不可见 | 读/分析 | 不可见 | 维护可见 |
| 量产 A/B Current | 读/分析 | 读/分析 | 读/分析 | 维护可见 |
| 量产他人原始文件 | 不可下载 | 不可下载 | 不可下载 | 可下载 |
| 量产他人补录/重跑/归档 | 不可操作 | 不可操作 | 不可操作 | 可操作 |
| 自己的上传 | 可管理 | 可管理 | 按 Owner | 可管理 |

重复合同：

- 同 SHA 顺序上传；
- 同 SHA 并发上传；
- 同 SHA 跨工程/量产；
- 同首文件、不同后续文件；
- 同 Dataset 重处理成功/失败；
- 后上传 Receipt 路径存在、首个 Source 路径缺失时仍能正确下载自己的快照。

## 5. 性能验收

| 路径 | 开发规模门槛 |
|---|---|
| Stage/Catalog 热查询 | ≤ 3 s |
| Chart/Detail 热查询 | ≤ 3 s |
| Quality 热查询 | ≤ 3 s |
| Quality 冷查询候选 | ≤ 5 s |
| 8 Dataset 比较无参数 | ≤ 3 s |
| 8 Dataset 比较 5 参数 | ≤ 5 s |
| 页面基础点击反馈 | ≤ 300 ms |

记录数据规模、p50/p95 或至少多次 elapsed、SQL 语句数、响应大小和是否发生全表扫描。只有真实执行计划证明必要时，才增加量产专用时间分页索引或进一步合并 Compare/Detail 查询。

## 6. 风险与失败关闭

- Migration 不自动恢复或猜测历史 Superseded 数据；恢复必须另做 DryRun 清单；
- 工程域无法判定时按私有处理，不放宽；
- Dataset Version 无法证明 Current + PUBLISHED 时，非 Owner 不可读；
- 并发 Source 注册失败必须回滚 Batch，并清理本次未登记快照；
- 前端能力字段缺失时默认隐藏管理动作；
- 不因本轮功能实现宣称生产安全或生产上线完成。

## 7. 交付物

- v0.3 业务需求；
- 第一性原理评估；
- 本开发计划；
- Migration、后端、前端和测试代码；
- 性能与回归测试报告；
- v1.2 完成报告与灰度报告；
- 可复现发布候选包及哈希；
- 安全范围、生产 G3/G4 和历史数据恢复的明确后续清单。
