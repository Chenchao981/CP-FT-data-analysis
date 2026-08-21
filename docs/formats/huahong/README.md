# 华虹 DCP/TXT 格式证据 v1.0

> 状态：样本证据已验证，尚未发布为生产 Cleaner Release  
> 样本位置：仓库外受控目录；原始文件、文件名清单和逐片结果不进入Git

## 已验证范围

- 362个TXT文件，22个业务Lot，362片Wafer；
- 163,095个Die记录；
- 10套固定、有序的参数Schema；
- 基础列固定为 `No.U / X / Y / Bin`；
- 文件头固定包含 Program、源Lot/Run、Wafer、日期、时间、LimitU/LimitL和Bias 1～6；
- Pass Bin依据现有华虹业务合同固定为1，其他Bin原样保留；
- 92个ZIP和1个7z已接入安全容器边界；拒绝加密、损坏、路径穿越、符号链接、重复路径、超限成员和无TXT数据的压缩包，临时目录在成功或异常后均清理。

全部362个TXT已通过当前严格Parser，源数据汇总为158,223个Pass Die。该汇总是开发对账证据，不等同于业务批准的Golden Manifest。

归档端到端复验中，93个容器均进入读取流程；80个完成严格解析，13个需要业务审批。其中6个首先命中尚未批准的Source Lot形态，7个首先命中尚未批准的参数Schema；13个文件并不代表13种新格式，去重后为5类Lot取值、1个文件名与文件内容差异、3种参数Schema，另有2个重复压缩包。逐项证据见 `Archive_Approval_2026-08-21.md`。

## 身份合同

当前已确认两类源Lot/Run字符串：

1. `四位批号-四位批号-工艺/日期/站点`：业务Lot取前两个分段，完整字符串保留为源Run；
2. `字母+数字+.版本-测试机台/日期/时间`：业务Lot取第一个分段，完整字符串保留为源Run。

Wafer使用文件头 `Wafer number`，并与文件名末尾的三位Wafer编号核对。Canonical Wafer身份使用“业务Lot + Wafer”；完整源Run、文件SHA256和源行号单独保留，不覆盖原始身份。

目录名以 `NCE产品号_...` 开头时只生成可选产品候选，不直接形成主数据映射。`C141321.02`、`FA53-5465` 已确认是业务Lot；CP分析以晶圆厂、Lot、参数、测试条件和数值为主，缺少Product不再产生DQ问题，也不阻断Dataset。

## 规格和单位

- LimitU/LimitL保留原始字符串，同时转换为基础单位数值；
- `u/n/m/k/M`等工程前缀按大小写转换；
- 源文件使用尾部 `-` 表示无量纲，例如 `50.00-`，保存为原值、数值50和单位 `1`；
- Bias条件只保留源字符串，当前阶段不推断其业务含义；
- Limit上下限单位不一致时阻断；
- 不从测量值分布猜规格，不做IQR删除，不为缺失参数补零。

## 失败关闭规则

- 未登记的参数Schema；
- 基础列、元数据、Limit或Bias结构不完整；
- 文件名与源Run/Wafer不一致；
- 重复No.U或重复X/Y；
- 非法Bin、数值或工程单位；
- 同一业务Lot混合Schema、测试程序或规格；
- 产品身份缺失/歧义；
- 重复“业务Lot + Wafer”。

## 当前实现

- Parser与10套Schema：`backend/app/cleaners/huahong_dcp.py`
- ZIP/7z安全输入：`backend/app/cleaners/huahong_archive.py`
- 批量DQ：`backend/app/cleaners/huahong_batch.py`
- Canonical来源登记与事务Writer：`backend/app/infrastructure/canonical_writer.py`
- DQ Gate与Dataset发布：`backend/app/infrastructure/sql_dataset_service.py`
- 单片检查API：`POST /api/v1/cleaners/huahong/inspect`
- 只读样本验证：`scripts/g0/profile_huahong_samples.py`
- React页面：侧栏“华虹样本检查”

## 下一门禁

1. 抽取代表性Lot形成逐Wafer行数、Bin、Yield、参数、上下限对账基线；
2. 审批新增归档证据中的Source Lot形态和参数Schema；未审批前保持失败关闭；
3. 将正式华虹接入收敛为对 `F:\cp_data_ansys` 既有清洗逻辑的Adapter复用和结果对账；
4. 当前G0技术链已完成Canonical Writer、DQ Gate和Dataset发布真实数据库验证；正式CP发布要求晶圆厂、Lot、Program、Test Item和对账基线，不要求Product。
