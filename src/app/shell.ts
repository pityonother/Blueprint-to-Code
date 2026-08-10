import type { WorkspaceView } from './router';
import { escapeHtml } from '../shared/html';


export interface WorkspaceShellContent {
  body: string;
  mainClass: string;
  topActions?: string;
}


export function renderTopbar(workspaceView: WorkspaceView, topActions = ''): string {
  const titles: Record<WorkspaceView, [string, string]> = {
    blueprint: ['蓝图分析工作台', '从 .uasset 还原 Unreal/ARK Blueprint，再生成中文行为说明。'],
    harvest: ['ARK 资源点采集查询', '资源节点 → HarvestComponent → 产出资源 → 生物排行'],
    knowledge: ['ARK 知识库 vNext', '实体、角色、领域、事实、Evidence 与数据库优先查询计划。'],
  };
  const [title, subtitle] = titles[workspaceView];
  return `
    <header class="topbar">
      <div class="topbar-title">
        <h1>${escapeHtml(title)}</h1>
        <small>${escapeHtml(subtitle)}</small>
      </div>
      <div class="top-actions">
        <nav class="workspace-tabs" aria-label="工作区">
          <button class="workspace-tab ${workspaceView === 'blueprint' ? 'active' : ''}" type="button" data-workspace="blueprint" aria-current="${workspaceView === 'blueprint' ? 'page' : 'false'}">蓝图分析</button>
          <button class="workspace-tab ${workspaceView === 'harvest' ? 'active' : ''}" type="button" data-workspace="harvest" aria-current="${workspaceView === 'harvest' ? 'page' : 'false'}">资源点采集排行</button>
          <button class="workspace-tab ${workspaceView === 'knowledge' ? 'active' : ''}" type="button" data-workspace="knowledge" aria-current="${workspaceView === 'knowledge' ? 'page' : 'false'}">知识库 vNext</button>
        </nav>
        ${topActions}
      </div>
    </header>
  `;
}


export function renderVersionFooter(appVersion: string): string {
  const label = appVersion ? `v${appVersion}` : '版本加载中';
  return `
    <footer class="app-footer" aria-label="应用版本">
      Blueprint to Code ${escapeHtml(label)}
    </footer>
  `;
}


export function renderWorkspaceShell(
  workspaceView: WorkspaceView,
  appVersion: string,
  content: WorkspaceShellContent,
): string {
  return `
    <div class="shell">
      ${renderTopbar(workspaceView, content.topActions)}
      <main class="${content.mainClass}">
        ${content.body}
      </main>
      ${renderVersionFooter(appVersion)}
    </div>
  `;
}


export function renderLoading(): string {
  return `
    <div class="boot-screen">
      <div class="boot-card">
        <p class="eyebrow">Blueprint Tool</p>
        <h1>正在连接本地控制中心</h1>
        <p>如果页面停在这里，请先运行 <code>scripts\\launch_blueprint_tool.ps1</code>。</p>
      </div>
    </div>
  `;
}
