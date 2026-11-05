# 安装与第一次验证

> 最后更新：2026-11-05 · 技能版本：v1.4.3

## 给服务商：复制这一句话

在 WorkBuddy 中发送：

```text
请安装这个地址的技能：https://github.com/HongFengFang1991/zuoyoushou-installation-takeoff
```

用户只需按提示选择本地工作文件夹。下面的步骤交给智能体执行。

## 给执行安装的智能体

1. 仓库为 `https://github.com/HongFengFang1991/zuoyoushou-installation-takeoff`。服务商的一句话入口不要求填写分支或版本：按本页当前说明安装 v1.4.3 技能，先解析 `v1.4.3` 标签对应的实际 commit SHA，在本次下载中固定该 SHA，避免混用不同提交文件。2026-11-05发布核对时标签对应 `180822adccb98beb4f939b5f0c29e3801235f8bc`。
2. 当前已发布 [v1.4.3 Release](https://github.com/HongFengFang1991/zuoyoushou-installation-takeoff/releases/tag/v1.4.3)，可下载 [WorkBuddy 专用安装包](https://github.com/HongFengFang1991/zuoyoushou-installation-takeoff/releases/download/v1.4.3/installation-takeoff-workbuddy-v1.4.3.zip)。安装动作遵循本页最新流程，技能文件与校验清单使用同一固定提交。标签或下载入口无法访问时先说明具体错误，不静默切换 main；用户明确同意使用开发版时才解析 main 并固定提交。
3. 下载同一提交下完整的 `skills/installation-takeoff/` 目录；保留 SKILL.md、scripts、references、assets、agents。不能只复制入口文件。按同一提交 `release-manifest.json` 的 `source_files` 校验技能目录内全部列明文件的 SHA256；字段路径相对于技能根目录，包校验值仅用于实际存在的对应 ZIP。
4. 项目安装位置为用户确认的本地项目下 `.codebuddy/skills/installation-takeoff`。项目未打开或不能写入时，先引导选定本地工作目录。不臆测用户目录，不自动扩大权限。
5. 已有同名技能时，先把旧版备份到技能扫描目录之外，再替换；保留客户图纸、已有报价工程与规则/价格档案。
6. 读回 SKILL.md，报告实际安装路径、版本、提交 SHA、校验结果与 Python 3.11+ 可用性。缺 Python 时说明安装方式，不改系统全局包。
7. 在技能目录之外创建单独验证项目，严格按 `references/runtime.md` 执行 bootstrap 和预检。依赖安装在该项目的环境中。
8. 用随包合成示例验证实际图片可见、尺寸归属可核对、能完成识图存档与带图 Excel 导出。检查工作簿可打开、包含「报价明细」「报价汇总」、缩略图对应产品、来源页码可追溯。只报告实际产生且存在的文件路径。
9. 分别报告安装、环境、视觉、导出成功或失败；下载成功不等于完整验证通过。不在安装阶段处理客户图纸。
10. GitHub不可访问、注册失败或工具能力不足时，明确具体阻塞。不得虚构已安装或已导出；不反复重试。若存在已发布且可访问的专用 ZIP，可提供准确导入入口。WorkBuddy手动导入入口为「专家·技能·连接器 → 技能 → 添加技能 → 上传技能」，该入口以客户端版本为准。

WorkBuddy官方支持ZIP技能导入；对话检索安装技能市场与从外部仓库复制完整本地技能不是同一机制。以上自动流程要求智能体具备联网、文件写入及本地执行能力。[官方技能说明](https://www.codebuddy.cn/docs/workbuddy/From-Beginner-to-Expert-Guide/Function-Description/Skills-Market)

## 豆包工作模式

先做能力检查，不宣称已完成适配。不把 WorkBuddy 安装路径、界面入口或自定义模型配置套用到豆包。豆包若不支持该版本的外部技能安装、图片工具、Python或所需依赖，说明缺口并停止完整算量承诺。[官方 Skill 接入指南](https://open.doubao.com/document/doubao-developer-guide/skill-integration-guide)

## 更新与接续

升级记录实际提交与技能版本；不在已有单据中静默升级。安装目录用于保存技能，本单项目目录保存图纸快照、状态与导出，两者分开。升级不覆盖已有项目，后续仅按技能支持的迁移方式处理。

