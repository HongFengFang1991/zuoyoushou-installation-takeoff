# 服务商安装说明

> 最后更新：2026-11-05 · 固定版本：v1.4.3

本仓库：`HongFengFang1991/zuoyoushou-installation-takeoff`。以下指令可直接复制给智能体。

## WorkBuddy：复制给智能体

先在 WorkBuddy 打开一个服务商本地工作目录，再发送：

```text
请把 https://github.com/HongFengFang1991/zuoyoushou-installation-takeoff 的 installation-takeoff 技能安装到当前 WorkBuddy 项目。
固定使用 v1.4.3 标签，读取该版本 INSTALL.md。
下载该版本完整 skills/installation-takeoff 目录，安装到当前项目的 .codebuddy/skills/installation-takeoff。
保留 scripts、references、assets、agents 和 SKILL.md，按同版本 release-manifest.json 验证全部文件 SHA256。
如已有旧版，先备份到技能扫描目录之外，再替换；不要修改客户图纸、已有报价项目和我的规则/价格档案。
安装后读回 SKILL.md，报告版本、实际目录、完整性检查和 Python 3.11+ 是否可用。
仅报告实际完成的步骤，暂不运行真实客户图纸。如果不能访问 GitHub、写入或注册技能，说明具体原因并给出该版本 ZIP 导入入口，不要反复重试。
```

项目级安装只对该项目生效，不代表所有 WorkBuddy 任务都已安装。没有本地项目目录时，使用下面的 ZIP 导入方式。

本地文件校验只能检查下载完整性，不证明模型识图准确。若缺少 Python，在安装好 Python 3.11+ 后继续；不要自动改系统全局 Python 包。依赖准备遵循技能 `references/runtime.md`，进入单独的验证项目运行 bootstrap；必须完成读写、转图和嵌图导出预检后再处理整套图纸。

## WorkBuddy：ZIP 导入兜底

1. 打开 `https://github.com/HongFengFang1991/zuoyoushou-installation-takeoff/releases/tag/v1.4.3`。
2. 下载 `installation-takeoff-workbuddy-v1.4.3.zip`，不要选 GitHub 自动生成的整个仓库 Source code ZIP。
3. WorkBuddy「专家·技能·连接器 → 技能 → 添加技能 → 上传技能」导入此 ZIP。
4. 新建任务，选中技能，让智能体读取 SKILL.md 并报告版本；再用合成示例做预检。

WorkBuddy 包内 SKILL.md 位于 ZIP 根目录。GitHub 网络不通时，可由发布者分发同一个 ZIP，并核对 release-manifest.json 中的 SHA256；不要改用来历不明的重打包版本。

## Codex：复制给智能体

```text
请使用 skill-installer 安装 https://github.com/HongFengFang1991/zuoyoushou-installation-takeoff 中的 skills/installation-takeoff，固定版本 v1.4.3。
先读取该版本 INSTALL.md，按 release-manifest.json 校验文件完整性。
已有旧版时，先备份到技能扫描目录之外，不动已有报价工程和自定义规则。
完成后报告技能版本、安装路径和校验结果，告诉我何时可调用。暂不计算客户图纸。
```

由 Codex 使用自己实际安装的 skill-installer 路径运行：

```text
python "<skill-installer目录>/scripts/install-skill-from-github.py" --repo HongFengFang1991/zuoyoushou-installation-takeoff --path skills/installation-takeoff --ref v1.4.3
```

默认安装到 `$CODEX_HOME/skills/installation-takeoff`（未配置时为 `~/.codex/skills/installation-takeoff`）。安装器遇到同名目录会停止；先在扫描目录之外保存备份，再安装，失败可恢复。安装后的技能在下一轮可用。

## 安装后的第一次验证

```text
使用 installation-takeoff v1.4.3。先在技能目录以外新建一个“安装验证”项目，按照 runtime.md 运行环境预检。
使用随包合成图检查能否实际看清尺寸，跑一次识图算量和带产品缩略图的 Excel 导出。
验证工作簿可以打开、两张页签存在、页码可追溯、缩略图对应产品、数量可复算。
告诉我成功项、失败项和真实生成文件路径，不把下载成功当作验证成功，不使用客户图纸。
```

## 更新与访问

- 服务商测试与报价记录使用固定版本；升级时指定新标签，不静默跟随 main。
- 技能版本、模型界面全名和自定义规则应随本单记录。
- 私有仓库需要每位使用者的访问权限和本机认证；不要把发布者账号令牌写进指令或技能包。
- 当前发布无跨模型同精度保证；看到尺寸冲突应保留复核状态，不能为了统一结果抄用其他模型的结论。

## 官方操作参考

- [WorkBuddy 技能导入](https://www.codebuddy.cn/docs/workbuddy/From-Beginner-to-Expert-Guide/Function-Description/Skills-Market)
- [WorkBuddy 项目级 .codebuddy/skills 配置](https://www.codebuddy.cn/docs/workbuddy/From-Beginner-to-Expert-Guide/Function-Description/Project)
