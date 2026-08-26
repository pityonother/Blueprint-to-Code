import { escapeHtml } from '../../shared/html';
import type {
  AppState,
  AssetSummary,
  MainNoticeTone,
  ReportKey,
} from '../types';
import {
  actionButton,
  reportButton,
  reportTargets,
  renderReportPreview,
} from './common';


export interface WorkflowViewModel {
  activeJobId: string;
  activeJobLabel: string;
  asset?: AssetSummary;
  busy: boolean;
  devkitInput: string;
  mainNotice: string;
  mainNoticeTone: MainNoticeTone;
  nativeClassResult: NativeClassReadResult | null;
  reportContent: string;
  reportLoading: boolean;
  reportPath: string;
  selectedReport: ReportKey;
  state: AppState | null;
  typedAssetName: string;
  typedNativeClass: boolean;
  typedPathRecognized: boolean;
}


export interface NativeClassPropertyResult {
  requestedName: string;
  readName: string;
  pythonAttribute: string;
  ownerClass: string;
  descriptor: string;
  readable: boolean;
  value: unknown;
  valueType: string;
  scope: 'CLASS_DEFAULT_OBJECT';
}


export interface NativeClassFunctionResult {
  name: string;
  pythonAttribute: string;
  ownerClass: string;
  available: boolean;
  called: false;
}


export interface NativeClassReadResult {
  sourceKind: 'native_class_reflection';
  assetPath: string;
  className: string;
  classLoaded: boolean;
  classDefaultObjectRead: boolean;
  inheritance: string[];
  engineVersion: string;
  generatedAt: string;
  readOnly: true;
  properties: NativeClassPropertyResult[];
  functions: NativeClassFunctionResult[];
  runtimeStateAvailable: false;
  runtimeStateReason: 'CLASS_DEFAULT_OBJECT_ONLY';
  runtimeStateExplanation: string;
}


function readStatusBadge(view: WorkflowViewModel): string {
  const { asset } = view;
  if (view.typedNativeClass) {
    return '<span class="status-line muted">已识别为 /Script 原生类；将启动本机 ARK DevKit 做一次只读反射，不会查找 .uasset。</span>';
  }
  if (!asset) {
    if (view.typedPathRecognized) {
      return '<span class="status-line muted">路径格式已识别，但这个资产还没有读取进历史列表。点下面绿色按钮开始读取。</span>';
    }
    return '<span class="status-line muted">未识别。粘贴 /Game/... Object Path、/Script/模块.类名，或 mod 相对路径。</span>';
  }
  if (!asset.hasUassetGraphRead) {
    return '<span class="status-line muted">这个资产还没有从 .uasset 读取过。点下面的“从 .uasset 读取图内容”开始。</span>';
  }
  const total = asset.uassetReadGraphCount;
  const complete = asset.uassetReadCompleteCount;
  const partial = asset.uassetReadPartialCount;
  const need = asset.uassetReadNeedsClipboardCount;
  const tone = need > 0 ? 'danger' : partial > 0 ? 'warn' : 'good';
  return `
    <span class="status-line ${tone}">
      已读取 ${total} 个图页 · 完整 ${complete} · 部分 ${partial} · 需手动补 ${need}${asset.lastOutputAt ? ` · 最近分析 ${escapeHtml(asset.lastOutputAt)}` : ''}
    </span>
  `;
}


export function renderStepPath(view: WorkflowViewModel): string {
  const value = view.devkitInput || view.state?.devkitAssetPath || '';
  return `
    <section class="panel step-panel">
      <div class="step-head">
        <span class="step-num">1</span>
        <div class="step-title">
          <h2>粘贴蓝图 Object Path</h2>
          <p class="hint">资产可粘贴 <code>/Game/.../Asset.Asset</code>；原生类可粘贴 <code>/Script/ShooterGame.ShooterCharacter</code>；也支持 <code>Kaminan_server/.../Asset.Asset</code> 这种 mod 相对路径。</p>
        </div>
      </div>
      <textarea id="devkit-path" spellcheck="false" placeholder="/Game/.../Asset.Asset 或 /Script/模块.类名">${escapeHtml(value)}</textarea>
      <div class="status-row">
        <strong>已选资产：</strong>
        <span class="asset-name">${escapeHtml(view.asset?.name || view.typedAssetName || '无')}</span>
        ${readStatusBadge(view)}
      </div>
    </section>
  `;
}


export function renderStepActions(view: WorkflowViewModel): string {
  const readPath = view.devkitInput || view.state?.devkitAssetPath || '';
  const canRead = !view.busy && Boolean(readPath.trim());
  const canAnalyze = !view.busy && Boolean(view.asset && view.asset.graphs);
  const primaryTitle = view.typedNativeClass ? '读取原生类属性' : '从 .uasset 读取图内容';
  const primaryHint = view.typedNativeClass
    ? '通过官方 PythonScriptCommandlet 只读反射类默认对象；不会修改 DevKit 资产。'
    : '解析 .uasset / .uexp，默认生成低 token 证据库和 AI 索引。';
  const sectionHint = view.typedNativeClass
    ? '原生类没有对应 .uasset。本次只读取类、继承、属性默认值和函数是否存在。'
    : '第一次操作只需要点左边绿色按钮，生成当前 revision 的 Evidence Store 和 AI 索引。右边只在需要人类长报告时使用。';
  return `
    <section class="panel step-panel">
      <div class="step-head">
        <span class="step-num">2</span>
        <div class="step-title">
          <h2>读取并生成报告</h2>
          <p class="hint">${sectionHint}</p>
        </div>
      </div>
      <div class="big-action-row">
        <button class="big-btn primary" data-action="read-uasset-graphs" ${canRead ? '' : 'disabled'}>
          <strong>${primaryTitle}</strong>
          <small>${primaryHint}</small>
        </button>
        <button class="big-btn secondary" data-action="analyze-standard" ${canAnalyze ? '' : 'disabled'}>
          <strong>生成 / 刷新人类报告</strong>
          <small>按同一 Object Path 以 dual 模式重读，再生成匹配当前 revision 的 asset_report 等。</small>
        </button>
      </div>
      ${
        view.activeJobId
          ? `<div class="job-bar"><span>正在后台执行：${escapeHtml(view.activeJobLabel || '任务')}……可以等它跑完，也可以取消。</span>${actionButton('取消任务', 'cancel-job', 'danger')}</div>`
          : ''
      }
      ${view.mainNotice ? `<div class="action-notice ${view.mainNoticeTone}">${escapeHtml(view.mainNotice)}</div>` : ''}
    </section>
  `;
}


function nativeValue(value: unknown): string {
  if (typeof value === 'string') return value;
  if (value === undefined) return '';
  return JSON.stringify(value);
}


function renderNativeClassResult(result: NativeClassReadResult): string {
  const propertyRows = result.properties
    .map(
      (property) => `
        <tr>
          <td><code>${escapeHtml(property.requestedName)}</code></td>
          <td>${property.readable ? '可读' : '不可读'}</td>
          <td><code>${escapeHtml(nativeValue(property.value))}</code></td>
          <td>${escapeHtml(property.ownerClass || '未知')}</td>
          <td>类默认对象</td>
        </tr>
      `,
    )
    .join('');
  const availableFunctions = result.functions
    .filter((item) => item.available)
    .map((item) => item.name)
    .join('、');
  return `
    <section class="panel step-panel">
      <div class="step-head">
        <span class="step-num">3</span>
        <div class="step-title">
          <h2>原生类反射结果</h2>
          <p class="hint"><code>${escapeHtml(result.assetPath)}</code> · 引擎 ${escapeHtml(result.engineVersion || '未知')}</p>
        </div>
      </div>
      <div class="action-notice warn"><strong>边界：</strong>当前显示的是类默认对象，不是在线玩家实时值；要监测实际下蹲，仍需在游戏或 PIE 中取得玩家实例。</div>
      <div class="table-wrap">
        <table>
          <thead><tr><th>属性</th><th>状态</th><th>默认值</th><th>声明类</th><th>范围</th></tr></thead>
          <tbody>${propertyRows || '<tr><td colspan="5">没有读到目标属性。</td></tr>'}</tbody>
        </table>
      </div>
      <p class="hint">继承链：${escapeHtml(result.inheritance.join(' → ') || '未知')}</p>
      <p class="hint">可用下蹲函数：${escapeHtml(availableFunctions || '未发现')}</p>
    </section>
  `;
}


export function renderStepResult(view: WorkflowViewModel): string {
  const { asset, nativeClassResult } = view;
  if (nativeClassResult) return renderNativeClassResult(nativeClassResult);
  if (view.typedNativeClass) {
    return `
      <section class="panel step-panel">
        <div class="step-head">
          <span class="step-num">3</span>
          <div class="step-title">
            <h2>原生类反射结果</h2>
            <p class="hint">点击上面的“读取原生类属性”后，这里会显示类默认对象中的属性值与继承来源。</p>
          </div>
        </div>
        <div class="empty-state">尚未读取这个原生类。</div>
      </section>
    `;
  }
  if (!asset || !asset.hasUassetGraphRead) {
    return `
      <section class="panel step-panel">
        <div class="step-head">
          <span class="step-num">3</span>
          <div class="step-title">
            <h2>读取结果</h2>
            <p class="hint">点完上面那个按钮以后，这里会出现：读取了多少图页，多少完整、多少部分、多少需要手动补。</p>
          </div>
        </div>
        <div class="empty-state">还没有读取过这个资产。</div>
      </section>
    `;
  }
  const partial = asset.uassetReadPartialCount;
  const need = asset.uassetReadNeedsClipboardCount;
  return `
    <section class="panel step-panel">
      <div class="step-head">
        <span class="step-num">3</span>
        <div class="step-title">
          <h2>读取结果</h2>
          <p class="hint">这是工具从 .uasset 文件里成功还原出多少图页的统计。</p>
        </div>
      </div>
      <div class="result-grid">
        <div class="result-tile good">
          <span class="tile-num">${asset.uassetReadCompleteCount}</span>
          <strong>已完整读取</strong>
          <small>节点、连线、属性都还原成功，可以直接信任报告里这部分内容。</small>
        </div>
        <div class="result-tile ${partial ? 'warn' : 'idle'}">
          <span class="tile-num">${partial}</span>
          <strong>部分读取</strong>
          <small>能看，但有些连线或字段是工具猜出来的（启发式）。报告里相关说明仅供参考，必要时再补采。</small>
        </div>
        <div class="result-tile ${need ? 'danger' : 'idle'}">
          <span class="tile-num">${need}</span>
          <strong>需要手动补充</strong>
          <small>这些图页 .uasset 解析失败，需要回 DevKit 里复制粘贴。下方会自动出现“补采”面板。</small>
        </div>
      </div>
      <div class="result-meta">共 ${asset.uassetReadGraphCount} 个图页 · 节点 ${asset.uassetReadNodeCount} · pin ${asset.uassetReadPinCount} · 连线 ${asset.uassetReadLinkCount}</div>
    </section>
  `;
}


function reportTile(
  key: ReportKey,
  title: string,
  hint: string,
  asset?: AssetSummary,
  missingHint = '尚未生成 — 先完成第 2 步“读取图内容”。',
): string {
  const exists = Boolean(asset?.reports?.[key]);
  return `
    <button class="report-tile ${exists ? '' : 'missing'}" data-action="open-report-${key}" ${exists ? '' : 'disabled'}>
      <strong>${escapeHtml(title)}</strong>
      <small>${escapeHtml(exists ? hint : missingHint)}</small>
    </button>
  `;
}


export function renderStepReports(view: WorkflowViewModel): string {
  const { asset } = view;
  const legacyHint = asset?.preservedLegacyReports
    ? '这是保留的 legacy 文件，可能早于当前 evidence revision；需要最新人类报告时请点“生成 / 刷新人类报告”。'
    : 'legacy 人类报告；indexed 默认不生成，需要时请显式重新分析。';
  const legacyMissing = 'indexed 默认只生成 AI 证据索引；需要这份人类报告时请点“生成 / 刷新人类报告”。';
  const tiles = [
    reportTile('agent_index', 'AI 证据索引 (agent_index)', `默认给 AI 的低 token 入口；按 Evidence ID 搜索和下钻，revision ${asset?.evidenceRevision || '-'}。`, asset),
    reportTile('context_pack', '问题上下文包 (context_pack)', `默认给 GPT 的小上下文，候选 ${asset?.formulaCandidateCount || 0} 个，未解析 ${asset?.unresolvedFormulaCount || 0} 个。`, asset),
    reportTile('asset_memory_card', '资产小卡片 (asset_memory_card)', '几 KB 级资产记忆卡，只保留身份、摘要、关键默认值和证据指针。', asset),
    reportTile('formula_candidates', '公式候选 (formula_candidates)', '概率、属性、XP、掉落、Buff 等机制候选；不会写成最终公式。', asset),
    reportTile('unresolved_formulas', 'unresolved formulas', '查看 native、父类、heuristic 连线等公式阻塞原因和下一步验证。', asset),
    reportTile('asset_report', '完整报告（历史/按需报告）', legacyHint, asset, legacyMissing),
    reportTile('behavior_summary', '行为说明（历史/按需报告）', legacyHint, asset, legacyMissing),
    reportTile('diagnostics_report', '诊断报告（历史/按需报告）', legacyHint, asset, legacyMissing),
    reportTile('call_graph_summary', '调用关系摘要（历史/按需报告）', legacyHint, asset, legacyMissing),
  ].join('');
  const allTabs = reportTargets
    .map((key) => reportButton(key, view.selectedReport, asset))
    .join('');
  return `
    <section class="panel step-panel">
      <div class="step-head">
        <span class="step-num">4</span>
        <div class="step-title">
          <h2>打开索引 / 按需报告</h2>
          <p class="hint">AI 默认读当前 revision 的证据索引；保留的 legacy Markdown 可能来自旧 revision，卡片会明确标注。</p>
        </div>
      </div>
      <div class="report-tile-grid">${tiles}</div>
      <details class="more-reports">
        <summary>查看预览 / 更多分项报告</summary>
        <div class="report-tabs">${allTabs}</div>
        ${renderReportPreview(
          asset,
          view.selectedReport,
          view.reportLoading,
          view.reportPath,
          view.reportContent,
        )}
        <div class="button-row tight">
          ${actionButton('在编辑器里打开当前预览', 'open-current-report', 'secondary', !asset || !asset.reports[view.selectedReport])}
          ${actionButton('打开 graph_reports 目录', 'open-graph-reports', 'ghost', !asset)}
          ${actionButton('打开输出目录', 'open-output', 'ghost', !asset || !asset.hasOutput)}
        </div>
      </details>
    </section>
  `;
}
