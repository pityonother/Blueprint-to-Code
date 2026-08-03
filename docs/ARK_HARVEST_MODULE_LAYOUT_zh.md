# Harvest 模块边界

Harvest Ranking Contract v2 的公开 JSON schema、排序顺序与错误码不因本次拆分改变。模块依赖固定为：

```text
contracts / identity
  -> facts
  -> model
  -> evaluation
  -> repository / build / API
  -> UI
```

## Python 职责

- `blueprint_translator.harvest.contracts`：静态模型身份与证据常量。
- `blueprint_translator.harvest.facts`：Unreal identity、攻击与 HarvestComponent 事实提取。
- `blueprint_translator.harvest.model`：唯一的 complete-node 公式、per-attack 计算与 ranking policy。
- `blueprint_translator.harvest.evaluation`：catalog contract、聚合、forward engine 与 reverse specialty projection。
- `blueprint_translator.harvest.repository`：数据集校验、缓存和查询服务；不实现 Harvest 公式。
- `blueprint_translator.harvest.build`：creature discovery、ancestry、asset projection 与 catalog assembly。

Evaluation 内部继续按职责拆分：

- `evaluation/contracts.py`：四个 metric 的 score basis、单位、static/runtime 边界与公开 policy 常量。
- `evaluation/runtime.py`：runtime profile 选择、preliminary opt-in 与 observation eligibility。
- `evaluation/variant_selection.py`：全目录 canonical variant 分类、歧义审计与同物种 variant 投影。
- `evaluation/species_evaluation.py`：单物种攻击事实计算；公式实现仍只来自 `harvest/model/`。
- `evaluation/tier_projection.py`：confirmed/conditional 独立 competition rank 与 bounded Top-N。
- `evaluation/result_projection.py`：Ranking Contract v2 coverage、identity、methodology 与 JSON 响应组装。
- `evaluation/legacy.py`：隔离一个兼容窗口内的 v1 响应。
- `evaluation/engine.py`：预索引 catalog 并编排上述服务，不复制公式或投影规则。

Repository 内部继续按职责拆分：

- `repository/dataset_loader.py`：JSON/SQLite 数据集加载、源绑定校验和节点查询。
- `repository/revision_binding.py`：evaluation/component revision 与模型身份绑定。
- `repository/runtime_overlay.py`：runtime profile 选择、过滤和 observation 缓存隔离。
- `repository/caches.py`：所有 LRU 容量和 repository 可变缓存状态。
- `repository/forward_service.py`：正向排行、同 tier baseline 与 legacy ranking fallback。
- `repository/specialty_service.py`：反向 specialties、稳定分页排序和 legacy specialty fallback。
- `repository/creature_service.py`：物种折叠与 creature list 投影。
- `repository/service.py`：只组合上述服务并初始化路径/缓存，不拥有查询或排行规则。

以下旧入口保留一个兼容窗口，只转发到新模块：

- `blueprint_translator.harvest_ranking`
- `blueprint_translator.harvest_evaluation_catalog`
- `blueprint_translator.harvest_node_repository`

`build_ark_harvest_evaluation_catalog.py` 只解析 CLI 参数、调用 builder、原子写入结果并打印摘要。

## TypeScript 职责

- `explorer.ts`：公开 facade。
- `controllers/explorer-controller.ts`：状态、事件和 API 协调。
- `filters.ts`：查询参数和筛选视图。
- `format.ts`：无状态格式与 identity helper。
- `views/`：dataset status、node list、node detail 与 ranking renderer。

所有 Harvest HTML renderer 统一调用 `src/shared/html.ts` 的 `escapeHtml`。服务端给出的 authoritative ranking rows 不在客户端重新排序。

## 验证

```powershell
python -m pytest tests/test_harvest_module_boundaries.py tests/test_harvest_refactor_characterization.py -q
python -m pytest -q
python -m ruff check <本次变更的 Python 文件>
npm run build
node tests/harvest_frontend_contract.mjs
```

`test_harvest_module_boundaries.py` 检查循环依赖、层级逆向导入、公式唯一所有权、兼容入口与薄 facade。characterization tests 锁定 byte-equivalent JSON、旧 import surface、排名顺序及输入不变性。
