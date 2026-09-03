# TMS 可选进阶版 Grid：Wijmo / ComponentOne

> 版本：v0.6  
> 状态：可选，不属于当前基础版依赖。

## 1. 定位

当前正式前端基础版使用：

```text
Ant Design Table
```

Wijmo/ComponentOne 仅作为未来的专业工程 Grid 升级方案。

## 2. 触发升级的条件

满足以下一个或多个条件，并经过实际 PoC 后再引入：

- Excel 式复制/粘贴成为高频核心操作；
- CP/FT 宽表需要大量冻结列、复杂分组和高级编辑；
- Wafer Summary 需要成熟转置表；
- 自助 Pivot/OLAP 成为明确需求；
- Ant Design Table 在真实数据规模/交互下出现不可接受的体验问题；
- 商业授权成本低于自研高级 Grid 功能的总成本。

## 3. 架构要求

业务页面不得直接依赖具体 Grid。

```text
Page
 ↓
EngineeringTable API
 ├─ AntDTableAdapter        ← v0.5 默认
 └─ WijmoFlexGridAdapter    ← 未来可选
```

统一接口至少覆盖：

```text
columns
rows
pagination
sort
filter
selection
fixed/frozen columns
export
cell formatter
loading/error
```

## 4. 不允许因为升级 Grid 而修改的层

- SQL Server Canonical Model；
- Parser / Normalizer；
- Measurement Long Format；
- Spec/Bin Resolver；
- FastAPI API 语义；
- ECharts 分析组件；
- Data Governance 模型。

## 5. 决策原则

Wijmo/C1 是生产力升级项，不是架构前提。

优先顺序：

```text
先验证 Ant Design Table 是否满足
→ 有明确痛点
→ 做小规模 PoC
→ 计算授权成本与开发节省
→ 决定是否升级
```
