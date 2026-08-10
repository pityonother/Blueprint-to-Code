# Frontend App Shell Follow-ups

| 问题 | 影响 | 建议阶段 |
| --- | --- | --- |
| `src/blueprint/controller.ts` 仍然较大 | Visual Plan 若继续加入 controller，可能增加 identity、stale、pagination 与并发 epoch 的认知负担 | Visual Plan MVP 明确需求后，再决定是否需要独立 Phase 3 |
| `scripts/blueprint_server/routes_blueprint.py` 尚未按 Visual Plan 路由拆分 | 当前没有新路由需求，提前拆分只会扩大回归面 | Visual Plan API 阶段按真实端点边界处理 |
| `src/styles.css` 仍是单文件 | 文件较大，但当前没有样式职责或界面行为问题 | 仅在后续 UI 设计变更需要独立样式边界时处理 |
| 本次模块边界使 Vite 主 JS 从约 46.53 KB gzip 增至约 48.29 KB gzip | 当前增加约 1.76 KB，未观察到运行时回归，但后续功能会继续使用下载预算 | Visual Plan 完成后统一做 bundle/performance 评估 |
