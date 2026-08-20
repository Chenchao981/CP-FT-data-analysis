# CP/FT 数据管理与分析平台

本仓库用于规划和建设统一的 CP/FT 数据接入、清洗、质量治理、数据集发布和图表分析平台。

## 当前开发基线

当前唯一有效的开发入口是：

- [`docs/TMS_Development_Baseline_v0.6_Unified_Cleaning_Analytics/`](docs/TMS_Development_Baseline_v0.6_Unified_Cleaning_Analytics/README.md)
- [`docs/TMS_Implementation_Roadmap_v0.6.md`](docs/TMS_Implementation_Roadmap_v0.6.md)

本机的 `docs/0.1`、v0.2、v0.3 和 v0.5 目录只保留决策演进记录，不进入 GitHub；如旧文档与 v0.6 冲突，以 v0.6 为准。

## 产品主线

```text
原始文件 / 压缩包
→ Format Profile + Cleaner Release
→ Processing Run + Data Quality Gate
→ Published Dataset Version
→ Evaluation Run
→ 清洗结果 / 良率与 Bin / 参数与空间图表
→ Export Job / Report
```

厂家和格式差异只存在于接入与清洗层。最终用户统一面对任务、数据质量、已发布数据集、分析图表和交付物。

## 参考项目边界

本地 `历史项目-参考用/` 包含 CP、FT 和 VDMOS 历史项目、样例和输出，只用于事实核对，不进入 Git。任何清洗、规格、Bin 或统计规则必须经过样例验证和业务批准后，才能进入本平台的版本化规则。

## 数据安全

仓库禁止提交原始测试数据、客户或产品样例、上传目录、输出报表、数据库、密钥、日志、构建包和本地环境配置。提交前必须检查暂存文件清单和大文件。
