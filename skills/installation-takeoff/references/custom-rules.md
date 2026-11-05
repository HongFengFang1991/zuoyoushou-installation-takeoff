# 自定义计量、计价与口述补价

> 最后更新：2026-11-04

## 范围与优先级

具体行 > 本项目 > 服务商常用口径 > 标准规则。同层更具体匹配优先；同等具体且不一致，不自动取最后一条，列冲突等待说明。
“本项目矮柜按实际米数，不保底”可直接执行；“柜子35”“厨房40”涉及不同单位/品类，先确认。“抽屉每个20元”直接应用。

未提及部分继续标准规则。默认仅本项目有效；说“以后都这样”才另存服务商口径文件，下个项目告知采用该口径。口述价格默认仅本项目。
每条规则保存 id、版本、来源原话、范围、方式、单位、最低计量值。保留原尺寸、标准量、新量及变更记录。元/平米改元/组同时影响计量，不能只换单价。

| 用户表述 | 处理方式 |
|---|---|
| 柜子全部按投影面积，矮柜也一样 | cabinet 改 projection_area，取消延米保底；缺高度仍待补 |
| 矮柜按实际米数 | 指定矮柜 length，minimum=0，0.8米保留0.8 |
| 灯带按米 | length；必须补每条实际长度，6个位置不能当6米 |
| 衣柜按展开面积 | developed_area；要板件长宽、数量、计入范围及门板/背板口径，不用投影面积乘经验系数 |
| 抽屉含在柜体安装费里 | 保留物理数量，在 excluded 记录“用户确认包含”，不重复收费，不改真实数量为0 |
| 每套上门费200元 | 新增 custom 人工项，标明口述来源 |
| 洞口扣除、异形、阶梯价、最低订单价 | 写清公式/范围/算例；脚本未支持则人工核算，不静默套默认规则 |

## 覆盖格式

takeoff.json 的 rule_overrides 示例：

```json
{
  "id": "project-low-actual-v1",
  "layer": "project",
  "selector": {"product_type": "cabinet", "name": "矮柜"},
  "method": "length",
  "unit": "米",
  "minimum": 0,
  "source_text": "本项目矮柜按实际米数，不保底一米"
}
```

selector 支持 product_type、cabinet_subtype、name、space、id 精确匹配，全部条件均需满足。无正则或模糊匹配。minimum 是每实例最低量，然后乘 repeat_count；计数类最低量为整数。
面积单位平米，length 为米，count 为个/条/扇/套/组/项；manual 必须有用户确认数量、单位、依据。脚本不执行字符串公式；复杂口径由智能体按明确算式核算为 manual 项。缺板件资料只阻断受影响项。

## 价格、修改与对账

v1.3使用checkpoint保存修订：settings.rule_overrides为完整生效规则列表，修改需新batch_id与reason。只补价格用items中对应ID及unit_price/price_unit/price_note，不重读全图。修改事实时同时更新证据并保留原话；不得用改计量规则掩盖原图冲突。

最新人工Excel用export --previous回读价格/备注，保留同名.manifest.json。检测到人工数量、尺寸等变动会阻止静默覆盖，先核对并更新台账再导出。旧版无基准、任意自写公式等情况需单独对账，不能承诺自动恢复。

- 保存 unit_price、price_unit、price_note（原话），不预设区域/行业/服务商价。未填=null，明确免费=0且备注免费。
- 计量单位变化后旧单价失效，重新获取新单位报价；元/平米不能自动沿用为元/米。
- 不同单价/口径不隐藏差异；量按单位分别汇总。只汇总有量有价的行；表格末行统一标“合计”，未齐价时抬头注明合计仅含已填价项目、不代表最终报价；不另设双重总额区块。汇总主表直接填价规则见 quote-layout.md。
- 税率、起步费、搬运费、折扣只接受用户明确提供；v1 脚本仅处理逐行数量×单价，不自动算订单税费或阶梯价。
- Excel 修改后先按稳定行编号比对，再继续。保留人工数量、单价、备注，新版另存并展示改前/改后/原因/受影响汇总。
- “这个柜子算重了”是实体纠错，不写成所有柜体减半；“以后所有矮柜按实际米数”才形成常用规则。无法定位的纠错先请用户指出行或页码。

## v1.3 跨项目服务商档案

规则与价格分别保存JSON，默认不自动加载。用户明确“以后都用”才保存，下一单选择档案先preview再apply。schema_version=1，kind=rules或prices，profile_id/name/version/source_text必填。文件只含用户提供的口径/价格，不含图纸、客户名单或密钥。

```json
{"schema_version":1,"kind":"rules","profile_id":"provider-low","name":"普通矮柜按实长","version":"1","source_text":"以后普通矮柜按实际米数，不保底","rules":[{"id":"low-actual","selector":{"billing_class":"ordinary_low_cabinet"},"method":"length","unit":"米","minimum":0}]}
```

billing_class只在档案中支持ordinary_low_cabinet（cabinet、明确ordinary、高<1000mm），按当前匹配ID展开；不靠名称猜。新增柜体后重新preview/apply。普通selector可按标准类型/稳定子类/房间/ID精确匹配；项目规则和单项规则仍优先。未匹配范围会提示，不能宣称已生效。

```json
{"schema_version":1,"kind":"prices","profile_id":"provider-price","name":"用户自定示例价","version":"1","source_text":"仅示例：用户确认普通衣柜投影安装35元每平米","prices":[{"id":"cabinet-area","selector":{"product_type":"cabinet","cabinet_subtype":"ordinary"},"method":"projection_area","unit":"平米","unit_price":35}]}
```

示例价只演示格式，默认不应用、不作为预设价。价格仅匹配当前方式/单位；未匹配、范围重叠或单位不一致明确报告。长期改价需显式更新价格档案，不能从某次人工表自动推成全局价。
