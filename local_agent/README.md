# TMS Local Agent（开发候选）

这是“计算到数据（compute-to-data）”的本机桥接：浏览器只发起原生目录选择，原始
CP/FT 文件不上传服务器；既有桌面工具在用户电脑读取源目录，结果和自声明一致性回执
保存在用户级工作目录。当前仅启用已发布且 SHA-256 匹配的杰群 FT `QUICK_PAT`。CP 原始目录
Quick PAT 因尚无批准入口和输出合同，能力列表会明确返回禁用原因。

## 启动

1. 将 `config.example.json` 复制到
   `%LOCALAPPDATA%\NCE\TMSLocalAgent\config.json`，核对 Python、PYZ 路径和发布
   SHA-256。配置文件和配对令牌不要提交到 Git。
2. 在仓库根目录运行：

   ```powershell
   D:\ProgramData\anaconda3\python.exe -m local_agent --config "$env:LOCALAPPDATA\NCE\TMSLocalAgent\config.json" --validate-only
   D:\ProgramData\anaconda3\python.exe -m local_agent --config "$env:LOCALAPPDATA\NCE\TMSLocalAgent\config.json"
   ```

   也可以运行 `scripts\windows\start_tms_local_agent.ps1`。Agent 每次启动生成新的随机
   配对令牌，只绑定 `127.0.0.1`；把该令牌填入本次可信的 TMS 快速分析页面。令牌从
   Agent 启动起固定有效 8 小时，过期后接口返回 `LOCAL_TOKEN_EXPIRED`，需要重启 Agent
   并重新配对。令牌不作为命令行参数传递。

## 浏览器合同

- `GET /v1/health`：唯一不要求令牌的接口。
- `GET /v1/tools`
- `POST /v1/select-folder`：正文必须是 `{}`；打开 Windows 原生目录选择框，拒绝网页传路径。
- `POST /v1/selections/{id}/preview`：正文 `{"tool_code":"..."}`。
- `POST /v1/selections/{id}/runs`：正文包含 `tool_code` 和
  `confirmed_manifest_sha256`。
- `GET /v1/runs/{id}`、`/receipt`、`/result`：轮询、取回执、下载结果。
- `DELETE /v1/runs/{id}`：TMS 已登记结果后确认并清理该次本机运行；运行中任务不可删除。

除 health 外都必须同时携带允许的 `Origin` 和 `X-TMS-Agent-Token`。默认仅允许
`http://127.0.0.1:5173` 与 `http://localhost:5173`。响应不返回本机绝对路径。

## 安全和数据边界

- 源目录全程只读；Agent 输出只写到配置的 `work_root/<run_id>/attempt-1`。
- 目录清单使用相对路径、大小和纳秒级 mtime 生成
  `LOCAL_PATH_SIZE_MTIME_V1` SHA-256；运行前后变化都使登记失败。
- 上述 Manifest 不包含文件内容哈希，因此仍有同大小、同 mtime 内容替换的 TOCTOU
  风险；这是开发候选的明确限制。
- 不跟随目录符号链接或 Windows junction，不允许源目录与工作目录重叠。
- FT 运行前再次校验 PYZ SHA-256，并从该包调用
  `factories.jiequn.pat_cleaner.generate_raw_pat`，不复制或改写 PAT 算法。
- 失败运行会清理该次工作目录；成功登记后由页面调用 DELETE 确认清理；Agent 启动时只
  清理 `work_root` 直属层级中合法 UUID 命名的陈旧运行目录。运行状态只保存在内存，
  Agent 重启不会恢复中断任务，用户需要重新选择和运行。
- 回执只把 Release SHA、Manifest 摘要、运行摘要和结果 SHA/大小做一致性关联，是
  “自声明一致性回执”，不是设备签名或可信执行证明；当前没有服务端 nonce 和防重放。
- TMS 接收端只接受 `.xlsx` PAT 结果，先限制 HTTP 正文和 Release 输出大小，再校验
  SHA、唯一 `PAT` 工作表、固定 17 列和精确 XLSX 部件白名单；公式、超链接、定义名称、
  外部链接、连接、VBA、OLE 和嵌入对象全部拒绝。
- 这是开发候选，不等同于生产安全审批、签名安装包、受信设备执行或集中运维方案。

## 与服务器受控数据源的边界

Local Agent 运行结果始终是当前用户的 `PERSONAL` 数据。服务器侧的
`SERVER_CATALOG/QUICK_ANALYSIS` 和 `FORMAL_IMPORT` Source Root 则必须配置
`data_domain_code`，并按当前用户有效、未过期的 Data Domain Grant 过滤。FTP/SFTP
凭据接入和生产定时调度不在本 Local Agent 中，当前也未实现；CP 原始目录 PAT 仍保持
禁用门。

## 测试

```powershell
D:\ProgramData\anaconda3\python.exe -m pytest local_agent\tests -q
```
