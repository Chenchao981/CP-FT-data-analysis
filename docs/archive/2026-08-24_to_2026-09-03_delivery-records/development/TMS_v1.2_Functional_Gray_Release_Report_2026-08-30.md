# TMS v1.2 功能灰度候选报告

- 日期：2026-08-30
- 候选版本：`v1.2-functional-rc1`
- 数据库 head：`sql2014_0019`
- 范围：工程私有、量产共享、重复上传独立分析、前端能力与缓存边界
- 安全范围：延期到后续独立项目

## 1. 本机演练结论

本机开发库 G0/G1 通过，本机受控 G2 灰度演练在已声明限制内通过，可交付功能发布候选包进行目标 TEST 环境评审。

这不是生产上线声明。目标 Windows Server、SQL Server 2014 SP3+、真实账户 UAT、备份恢复和安全专项未完成，G3/G4 继续 NO-GO。

## 2. 灰度策略

本轮采用最小可回退范围：

1. 只升级独立 TEST/DEV 数据库到 `sql2014_0019`；
2. 先以只读查询用户验证工程私有和量产共享；
3. 再开放两个测试上传用户验证同 SHA 重复上传；
4. 只允许 Owner/Admin 做补录、导出、重处理和归档；
5. 观察 Job、Batch、Dataset Current 和错误率；
6. 任一 Current、可见性或清理不变量失败即停止扩大范围。

## 3. 门禁状态

| 阶段 | 状态 | 证据/限制 |
|---|---|---|
| G0 静态与单元 | PASS | 520 passed、1 环境条件 skip；前端 125 passed；lint/build PASS |
| G1 开发库集成 | PASS | 0019 增量、随机空库、真实 SQL E2E、浏览器和数据不变检查 |
| G2 本机灰度演练 | PASS WITH LIMITS | 鉴权关闭/开启两模式、真实数据分析、可复现 RC；未传输两账户密码 |
| G3 目标 TEST/UAT | NO-GO | SP3+、真实账户、8-Dataset、备份/恢复待执行 |
| G4 生产 | NO-GO | 安全、容量、变更审批、灾备和生产观察期未执行 |

## 4. 本机灰度演练场景

- 工程 Owner 查询本人 CP/FT；
- 工程 non-owner 的列表和直接 URL 均失败关闭；
- 量产 non-owner 查询他人 Current+PUBLISHED 并进入分析；
- 量产 non-owner 不显示且不能直接调用原始文件、input request、补录、导出、重处理和归档；
- 两个用户经真实 HTTP 并发上传同一文件内容，形成 1 Source 和两套独立 Upload-to-Current 身份链；
- 两个 Dataset 同时保持 Current+PUBLISHED；
- 同一 Dataset 的后续版本只替换自己的上一版本；
- 身份失效后浏览器清除旧数据；
- 发布包从两次独立构建获得相同哈希。

## 5. 发布候选

- Build A：`artifacts/release/NCE-TMS-v1.2-functional-rc1-a.zip`
- Build B：`artifacts/release/NCE-TMS-v1.2-functional-rc1-b.zip`
- SHA-256：`67732689436002e79bfa3fbd6b7a8f8427ad115fbeb78abb2ca3c7c04d990769`
- 大小：503,305 bytes
- Manifest：216 files / `sql2014_0019`
- 检查：CRC、路径、敏感内容、可复现性、解包 launcher 均 PASS

交付时任选一份 ZIP；Build B 仅用于证明可复现，不需要同时部署。

## 6. 回退与停止条件

Migration 0019 改变 Current 约束且 downgrade 明确不自动猜测历史状态，因此回退必须使用升级前备份恢复，不能直接执行破坏性 downgrade。

立即停止灰度的条件：

- 工程 non-owner 能读取他人工程数据；
- 量产 non-owner 能下载原始文件或执行管理动作；
- 第二次同 SHA 上传导致第一 Dataset 退出 Current；
- 一个 Dataset 出现多个 Current Version；
- Source/Receipt 路径串用；
- Worker 队列持续堆积或出现无法恢复的 STAGED intent；
- 数据库一致性检查或前后业务计数不一致。

## 7. 观察指标

- 上传成功率、重复 Receipt 比例、排队时长、处理时长；
- Job `QUEUED/RUNNING/NEEDS_INPUT/FAILED` 数量与停留时间；
- Current Dataset/Run 双向一致性问题数；
- 工程/量产查询 P50/P95；
- Chart/Detail/Quality 错误率与 P95；
- 未授权 403/404 数量及访问对象类型；
- 临时 Artifact、上传快照和 Workspace 容量。

## 8. 已知限制

- 当前库只有 3 个兼容量产 Current Dataset，8-Dataset Compare 尚未实测；
- 重复上传专项使用 synthetic Canonical staging 验证身份和 Current；真实 Cleaner/Canonical 解析由既有 Route A 回归覆盖，未在同一脚本内重跑；
- 浏览器只验证了本机开发管理员和认证失效清缓存，真实 A/B 登录待目标 UAT；
- SQL Server 版本低于 SP3；
- 既有 Job 64、66、68 仍为 `NEEDS_INPUT`，灰度前需业务确认保留或处置；
- 本轮没有完成安全、备份恢复、生产容量与灾备验收。

## 9. G3 执行清单

1. DBA 在 SP3+ TEST 实例完成升级前备份和随机空库 Migration；
2. 使用脱敏数据复跑完整 Route A、回归和 8-Dataset Compare；
3. 工程 A、工程 B、量产只读、管理员四类真实账户签字；
4. 验证 SAP-B1 产品 Crosswalk 和各晶圆厂/封测厂身份映射；
5. 完成 restore drill 和回退计时；
6. 单独评审安全需求后，再决定是否进入 G4。
