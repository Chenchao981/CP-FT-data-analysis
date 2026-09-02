# TMS 数据归属与本机快速分析需求 v1.0

> 2026-09-02 变更：开发期管理员改为全数据访问，四个工程/量产入口合并为 CP、FT 两个入口。最新口径见 `TMS_Development_First_Access_and_Entry_Requirements_2026-09-02.md`；本文保留为前一版决策记录。

- 日期：2026-09-01
- 状态：本轮开发基线
- 替代口径：替代“工程私有、量产默认全员共享”的数据可见性规则
- 不变边界：CP、FT 仍使用四个固定业务入口；快速分析不写入正式 `test.*` Canonical

## 1. 已确认的业务决定

### 1.1 功能开放，数据按归属控制

所有已启用的普通用户默认可以使用上传、快速分析、正式数据分析、Dashboard、趋势、异常和派生结果导出等业务功能。系统不再通过“CP 工程师、FT 工程师、生产组”等虚拟角色决定数据可见性。

“功能全开放”不包括控制面权限：用户管理、数据域授权、数据源与定时任务配置、Cleaner/规则发布、DQ 豁免、系统运维和紧急穿透访问仍需独立授权，并且这些管理身份本身不自动获得数据内容查看权。

### 1.2 数据只采用两种访问范围

| 访问范围 | 产生方式 | 所有者 | 谁能读取和分析 |
|---|---|---|---|
| `PERSONAL` | 用户手工上传、本机快速分析 | 当前用户 | 仅所有者本人 |
| `DOMAIN` | FTP/NAS/API/SAP 等受控系统源 | 不可登录的系统技术账号 | 当前有效的数据域授权用户 |

`ENGINEERING / PRODUCTION` 只作为业务分类和页面路线，不再是授权条件。把数据标成 `PRODUCTION` 不会使其自动对全员可见。

### 1.3 数据权由来源决定，不由任务或待办决定

受控数据源必须在配置时绑定唯一数据域，例如：

```text
华虹 FTP /CP/NCE/
  -> source_definition = HUAHONG_CP_FTP
  -> data_domain = HUAHONG_CP
  -> test_stage = CP
  -> cleaner/profile = 已发布华虹 CP 合同
```

这一约束同时适用于 `SERVER_CATALOG`（服务器目录快速分析）和
`FORMAL_IMPORT`（正式导入）：每个受控 Source Root/Source Definition 都必须显式配置
`data_domain_code`。系统在列举、浏览、预览和创建任务时重新校验当前用户对该数据域的
有效、未过期授权；未绑定、无权限或授权已过期时失败关闭，并且直接探测编码也不泄露
数据源是否存在。

定时任务只是执行者。它不能把数据授权给运行账号、任务接收人或待办处理人；待办也不能成为数据授权凭证。正常完成只更新对应数据域 Dashboard，只有异常、输入缺失、数据质量问题或需要人工决策时才创建待办。

### 1.4 Dashboard 分开统计

首页至少分为：

- 我的数据：只统计本人 `PERSONAL` 数据和本人快速分析结果；
- 数据域：先选择本人已获授权的数据域，再统计该域的正式数据；
- 异常中心：按同一数据访问规则汇总异常，不扩大数据范围。

不得把个人与数据域指标合并后用一个总数表达，也不得通过 Dashboard、导出、Saved Analysis、Job 详情或直接 URL 绕过数据边界。

## 2. 快速分析需求

### 2.1 两种执行位置

| 模式 | 源数据位置 | 计算位置 | TMS 接收内容 |
|---|---|---|---|
| `SERVER_CATALOG` | FTP/NAS/服务器受控目录 | 靠近数据的服务器 Worker | 结果、摘要、Manifest |
| `LOCAL_AGENT` | 用户电脑目录 | 用户电脑上的 TMS Local Agent | 结果、摘要、Manifest；不接收原始文件 |

浏览器和中央服务器不能直接读取用户电脑的任意绝对路径。当前开发机上服务器与 F 盘恰好同机，不得据此形成生产架构。用户电脑目录必须由本机 Agent 通过原生目录选择器取得，并在本机调用已登记、校验 SHA-256 的 CP/FT 发布工具。

### 2.2 FT 首个验收能力

- 路线：FT；
- 厂家：杰群；
- 分析：原始目录低内存 PAT；
- 输入合同：已批准杰群统一 CSV 原始目录；
- 计算内核：`ft_data_cleaner.pyz` 内 `factories.jiequn.pat_cleaner.generate_raw_pat`；
- 结果：PAT Excel、统计摘要、来源 Manifest、自声明一致性回执；
- 禁止：在 TMS 或 Agent 重写解析、单位换算、参数筛选或 PAT 公式。

真实验收目录为 520 个 CSV、约 2.83 GiB。验收必须证明原始上传字节为 0、结果与发布工具一致、临时空间受控、异常时失败关闭。

这里的“自声明一致性回执”只用于把工具 Release SHA、来源 Manifest 摘要、运行摘要和
结果 SHA/大小进行合同对账。当前回执没有设备私钥签名、服务端任务 nonce 或防重放
机制，不能称为“签名执行证明”，也不能单独证明计算确实发生在受信设备上。

### 2.3 CP 能力门

CP 与 FT 采用相同 Local Agent 架构，但不做自动类型识别，也不把普通统计/Cp/Cpk 冒充 PAT。当前 CP 发布包可复用标准 cleaned/yield/spec 数据的良率、Wafer 统计和 Cpk；尚无已批准的“原始目录低内存 PAT”入口和结果合同。

因此本轮应完成 CP 能力注册和禁用原因展示。CP 原始目录能力只有在 CP 工具仓形成稳定只读 Adapter、真实样本 Golden、输出合同和 Release SHA 绑定后才能启用。

## 3. 安全与审计合同

- 普通用户不能提交 `owner_user_id`、`access_scope` 或 `data_domain_id` 来改变手工上传归属；服务端强制写为本人 `PERSONAL`。
- 系统源只接受已启用的 `source_definition_id`；服务端从 Source Definition 读取数据域和技术账号。
- 数据域授权只在专用管理接口发生；任务分配、部门、角色名、厂家接触经验都不会隐式授权。
- Local Agent 只监听本机回环地址，校验精确 Host/Origin 和配对令牌；令牌从 Agent 启动起固定有效 8 小时，过期后必须重启 Agent 并重新配对。网页不能提交任意绝对路径，目录只能由本机原生选择器选择。
- Agent 不接收 TMS 登录 JWT；源文件只读；输出写入隔离运行目录；工具包执行前校验登记 SHA。
- 来源 Manifest 当前采用 `LOCAL_PATH_SIZE_MTIME_V1`，只覆盖相对路径、大小和纳秒级 mtime；运行前后都重新生成并比对，但它不是文件内容哈希，仍保留同大小、同 mtime 内容替换的 TOCTOU 风险。
- Agent 失败时清理该次工作目录；TMS 登记成功后页面调用运行删除接口确认清理；Agent 重启时只清理由安全 UUID 命名且位于 `work_root` 直属层级的陈旧运行目录。内存中的运行状态不跨 Agent 重启恢复，用户需重新选择和运行。
- TMS 只接收白名单结果角色，HTTP 正文先执行总大小限制，再流式校验结果大小/SHA；PAT XLSX 必须符合唯一工作表、固定 17 列和精确容器部件白名单，禁止公式、超链接、定义名称、外部链接、连接、VBA、OLE 和嵌入对象，结果/摘要计数一致后才原子登记。
- 用户管理、系统管理或数据域管理权限不等于数据读取；未来如启用紧急读取，必须使用独立 `DATA_BREAK_GLASS` 并先形成审批与审计闭环。
- Quick `PERSONAL` 结果即使系统管理员也不能通过普通结果接口下载。

`DATA_BREAK_GLASS` 当前只保留权限码和授权谓词扩展点，不授予任何可用角色，也没有
批准理由、短时授权、双人复核和持久审计工作流。上述治理闭环完成前，生产环境不得
启用该权限。

## 4. 验收场景

1. A、B 分别上传相同或不同文件，只能看到和分析自己的数据。
2. 将 `business_domain` 从 ENGINEERING 改成 PRODUCTION 不改变任何访问结果。
3. 华虹 FTP 数据绑定 `HUAHONG_CP` 后，域成员可读取 Published Current，非成员和授权过期用户不可读取。
4. USER_ADMIN、DATA_DOMAIN_ADMIN、SYSTEM_ADMIN 在没有数据域授权且没有 break-glass 时不能查看数据内容。
5. 撤销数据域授权后，下一次 Dataset、图表、Job、Saved Analysis、导出及 Artifact 请求
   立即失败关闭；已进入受信 Worker 计算的任务也不得登记 `SUCCESS`、Artifact 或交付结果。
6. 本机 Agent 对 520 文件运行 FT PAT，中央服务器收到的原始文件字节数为 0，仅收到经验证的结果、自声明一致性回执与 Manifest。
7. Agent 包 SHA 不匹配、Manifest 改变、混产品、格式漂移、输出超限或结果合同不一致时不得登记 SUCCESS。
8. 快速分析前后正式 `test.*` 行数不增加。

## 5. 本轮不宣称完成的事项

- FTP/SFTP 凭据接入和生产定时调度；
- CP 原始目录低内存 PAT Adapter；
- Local Agent 签名安装包、URI Handler/DPAPI 设备凭证、设备签名、服务端 nonce、防重放和跨两台机器生产验收；
- Local Manifest 文件内容哈希及其对同大小、同 mtime 替换的 TOCTOU 防护；
- `DATA_BREAK_GLASS` 的可授角色、审批、短时授权和审计工作流；
- SQL Server 生产库历史数据域映射与业务签字；
- G3 测试服务器、G4 生产上线。
