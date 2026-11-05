# 固定运行步骤与恢复

> 最后更新：2026-11-05
> v1.3.0，Python 3.11+。macOS本地验证；Windows/Linux脚本兼容性仍需目标机验证。

以下命令由智能体执行，用户只需给资料并口述需求。<技能目录>是实际安装路径，<项目目录>是本单可写目录，始终加引号。macOS/Linux探测python3，Windows探测python或py -3，不复制其他机器用户名或解释器路径。

## 一次准备

```text
python "<技能目录>/scripts/bootstrap.py" --project-dir "<项目目录>"
```

使用输出的python路径执行后续命令（简称<项目Python>）。固定3个依赖，版本相同直接复用；首次须安装，支持--wheelhouse匹配系统/CPU/Python的离线包。不能循环安装。先完成读写预检再整套识图。

后文`RT <命令>`表示以下完整前缀，RT只是文档缩写，**不是可运行命令**：
```text
"<项目Python>" "<技能目录>/scripts/takeoff_runtime.py" --project-dir "<项目目录>" <命令>
```

```text
RT init --source "<图纸.pdf>" --name "本单名称" --model "界面实际模型全名"
RT render
RT next
```

init允许重复--source；读取、渲染首图、写入/重开嵌图Excel、改名/删除全部预检通过后才继续。原图复制为sources快照（原文件不修改），UUID和源哈希绑定本单；后续修改原始文件不会改旧快照，换图必须新建修订项目。

render一次保存1800px页面、原生文字、带归一化坐标的数字索引，按哈希复用；可 `render --pages D001:1,D001:2`。扫描图索引为空，未内置OCR。先实际打开一页验证宿主能看尺寸线。doctor检查本地依赖，不能证明宿主模型视觉能力。

## 识图、定位、断点

```text
RT next --limit 4
RT query --doc D001 --page 1 --query "2400" --limit 20
RT query --doc D001 --page 1 --region 0.1,0.2,0.8,0.9
RT crop --id Q001 --doc D001 --page 1 --box 0.1,0.15,0.85,0.9
RT checkpoint --input "<本批.json>"
RT status
```

query仅帮助定位原生数字；即便数字匹配，也须看图核对属于哪条尺寸线。坐标统一为渲染后整页左上原点0～1（已处理旋转/CropBox）；crop默认4200px整页再裁，--edge最大8000。只追加查看有疑点区域。默认next最多4页、16条附近实体；跨页关联查 `status --full`，不要反复加载全台账。

报价图片需产品局部裁图：优先`crop --id Q001`与明细ID一致；若使用`p2-struct`等临时裁图ID，checkpoint中须明确`{"id":"Q001","thumbnail":"<crop返回的image>"}`。配件共享父柜图时也明确填路径，优先截带计数标注的配件局部。裁图须来自本项measurement来源且页类型为product_drawing；户型图、context来源及整页缓存不会自动成为报价缩略图。找不到有效裁图时audit返回thumbnail_issues，Excel显示待补缩略图；补裁图后直接export，无需重新识图算量。

本批JSON见 [example-batch-v3.json](../assets/example-batch-v3.json)。新项完整填写，旧项按ID增量合并；嵌套对象/数组整体替换。更新已有项/settings/删除需要reason和新batch_id；同批同内容幂等。settings仅rule_overrides/model_info。未出现在新批中的旧项不会删除；delete_ids只用于明确删除。model_seconds/platform_credits只填实际可见值，未知省略。

checkpoint自动对新/变更的读数核对缓存数字索引；模型不得自行编造source_checks。纯补价/改规则不会重新识图。每批检查返回的review和diff，发现证据格式错误只修对应字段。

## 改规则、补价格

```json
{"batch_id":"price-001","reason":"用户：Q001每平米35元","items":[{"id":"Q001","unit_price":35,"price_unit":"平米","price_note":"Q001每平米35元"}]}
```

规则覆盖格式见custom-rules.md。保持测量尺寸不变，改变计价算法；新单位与原单价不匹配时失效待补。分类补价明确匹配ID；价格留空与免费0不同。

```text
RT profile save --input "<已确认档案草稿.json>" --out "<服务商规则.json>"
RT profile preview --input "<服务商规则.json>"
RT profile apply --input "<服务商规则.json>"
```

用户明确长期保存时用profile save写用户指定档案文件；不会自动读取某台机器旧档案。规则档案与价格档案分开，本单和单项规则保留优先权。

## 复核和导出

```text
RT audit
RT review
RT export
RT export --previous "<最新手改表.xlsx>"
```

export返回真实xlsx路径才算交付。每次另存exports/<时间-编号>/，含安装报价表.xlsx、同名.manifest.json、takeoff.json、calculation/JSON与CSV、逐项复核.html。保留整个项目供继续计算；客户可只收Excel。HTML离线嵌图，无网络上传，可填写反馈并下载JSON。

人工Excel：汇总D改分类价、明细I覆盖单项价、L改备注、明细S写复核意见。配对manifest保持同名同目录。接续先三方比较“导出基准—当前台账—人工表”，真正编辑写回state；未改格不能覆盖后续口述修改。数量/尺寸/金额/公式/结构/排序等不支持自动回读，准确报改单元格，保留原表逐行确认；旧版无UUID不伪造自动绑定。

```text
RT import-review --input "<复核反馈.json>"
```

反馈只保存pending，不改变确定量。按用户确认字段修改对应项，以evidence.field_confirmations记录字段/选值/原因/原话；保留原始dimension_readings，不伪造新图纸读数，其他疑点继续保留。checkpoint可附 `resolved_inputs:[{"id":"反馈ID","reason":"本次仅按用户确认总高2600修订，高度证据见同批Q001"}]`，必须同批修订该行；不能以一句“确认”清空整行疑点。其他独立冲突仍会复核。卡片版本过旧拒绝导入，重新生成卡片并对照复制尚未处理的意见，不静默覆盖新结果。

## 移机、升级、恢复

```text
RT archive --out "<项目目录外>/项目归档.zip"
RT restore --input "<项目归档.zip>"
RT migrate --from "<旧v1.2项目目录>"
RT upgrade-evidence
```

restore/migrate的--project-dir指向不存在或空的新目录。归档含真实客户图纸和计算状态，仅交给授权接收方；不是公开技能包。恢复验证全部文件哈希和安全路径，技能ZIP只含合成示例。migrate保留旧目录、旧观察和证据等级，不能把旧数据变成已核验；upgrade-evidence切换新检查，缺项进入复核，需要逐项补证据。

state.json为唯一已提交台账；changes保留修订。人工表接续已写入state后若导出失败，价格仍保存，直接再export。不要回头重新识图。缓存损坏仅重做对应render/crop。崩溃锁仅在核实PID退出后移除。

run-log.jsonl只统计脚本阶段、缓存命中及用户提供的平台积分。脚本耗时不等于模型总耗时。故障按原始错误修复后最多重试一次；不猜测ModifyBackup开关、不关闭安全限制、不自动切模型。
