# Blueprint to Code 使用手册

这份手册只讲怎么用。

## 1. 启动

1. 解压完整环境版 zip。
2. 双击：

```bat
START_HERE.bat
```

3. 浏览器会打开：

```text
http://127.0.0.1:8765/
```

完整环境版已经带了 Python，不需要先安装 Python，也不需要安装 Node.js。

如果网页没自动打开，就自己复制上面的地址到浏览器。

工具会先读取 Epic Games Launcher 的安装清单，自动识别包括
`E:\AKD\ARKDevkit` 在内的自定义安装位置。正常通过 Epic Launcher 安装时，
不需要再手工改路径。

如果清单被删除、损坏，或 DevKit 是手工移动的，再配置 DevKit Content 目录：

1. 把项目里的 `devkit_content_root.example.txt` 复制一份。
2. 把复制出来的文件改名为 `devkit_content_root.txt`。
3. 打开 `devkit_content_root.txt`，里面写他自己的 DevKit Content 目录，例如：

```text
D:\Epic Games\ARKDevkit\Projects\ShooterGame\Content
```

注意：`/Game/PrimalEarth/Dinos/Dodo/Dodo_Character_BP.Dodo_Character_BP` 这种不是 Windows 文件路径，而是 Unreal Object Path。工具会用它拼到 DevKit Content 根目录下面去找真正的 `.uasset`。

环境变量或 `devkit_content_root.txt` 的显式配置优先于自动发现。为某台伙伴电脑
专门生成的完整环境包也可以直接带入这份配置，避免依赖该电脑的 Launcher 清单。

如果 mod 资产放在外置 mod Content，例如：

```text
G:\ARKDevkit\Projects\ShooterGame\Mods\Kaminan_server\Content
```

复制 `devkit_path_mappings.example.txt` 为 `devkit_path_mappings.txt`，加一行：

```text
/Game/Mods/Kaminan_server=G:\ARKDevkit\Projects\ShooterGame\Mods\Kaminan_server\Content
```

这样 `/Game/Mods/Kaminan_server/...` 会映射到这个 mod 的 `Content` 目录。

如果输入框里粘贴的是 `Kaminan_server/.../Asset.Asset` 这种 mod 相对路径，工具会自动规范化成 `/Game/Mods/Kaminan_server/.../Asset.Asset`。

## 2. 最常用流程

界面从上往下就是 4 步，按数字走就行。先别管下面折叠起来的“高级功能”。

1. 在 ARK DevKit 里找到蓝图资产，右键 → **Copy Reference**。路径一般像：

   ```text
   /Game/ASA/Dinos/ShoulderDragon/ShoulderDragon_Character_BP.ShoulderDragon_Character_BP
   ```

2. 回到工具网页。**第 1 步**：把这串路径粘贴到顶部那个大输入框。

3. **第 2 步**：点绿色大按钮 **从 .uasset 读取图内容**。它会自动解析
   `.uasset`/`.uexp`，写入规范化证据库，并生成当前 revision 的小型 AI 索引。等任务条提示完成。

4. **第 3 步**：看“读取结果”。三个数字：

   - **已完整读取**：达到当前自动恢复门槛（存在节点、Node-Pin 覆盖率至少
     75%，且至少恢复一条连线）。它不代表每个节点、连线和属性都已还原；
     下结论前仍需检查 Gap、覆盖率和启发式链接标记。
   - **部分读取**：能看，但有些连线或字段是工具猜出来的（启发式），相关说明
     仅供参考。
   - **需要手动补充**：这些图页 `.uasset` 解析失败，需要回 DevKit 复制粘贴。

5. **第 4 步**：先点 **AI 证据索引 (agent_index)**。这是默认的新入口，里面有
   Graph/Node/Pin/Wire/Default/Gap 数量、恢复率、当前 revision 和按需查询命令。

如果你需要给人看的长篇中文说明，再点 **生成 / 刷新人类报告**。它会复用这个资产的同一 Object Path，以 `dual` 模式重新读取当前源，再生成与新 evidence 同源的
`behavior_summary.md`、`asset_report.md` 等 legacy 人类报告；默认 indexed 读取不会为了这些长报告重复生成整套旧产物。

如果第 4 步上面出现红色的“需要手动补采的图页”面板，按里面的按钮即可：
**载入失败图页到补采队列** → 在 DevKit 里 `Ctrl+A`、`Ctrl+C` → 展开补采面板
逐个保存。

多数情况下，把 `agent_index.md` 交给 AI，再让它按里面的命令查询具体证据就够了；不要把整个资产目录一次性丢给 AI。

> 想用旧的 DevKit 默认值导出器、做两个资产对比、生成 debug 报告、判定
> notes.md 误报、查看历史资产或运行日志，统一在页面最下方“高级功能”折叠区里。

## 3. 先看哪些索引和报告

按这个顺序看。

### 1. AI 证据索引（默认、当前 revision）

最重要，也是新资产默认一定会生成的文件。它很小，告诉 AI 当前证据的身份、数量、恢复率、未展开内容和下一条查询命令。

对应文件：

```text
captures/<资产名>/output/agent_index.md
```

`AVAILABLE_NOT_RETURNED` 表示数据存在，只是这一次为了省 token 没展开；`NOT_RECOVERED` 才表示解析器没有恢复；`SOURCE_NOT_AVAILABLE` 表示实现位于父类、native 或其他资产。

### 2. 按需查询具体证据

索引里自带可复制命令。常用顺序是 `search → entity → neighborhood/trace → gaps`。AI 可以用稳定的 `bp://` ID 只拿一个 Node 的 Pin 和 Wire，而不用打开整张图 JSON。每次查询接受 500–8,000 estimated tokens；过小会直接报错，超过 8,000 会明确截到有效上限。

### 3. 行为说明（按需 legacy 报告）

给非程序同事看的中文行为说明。fresh indexed capture 可能没有这个文件；需要时点 **生成 / 刷新人类报告**。如果它是之前保留下来的旧文件，网页会标成“历史/按需报告”，它可能早于当前 evidence revision。

```text
captures/<资产名>/output/behavior_summary.md
```

### 4. 完整报告（按需 legacy 报告）

看读到了多少图、多少节点、用了哪些变量和函数。

对应文件：

```text
captures/<资产名>/output/asset_report.md
```

### 5. 诊断（按需 legacy 报告）

给人阅读的旧格式诊断。AI 判断缺口时应优先使用 Evidence Store 的 `gaps` 查询，因为它能区分预算省略、解析失败和来源不在本资产。

对应文件：

```text
captures/<资产名>/output/diagnostics_report.md
```

### 6. .uasset 默认值

看变量默认值，比如 `MinStoredXPForTreasure`、`MaxStoredXP`、冷却、倍率之类。

对应文件：

```text
captures/<资产名>/uasset_class_defaults_report.md
```

## 4. 什么时候需要手动补采

只有工具提示需要时才补采。

需要补采的常见情况：

- `.uasset` 图内容读取失败。
- 某些图页是 `failed` 或 `needs_clipboard`。
- 你想确认某个复杂图页的精确连线。

不要一开始就把所有图页都复制一遍。

## 5. 怎么补采失败图页

1. 在网页里点 **只补采失败图页**。
2. 工具会把失败图页放进队列。
3. 看队列当前图页名字。
4. 去 ARK DevKit 打开同名图页。
5. 在图页里按：

```text
Ctrl+A
Ctrl+C
```

6. 回到网页点 **保存队列当前项**。
7. 继续下一页。
8. 全部补完后点 **生成 / 刷新人类报告**。

## 6. 怎么导出组件和额外默认值

一般先用 **从 .uasset 读取图内容**。

如果 `agent_index`、`gaps` 或按需报告提示组件信息不足，再做这个：

1. 在网页 **DevKit 导出** 区域粘贴 Blueprint Object Path。
2. 点 **保存路径并复制 Python 命令**。
3. 到 ARK DevKit 的 Python Console 里粘贴运行。
4. 回网页点 **刷新资产**。
5. 点 **生成 / 刷新人类报告**。

## 7. 常用按钮说明

### 从 .uasset 读取图内容

最常用按钮。直接读取资产文件里的蓝图图内容，默认生成 Evidence Store 和 AI 证据索引，不自动生成整套 legacy 长报告。

### AI 证据索引

默认先看。对应当前 evidence revision，适合直接交给 AI；需要细节时让 AI 运行索引中的有界查询。

### 行为说明

按需 legacy 人类报告。看这个蓝图对玩家或游戏有什么实际作用；旧文件可能不对应当前 revision。

### 完整报告

按需 legacy 人类报告。看详细结构、变量、函数、图页数量。

### 诊断

按需 legacy 人类报告。AI 应优先用 `gaps` 查询看缺什么、哪里不确定。

### 只补采失败图页

只让你补工具没读好的图页。

### 生成 / 刷新人类报告

需要最新的人类可读 legacy 报告，或补采、导出、修改 notes 后点它。工具会按同一 Object Path 做一次当前源的 `dual` 读取，再运行兼容报告 renderer。默认 indexed 证据库不会因为旧报告保留而自动删除那些文件。

### 生成 debug 包

只有排查问题时才用。普通使用不用点。

## 8. 可选的原生证据与报告验证

普通 Blueprint 读取不需要 Ghidra。只有问题进入 ARK 原生 C++，例如 loot
quality、item rating 或 Blueprint 节点背后的 native call 时，才需要由开发
伙伴在拥有匹配 DLL/PDB 的电脑运行 Native recipe。

你拿到的公开报告会链接两种小文件：

- `reports/manifests/*.claims.json`：列出每条结论、Evidence ID、假设和失效条件；
- `reports/evidence_manifests/*.native.json`：只保存版本和 target 摘要，不含
  ARK DLL/PDB 或完整反编译。

检查所有报告：

```powershell
runtime\python\python.exe scripts\validate_report_claims.py --all --pretty
```

`LOCAL_EVIDENCE_REQUIRED` 表示公开仓库没有提交完整本机 evidence，需要在对应
DevKit build 上重跑 recipe；它不是“函数不存在”。历史报告可能显示
`PROVENANCE_INCOMPLETE`，此时默认检查保留报告并警告，formal 发布会拒绝。

不要把 Ghidra 伪 C 当作原始源码。需要玩家可见的确定结论时，还要按
[`HARVEST_RUNTIME_TEST_PROTOCOL_zh.md`](HARVEST_RUNTIME_TEST_PROTOCOL_zh.md)
一类协议采集真实 runtime observation。

## 9. 常见问题

### 找不到 `.uasset`

检查 Blueprint Object Path 是否正确。

路径应该类似：

```text
/Game/.../AssetName.AssetName
```

还要确认对方电脑上安装了 ARK DevKit，并且资产文件确实存在。外置 mod 资产需要检查 `devkit_path_mappings.txt` 是否已经把 `/Game/Mods/<ModName>` 指到对应的 `Mods\<ModName>\Content`。

### 报告里还有 unknown

不一定是问题。

运行时变量通常没有固定默认值，比如：

```text
StoredXP
MountCharacter
Target
CurrentWeapon
```

这些显示 unknown 很正常。

真正需要关注的是配置变量，比如：

```text
MinStoredXPForTreasure
MaxStoredXP
Cooldown
Interval
Multiplier
```

如果这些缺值，再看 `.uasset 默认值` 或重新导出 defaults。

### 页面打不开

先确认启动窗口没关。

再手动打开：

```text
http://127.0.0.1:8765/
```

如果端口被占用，用：

```powershell
.\scripts\launch_blueprint_tool.ps1 -NoBuild -Port 8766
```

然后打开：

```text
http://127.0.0.1:8766/
```

## 10. 给别人一句话说明

解压，双击 `START_HERE.bat`，粘贴 Blueprint Object Path，点 **从 .uasset 读取图内容**，先看 **AI 证据索引**，需要补采时再点 **只补采失败图页**；长篇行为说明按需重新生成。
