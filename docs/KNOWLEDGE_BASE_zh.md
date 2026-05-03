# ARK 背景知识库说明

这份知识库用于给蓝图分析报告补上下文。它不是百科，也不是最终结论库，而是把整个 ARK DevKit 的资产索引、已深度解析的 `.uasset/.uexp` 报告、默认值、图页调用、native/父类缺口和证据来源整理成机器可读的数据。

## 现在先做什么

第一版会做两件事：

1. 扫描本机 ARK DevKit `ShooterGame/Content`，建立全局 `.uasset` 资产索引。
2. 把已有深度解析结果合成专题模块。当前第一个专题默认围绕巨盗龙生成，使用这些已有采集结果：

- `captures/Gigantoraptor_Character_BP`
- `captures/PrimalItemResource_GigantoraptorFeather`
- `captures/Buff_GigantoraptorCallPlayer`

运行：

```bat
runtime\python\python.exe scripts\build_ark_knowledge_base.py
```

输出目录：

```text
knowledge_base/
```

## 先看哪个文件

先看全局资产索引：

```text
knowledge_base/global/asset_index_report.md
```

再看巨盗龙专题报告：

```text
knowledge_base/reports/gigantoraptor_knowledge_base.md
```

再看机器入口：

```text
knowledge_base/index.json
```

## 每个文件是什么

- `knowledge_base/index.json`：知识库入口，列出纳入的资产、系统主题和报告。
- `knowledge_base/global/asset_index.sqlite`：整个 ARK DevKit Content 目录下的 `.uasset` 文件索引数据库。
- `knowledge_base/global/asset_index_summary.json`：全局索引摘要，适合直接交给 AI 看。
- `knowledge_base/global/asset_index_report.md`：全局索引的人读版报告。
- `knowledge_base/assets/*.json`：单个蓝图资产摘要，包括默认变量、图页、函数调用、变量读写、外部引用和证据。
- `knowledge_base/systems/gigantoraptor.json`：把巨盗龙相关资产按主题聚合，例如羽毛继承、幼崽训练、Buff、巢穴驯养、XP/宝箱。
- `knowledge_base/native_functions.json`：记录蓝图里看得到调用、但看不到内部实现的 native/父类函数。
- `knowledge_base/evidence.json`：从报告中抽出的行级证据，方便 AI 回答时说明来源。

## 怎么加新资产

先用 GUI 读取新蓝图，确认 `captures/<资产名>/` 里有报告和 uasset JSON。

然后运行：

```bat
runtime\python\python.exe scripts\build_ark_knowledge_base.py --asset Gigantoraptor_Character_BP --asset PrimalItemResource_GigantoraptorFeather --asset Buff_GigantoraptorCallPlayer --asset 新资产名
```

如果要换一个主题名：

```bat
runtime\python\python.exe scripts\build_ark_knowledge_base.py --focus gigantoraptor
```

## 目前第一版的定位

它已经能回答：

- 整个 DevKit 里大概有哪些 `.uasset`，按目录、领域、资产类型怎么分布。
- 哪些资产已经有 captures 深度解析，哪些只是文件索引。
- 哪些资产参与巨盗龙机制。
- 羽毛、幼崽训练、Buff、巢穴驯养分别有哪些默认值和图页证据。
- 哪些问题还卡在 native/父类函数里。

它还不能直接保证：

- `GetDinoStatDistributionAgainstMax` 的内部公式。
- 宝箱和 XP 的完整来源链。
- 所有 ObjectProperty 数字索引都已解析成具体资产路径。
- 全部 DevKit 资产都已经完成深度反序列化。全局索引只是底座，深度理解要逐步补读重点资产。

这些会作为下一轮知识库建设目标。
