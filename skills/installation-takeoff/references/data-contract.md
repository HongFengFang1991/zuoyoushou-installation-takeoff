# 数据、表格与缩略图约定

> 最后更新：2026-11-05

## takeoff.json

参见可运行的 [合成示例](../assets/example-takeoff.json)。这是技能独立格式，不是原报价器 API。

- schema_version：1；project：项目名称；rule_version：quote-app-36791a0。v1.3新项目固定 evidence_policy=3；旧policy1/2可重算草稿但不生成完整最终总额。运行时生成project_id、source_fingerprint、revision，禁止模型自造。
- evidence_mode：visual（确实查看原图）、text_observations（只有文字观察）、synthetic（合成测试）。缺省按文字观察处理，不能把页清单齐全当视觉已核验；后两种只生成依据输入文字的试算，不形成完整报价总额。
- documents：每个文件的 file、page_count。图片按1页登记；CAD转换后按实际出图页登记，并保留原布局对应关系。
- pages：file、page（从1开始）、page_type（product_drawing / spatial_plan / non_billable / unreadable）、reason。所有应处理页均需登记，缺页则 coverage_complete=false。
- items：去重后的实体/配件数组，见下表；rule_overrides：默认空数组，格式见自定义规则。
- changes：变更原话、范围、原值、新值、原因的数组；model_info：实际客户端/模型/版本/时间。禁止把API Key写进文件。

| 字段 | 说明 |
|---|---|
| id / instance_key | 稳定明细编号、实体键；更改名称后保持ID，配件也有自己的键 |
| product_type / name / space | 标准类型、实际名称、房间；cabinet另须cabinet_subtype=ordinary或sideboard，名称不改变新规则。厨房吊/地柜使用wall_cabinet/base_cabinet |
| parent_id | 抽屉、灯带、篮类必填所属主体id |
| sources | 数组，每项 file、page、region、text；role默认measurement；空间/轴测关联用context但不作为尺寸读数源；CAD可增 layout、handle |
| length_mm / height_mm / depth_mm | 单件尺寸，数值或null；不含重复次数 |
| count / repeat_count | 单实例配件/门数量；主体重复套数默认1，>1须repeat_evidence；子配件继承父柜倍数恰好一次，显式不同倍数进入复核 |
| linear_segments | 可选数组，length_mm、role、evidence；role为billable_run、adjacent_depth_projection、uncertain |
| installation_lengths_mm | 配件改按米时，每个安装位置的长度数组；单个也须使用数组。例如6处各500mm写6个500，总量3米。不能只填一个500当作总长 |
| side_panel_area / side_panel_evidence | 餐边柜已确认额外侧板面积及其依据，未确认不填 |
| components | 展开面积板件列表：name、length_mm、width_mm、count、evidence |
| confidence / needs_review / review_reason | 0–1、自查是否需复核、具体原因；不得为了通过脚本调高置信度 |
| user_confirmation | 用户补尺寸/人工确认的原话；保留原图事实与修订记录，不虚称图纸识别 |
| unit_price / price_unit / price_note | null或非负单价、对应单位、用户原话；无价三者留空 |
| excluded_reason | 明确不单列项及用户原因，保留物理数量 |
| manual_quantity / manual_unit / manual_basis | manual规则的人工核算结果；需user_confirmation，不代表AI识图结果 |
| thumbnail | 相对于项目目录的PNG/JPEG路径，须为本项目crop登记的产品局部图；可与明细ID不同，须来自该项measurement来源的product_drawing页，不得借用其他项目、户型图或整页缓存 |
| price_scope | 可选价格适用范围，例如某个房间/合同；不同范围分组。抽屉应显式记录普通抽屉/裤抽适用范围；方位描述中的“裤抽上方”不能把该普通抽屉归为裤抽 |
| evidence | v1.3必填的结构化证据，格式见下文；置信度不能替代 |

## 证据基础字段

`evidence` 必填 basis、boundary、note、dimension_checks、adjustments、ambiguities。三个数组确实未发现对应情况才填[]，不可为了通过检查清空疑点。模型声明仍需原图/人工核对，脚本不能证明读图正确。

- basis：explicit_dimensions（明确尺寸）、explicit_count（明确计数标注）、visual_candidate（仅外观候选，自动待复核）、user_confirmed（有行级user_confirmation原话）。
- boundary：clear、uncertain、not_applicable。尺寸取量必须clear；计数可not_applicable。封板归属/转角投影不清用uncertain。
- dimension_checks：每条 `{field:"length_mm", overall_mm:2750, segments_mm:[36,860,1218,584,50]}`。分段和2748与2750差超0.5mm会进入复核，不自动选大值。明确确认后在该条添加 `resolution:{selected_mm:2750,reason:"按用户确认总宽",user_confirmation:"用户：总宽按2750计"}` 并保留冲突原始数。
- adjustments：每条 `{field:"length_mm",original_mm:2398,deduction_mm:660,evidence:"关联视图与尺寸线证明此段为相邻柜侧投影"}`，计量尺寸应为1738。无明确关联不能因为像600/660就扣。
- ambiguities：尚未解决的疑点文本数组；非空不进确定量。
- explicit_count 另须 count_marks：`[{label:"裤抽×3",quantity:3,region:"柜内下部"}]`。各处标注之和必须等于count。外观格子候选仅留待复核；人工确认后记录user_confirmed和原话。

裁图、批次包和唯一状态文件详见 [固定运行步骤](runtime.md)。不得复制另一行的证据来填空。新批次修订旧行保留历史；价格/规则变化无需重新识图。

不要直接信任AI给的 quantity。辅助脚本从尺寸/计数重算，返回 calculated.json、报价明细.csv、报价汇总.csv、待复核.csv、不单列项.csv。脚本按列转义公式式文本，不运行任意公式；输出目录必须是新目录，防止覆盖手改表格。

## Excel 结构

沿用原报价器12列：空间、产品名称、长度(mm)、高度(mm)、深度/宽度(mm)、计价方式、计价单位、数量、单价、金额、复核状态、备注。追加图纸缩略图、来源/计算依据、行编号；前12列顺序不变。Excel额外用“价格状态”公式列支持实时汇总。

只保留“报价明细”“报价汇总”两个页签，不设待复核页。明细包含依据齐全项与待复核项，后者按当前字段暂计并进汇总；缺必要数据仍留行，数量/金额空白并注明原因。未报价单价/金额留空，不把未知值写0。另附规则版本及本单规则摘要。不同单位不做总数量；同单位不同口径可在明细区分，复杂自定义汇总按类型/方法/单位/单价分别分组。

模板容量为50条明细；实际导出删除预留空行并将单行合计紧跟末条数据，超50条扩展公式及图片映射。汇总D列直接填分类价，明细I列引用，汇总E/F自动求量与金额；单行覆盖价格保留。品牌、分组、公式、草稿状态和验证要求见 [客户报价版式与公式](quote-layout.md)。改尺寸或规则需重新算量，模板不代替识图。

项目台账登记全部预期页；文件抬头准确展示待复核与未覆盖情况。合计只代表已填价项目；覆盖完整、视觉核验、无待复核且全部填价之前不能称完整最终报价。

## 图片嵌入与映射

- 先渲染/读取原图，再截取包含产品及相关尺寸线的区域，保留原页。截图文件命名使用稳定行ID；图上不能补造数字或移除冲突尺寸。
- v1.4.3：报价缩略图不截户型图。产品页中也要定位到具体立面/结构或配件局部，保留完整外轮廓、总尺寸线、冲突标注；不把整页多视图或无关把手照片缩小塞入单元格。实际打开裁图检查后才能用于交付，页类型核验不能替代视觉核对。
- 使用原文件/页/区域创建缩略图清单：row_id、source_file、page、region、image_path。工作簿插图必须依据同一个row_id绑定，排序后复查图片归属。
- 明细M列插入真正图片对象（PNG/JPEG），不能只放本机路径、外链或IMAGE网络公式。宽约140–180px、高约80–110px，保持比例，行高适配；来源栏写全部证据页。
- 多页关联选一张主要取量图放本行，其余证据在本行来源中标注页码并保留于复核HTML；说明“内部图第4页取量，外观第3页关联”。配件可共用父柜图，最好局部放大，不能重复算父柜。
- 待复核行也嵌对应图。无可用图片则显示“待补缩略图”，不能用其他柜图占位。
- 导出优先解析显式thumbnail所指的已登记裁图，再查同明细ID裁图；校验来源角色、页类型、项目路径和源/图像哈希。无有效裁图不回退到rendered整页，audit和导出manifest记录thumbnail_issues，不改变该项算量或复核状态。
- 导出后检查图片嵌入数量、明细ID对应关系、尺寸可读性；关闭/重开后确认仍显示。文件发给他人不应依赖临时路径。
- 当前宿主不能嵌图时，明确交付CSV+按行ID命名的图片文件夹及来源映射，不声称已交付带图Excel。

示例图是合成示意，不能作为真实项目报价。v1.3由宿主视觉工具识图；takeoff_runtime.py负责转图/裁图/存档，export_quote.py固定生成带图Excel。prepare_quote.py仍可独立输出JSON/CSV。自动导出不受旧空模板50行容量限制，按实际行数生成公式与合计。

## v1.3 必需补充：按字段核验、覆盖检查

- `dimension_readings`：逐个实际使用尺寸记录 `{field:"length_mm",value_mm:2400,source_index:0,text:"2400",region:"本柜总宽",box:[0.1,0.2,0.8,0.3]}`。box可省但建议局部定位；source_index为sources零起始下标，必须指向measurement。存在转角扣减时记录读到的原始总尺寸，最终取量与adjustments关联；不能把扣后尺寸伪称图纸原值。展开板件用`components[0].length_mm`等；灯带安装段用`installation_lengths_mm[0]`。
- `chain_check`：每个实际尺寸记 `{field:"length_mm",status:"checked",overall_mm:2400,segments_mm:[1200,1200]}`；图中确实无分段才记 `{field:"length_mm",status:"not_present",reason:"本视图仅标总宽"}`。冲突记录resolution和用户原话，不能空数组跳过。纯计数/人工计量可保留空dimension_readings/chain_check。
- count_marks在policy3另须source_index，标注数量和必须等于count。配件按米仍需要数量标注或用户确认，以及每个安装位置长度。不能把母柜长度当灯带安装长度。
- `unresolved_issues:[{field:"depth_mm",reason:"深度不清"}]`：区分字段。当前计量依赖字段不清则待复核；不参与当前计量的未证尺寸在输出留空并标unverified_fields，不能展示为已确定尺寸。若改规则后依赖它则自动进入复核。无字段的ambiguities仍影响整行。
- `source_checks`由运行时生成，matched只表示数字存在，conflict阻止相关字段，unavailable表示无原生文字、依靠视觉/人工证据，不等于通过。不要在模型批次里编造结果。
- `pages[].views`产品页逐视图列 `{view_id:"A",disposition:"measured",item_ids:["Q001"],reason:"本柜正立面"}`。disposition可measured/duplicate/reference/non_billable/unresolved，后四类须解释；duplicate引用已有ID，unresolved保留疑点。
- 主体cabinet/wall_cabinet/base_cabinet的`accessory_checks`必须检查drawer/light_strip/pull_basket/dish_basket/seasoning_basket。简写对象：`{"checked_types":["drawer","light_strip","pull_basket","dish_basket","seasoning_basket"],"present":{"drawer":["A001"]},"unresolved":{},"note":"其余本次查看未见明确标注或候选"}`。发现候选也建配件待复核行并记录父柜，不直接填“没有”。
- 页面完整、产品覆盖、配件检查分别报告；其中一项未完成就不算完整覆盖。它们仍是代理整理的检查记录，必须人工抽核，不能替代图纸实质审查。
- pending_review_inputs保留卡片/Excel反馈；status=pending时不出完整最终总额。resolved须有修订记录，意见文本不自动变成确认尺寸。

本包合成示例有7项，其中6项可算、1项高度待补；仅测试流程，不预置任何真实项目的“正确答案”。


### 人工只确认一个字段

v3的user_confirmed不能用一句确认豁免整行。须有行级user_confirmation原话，以及非空`evidence.field_confirmations`：

```json
{"field":"height_mm","value":2500,"reason":"采用现场复尺值","user_confirmation":"用户：高度按2500"}
```

此时height_mm改2500；dimension_readings仍保留图上原读数2600，不把用户确认值伪装成图纸文字。原始冲突成为有来源的已确认警告，宽度等未确认字段仍需读数、尺寸链或继续待复核。边界单独确认field=boundary/value=clear，且evidence.boundary=clear；数量用field=count。支持每条安装长度、板件字段、人工数量等路径；不允许all、classification、accessories式全局确认。manual口径也须确认manual_quantity。

## v1.4 报价与证据分离

rows/review保留证据核验结论；quote_rows按原始实体顺序合并两者，每项最多一行。review中的可计算项标provisional=true，使用已有原始字段按生效规则计算，保留reason及公式；无法计算的quantity/amount为null。明确excluded_reason的非计费项不重复进入报价，重复实体未厘清不重复暂计。quote_summary用于Excel/CSV报价汇总，review_count/provisional_quantity显示未确认部分；complete_total仍须全部核验与填价完成。status.summary为报价汇总，verified_summary仅为依据齐全量。
