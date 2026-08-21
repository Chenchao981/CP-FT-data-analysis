# 华虹归档待审批清单（2026-08-21）

## 结论

- 93个归档中80个已通过，13个需要业务确认；
- 13个失败实例不是13种格式：6个首先命中Lot规则，7个首先命中Schema规则；
- 去重后需要确认5类Lot取值、1处归档名与文件内Lot不一致、3种参数Schema；
- `C141321.02`、`FA53-5465` 已确认是业务Lot；CP不要求Product，无需再补Product映射。

## A. Source Lot 规则待确认（6个归档）

| # | 归档 | 文件内Source Lot | 建议业务Lot | 其他问题 |
|---|---|---|---|---|
| 1 | `NCEVTG120EB60DB_FA4Z-8752A@202.zip` | `FA4Z-8752A-367A-250226@202`、`FA4Z-8752A-367A-250227@202` | `FA4Z-8752A` | 5个TXT，同时包含Schema A |
| 2 | `NCEVTG120EB60DB_FA4Z-8752AA@202.zip` | `FA4Z-8752A-361A-250219@202` | 待确认 | 归档名是`8752AA`，文件内容是`8752A`；2个TXT，其中1个包含Schema B |
| 3 | `NCEVTG120EB60DB_FA4Z-9844-B@202.zip` | `FA4Z-9844-B-367A-250122@202` | `FA4Z-9844-B` | 2个TXT，现有17参数Schema |
| 4 | `NCEVTG120EB60DB_FA4Z-9847AA@202.zip` | `FA4Z-9847AA-361A-250213@202` | `FA4Z-9847AA` | 4个TXT，现有17参数Schema |
| 5 | `NCEVTG120EB60DB_FA4Z-9847AB@202.zip` | `FA4Z-9847AB-362A-250205@202` | `FA4Z-9847AB` | 1个TXT，现有17参数Schema |
| 6 | `NCEVTG120EB60DB_FA5X-8873-A@202.zip` | `FA5X-8873-A-367A-251105@202` | `FA5X-8873-A` | 1个TXT，现有17参数Schema |

建议审批规则：业务Lot允许“第一段-第二段”，第二段可含字母后缀；若工艺段之前还有独立的单字母段，则该段属于业务Lot。第2项需单独决定以文件内`FA4Z-8752A`为准，还是以归档名`FA4Z-8752AA`为准。

## B. 参数Schema待确认（7个失败实例，3种Schema）

### Schema A：14参数，无VCESAT1/2/3

`CONT@D, CONT@G, CONT@S, IGES1, IEGS1, VTH1, VTH2, ICES1, ICES2, ICES3, BVCES1, BVCES2, IGES2, IEGS2`

涉及归档：`FA4Z-8921`（13个TXT）、`FA4Z-8922`（13个TXT）、`FA4Z-8927`（12个TXT，存在2份相同归档）、`FA4Z-8930`（12个TXT，存在2份相同归档），以及A节第1项`FA4Z-8752A`（5个TXT）。

### Schema B：15参数，无IGES2/IEGS2

`CONT@D, CONT@G, CONT@S, IGES1, IEGS1, VTH1, VTH2, VCESAT1, VCESAT2, VCESAT3, ICES1, ICES2, ICES3, BVCES1, BVCES2`

涉及归档：A节第2项 `FA4Z-8752AA@202.zip` 中1个TXT；同一归档另1个TXT使用现有17参数Schema。

### Schema C：17参数，ICES3位置变化

`CONT@D, CONT@G, CONT@S, IGES1, IEGS1, VTH1, VTH2, VCESAT1, VCESAT2, VCESAT3, ICES1, ICES2, BVCES1, BVCES2, ICES3, IGES2, IEGS2`

涉及归档：`NCEVTG120EB60DB_FA4Z-9844-A@202.zip`，1个TXT。参数名称与现有17参数Schema相同，但`ICES3`列位于`BVCES1/BVCES2`之后，因此仍需按精确列顺序审批。

## C. 请业务回复

1. 建议业务Lot `FA4Z-8752A / FA4Z-9844-B / FA4Z-9847AA / FA4Z-9847AB / FA5X-8873-A`：批准 / 修改；
2. `FA4Z-8752AA`归档名与文件内容不一致：以 `FA4Z-8752A` / `FA4Z-8752AA` / 退回源文件；
3. Schema A：批准 / 不批准；
4. Schema B：批准 / 不批准；
5. Schema C：批准 / 不批准。

审批通过后，将按精确Lot规则和精确有序Schema登记，不改变原始Source Lot、参数列顺序及来源证据。
