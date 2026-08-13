# Solver v1 Benchmarks

Benchmark 是验收 fixture，不是生产规则。生产 Solver 源码不得出现 Benchmark 的实体专名或同义词特判。

覆盖类型：

1. 公式：Graph structure、Defaults、formula trace 与 example evaluation。
2. 完整本地化枚举：reference closure、localization 与 completeness gate。
3. 全部影响因素：backward slice、readers/writers 与 native completeness gap。
4. 用户公式排行：dataset/schema、expression evaluation、Top-K。
5. Blueprint 修改：current/desired contracts、compare 与 Patch Plan compilation（只规划，不执行）。
6. 无关控制请求：变量所有写入点与 server-only 修改设计。

Metamorphic 验收：替换资产名或语言不改变 DAG；Top 10 改 Top 5 只改变 typed constraint；关闭 localized-only 只移除 localization 算子/需求；降低 completeness 只改变 gate 策略。
