# Control Center 模块化后续项

| 问题 | 影响 | 建议阶段 |
| --- | --- | --- |
| `src/main.ts` 仍聚合应用壳、Legacy workspace、actions 与 renderer | 后续前端功能仍需理解较大的应用入口，但本次后端移动不应扰动其合同 | Phase 2：单独拆前端应用壳 |
| `src/blueprint/controller.ts` 的进一步职责拆分尚无本阶段验证依据 | 过早移动会扩大前后端同时变更面，并增加行为冻结验证成本 | Phase 3：根据 Phase 2 后的真实边界决定 |
| `routes_blueprint.py` 仍统一承载现有 Blueprint GET 路由 | 新 Visual Plan API 若直接叠加可能再次形成聚合点；当前路由本身稳定 | Phase 4 或出现真实扩展需求时单独拆分 |
