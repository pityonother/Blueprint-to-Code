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

## 2. 最常用流程

这是推荐流程。先不要手动复制图页。

1. 在 ARK DevKit 里找到蓝图资产。
2. 复制 Blueprint Object Path，格式一般像这样：

```text
/Game/ASA/Dinos/ShoulderDragon/ShoulderDragon_Character_BP.ShoulderDragon_Character_BP
```

3. 回到工具网页。
4. 在 **DevKit 导出** 区域，把路径粘贴到输入框。
5. 点 **从 .uasset 读取图内容**。
6. 等任务完成。
7. 左侧选择这个资产。
8. 先看 **行为说明**。
9. 再看 **完整报告**。
10. 如果报告说有失败图页，再点 **只补采失败图页**。

多数情况下，做到第 8 步就能知道这个蓝图大概是干什么的。

## 3. 先看哪些报告

按这个顺序看。

### 1. 行为说明

最重要。告诉你这个蓝图在游戏里有什么作用。

对应文件：

```text
captures/<资产名>/output/behavior_summary.md
```

### 2. 完整报告

看读到了多少图、多少节点、用了哪些变量和函数。

对应文件：

```text
captures/<资产名>/output/asset_report.md
```

### 3. 诊断

看还缺什么，哪些地方不确定，需不需要补采。

对应文件：

```text
captures/<资产名>/output/diagnostics_report.md
```

### 4. .uasset 默认值

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
8. 全部补完后点 **重新分析标准报告**。

## 6. 怎么导出组件和额外默认值

一般先用 **从 .uasset 读取图内容**。

如果报告提示组件信息不足，再做这个：

1. 在网页 **DevKit 导出** 区域粘贴 Blueprint Object Path。
2. 点 **保存路径并复制 Python 命令**。
3. 到 ARK DevKit 的 Python Console 里粘贴运行。
4. 回网页点 **刷新资产**。
5. 点 **重新分析标准报告**。

## 7. 常用按钮说明

### 从 .uasset 读取图内容

最常用按钮。直接读取资产文件里的蓝图图内容。

### 行为说明

看这个蓝图对玩家或游戏有什么实际作用。

### 完整报告

看详细结构、变量、函数、图页数量。

### 诊断

看缺什么、哪里不确定。

### 只补采失败图页

只让你补工具没读好的图页。

### 重新分析标准报告

补采、导出或改 notes 后点它。

### 生成 debug 包

只有排查问题时才用。普通使用不用点。

## 8. 常见问题

### 找不到 `.uasset`

检查 Blueprint Object Path 是否正确。

路径应该类似：

```text
/Game/.../AssetName.AssetName
```

还要确认对方电脑上安装了 ARK DevKit，并且资产文件确实存在。

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

## 9. 给别人一句话说明

解压，双击 `START_HERE.bat`，粘贴 Blueprint Object Path，点 **从 .uasset 读取图内容**，先看 **行为说明**，需要补采时再点 **只补采失败图页**。
