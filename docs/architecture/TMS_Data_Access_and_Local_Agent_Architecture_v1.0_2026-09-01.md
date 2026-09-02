# TMS 数据访问与 Local Agent 架构 v1.0

> 2026-09-02 变更：开发期 `SYSTEM_ADMIN`、`DATA_DOMAIN_ADMIN` 改为全数据访问；工程/量产菜单已合并。本文的严格管理员隔离内容转入安全延期项，最新口径见 `docs/business/TMS_Development_First_Access_and_Entry_Requirements_2026-09-02.md`。

- 日期：2026-09-01
- 状态：开发候选架构
- 继承：Route A 唯一 Canonical 与 v0.8 双通道边界

## 1. 授权决策

所有数据内容读取、分析和派生导出统一使用：

```text
(access_scope = PERSONAL AND owner_user_id = current_user)
OR
(access_scope = DOMAIN AND current_user has active, unexpired data_domain_grant)
OR
(current_user has explicit DATA_BREAK_GLASS)
```

`SYSTEM_ADMIN`、`USER_ADMIN`、`DATA_DOMAIN_ADMIN` 和 `business_domain=PRODUCTION` 均不构成数据读取条件。写入、重处理、归档、正式发布和数据域治理另行判定，不能从 READ 权限反推。

当前正常授权路径只有前两支。`DATA_BREAK_GLASS` 只是保留的权限码与谓词扩展点，迁移
不会把它授予任何角色；在批准理由、短时授权、双人复核和持久审计工作流落地前，生产
环境不得启用第三支。

## 2. 数据模型

```text
iam.data_domain
  1 ── n iam.data_domain_grant ── 1 iam.app_user
  1 ── n ingestion.source_definition
  1 ── n ingestion.import_batch
  1 ── n dataset.dataset

PERSONAL: owner_user_id = current user, data_domain_id = NULL
DOMAIN:   owner_user_id = SYSTEM_INGESTION technical account,
          data_domain_id = configured domain
```

技术 Owner 只为兼容现有外键和追溯链，不参与 ACL。Source Definition 保存凭据引用，不保存明文 FTP/SFTP 密码。

服务器受控目录有两个用途：`SERVER_CATALOG/QUICK_ANALYSIS` 与 `FORMAL_IMPORT`。
两者都必须显式绑定 `data_domain_code`。API 在列举 Root、浏览目录、Manifest 预览、创建
Quick 任务或正式导入时重新检查当前用户的有效、未过期 Data Domain Grant；未绑定或
无权访问的 Root 失败关闭，并以不泄露存在性的 404 响应直接探测。

## 3. 双位置快速分析

```text
Web UI
  ├─ SERVER_CATALOG -> TMS API -> SQL Queue -> near-data Worker -> released tool
  └─ LOCAL_AGENT    -> loopback/deep link -> user-PC Agent -> released tool
                                                   |
                                                   └─ result-only upload -> TMS verification

TMS verification -> workspace.analysis_session + artifacts (TTL)
TMS verification -X-> test.* Canonical
```

本机路径只存在于用户电脑和 Agent 内存/本机日志脱敏上下文。中央 TMS 只保存 `source_label`、标准化 Manifest、统计摘要、工具 Release 身份和结果 Artifact。

## 4. Local Agent 信任边界

- 目录只能通过 Agent 的原生选择器取得；网页不能传任意路径。
- 开发候选仅监听 `127.0.0.1`，严格 Host/Origin、非 cookie 配对令牌、只允许必要方法、禁止通用文件读取 API。配对令牌从 Agent 启动起有效 8 小时，过期后必须重启并重新配对。
- 生产候选使用签名 URI Handler 唤起 Agent，Agent 通过短期设备/任务凭证主动 HTTPS 出站；TMS JWT 永不交给 Agent。
- Agent 能力表固定 `(stage, factory, analysis, input contract, output contract, release SHA)`；执行前重新校验包 SHA 和来源 Manifest。
- 每次运行使用独立目录；源只读；失败不产生 SUCCESS；取消和失租后不得 finalize。

Agent 返回的是严格、无绝对路径的“自声明一致性回执”，不是签名执行证明。回执把
Release SHA、Manifest 摘要、运行摘要和结果 SHA/大小连接起来，但当前没有设备私钥
签名、服务端 nonce、一次性票据或防重放机制。Manifest 也仅使用相对路径、大小和纳秒
级 mtime；运行前后对账能发现常规漂移，不能排除同大小、同 mtime 内容替换的 TOCTOU。

运行清理采用明确确认语义：失败运行立即清理工作目录；TMS 完成结果登记后，页面调用
`DELETE /v1/runs/{run_id}` 作为确认并清理本机结果；Agent 启动时只删除 `work_root`
直属层级中合法 UUID 命名的陈旧目录。Agent 不把运行状态持久化，重启后不恢复中断
任务，用户必须重新选择和运行。TMS 侧 Quick Artifact 仍按既有 TTL/Cleanup 状态机处理，
支持陈旧 `CLEANING` 的重领与收口。

## 5. 结果验证与持久化

接收流程为：

```text
receipt intent（自声明一致性回执）
 -> server-owned .staging
 -> HTTP 总大小限制 + stream size/SHA validation
 -> 严格 XLSX 容器和 workbook schema validation
 -> summary/manifest/release reconciliation
 -> atomic rename
 -> artifact + session SUCCESS registration
```

允许的 FT PAT Artifact 角色只有 `pat_report`、`pat_summary`、`source_manifest`。结果上传
只接受 `.xlsx`，默认 HTTP 正文上限为 70 MiB，并同时受 Cleaner Release 输出上限约束。
XLSX 必须只有名为 `PAT` 的工作表、固定 17 列表头和精确容器部件白名单；公式、超链接、
定义名称、外部链接、连接、VBA、OLE、嵌入对象、非有限数值、重复参数和摘要计数不一致
全部拒绝。任何源数据、任意扩展名、目录名路径段、未知角色或超过上限的内容也都拒绝。

## 6. 兼容与扩展

- 现有 `/quick-analysis/pat` 与服务器受控目录继续作为 `SERVER_CATALOG`；
- 新本机结果使用独立 API，不根据绝对路径猜测执行模式；
- FT 从杰群开始，后续每个厂家按独立已批准 Adapter 注册；
- CP 先接标准 cleaned/yield/spec 的已验证分析能力，原始目录 PAT 必须先补 CP 工具 Adapter 和 Golden；
- 快速结果仍是 PERSONAL；若未来要共享，必须显式形成正式数据或设计独立的结果共享合同，不能自动进入数据域。

## 7. 尚未关闭的生产门

- FTP/SFTP 凭据接入、扫描去重、定时调度和异常待办尚未实现；图中的系统源链路是目标架构，不是本轮完成声明。
- CP 原始目录低内存 PAT Adapter、输出合同和真实 Golden 尚未实现；CP 能力必须保持禁用。
- Local Agent 设备签名、服务端 nonce、防重放、签名安装包和可信执行隔离尚未实现。
- `LOCAL_PATH_SIZE_MTIME_V1` 没有文件内容哈希，TOCTOU 风险仍开放。
- `DATA_BREAK_GLASS` 没有可授角色和审批审计工作流，生产不得启用。
