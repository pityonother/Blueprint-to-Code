import type { ApiResult } from '../shared/api';


export type ReportKey =
  | 'agent_index'
  | 'next_actions'
  | 'notes_todo'
  | 'behavior_summary'
  | 'context_review'
  | 'asset_memory_card'
  | 'context_pack'
  | 'formula_candidates'
  | 'formula_candidates_json'
  | 'unresolved_formulas'
  | 'capture_quality_report'
  | 'diagnostics_report'
  | 'asset_report'
  | 'call_graph_summary'
  | 'uasset_graph_read_report'
  | 'uasset_property_parse_report'
  | 'uasset_link_resolution_report'
  | 'uasset_partial_graph_triage'
  | 'uasset_quality_gates'
  | 'uasset_vs_clipboard_compare'
  | 'uasset_class_defaults_report'
  | 'uasset_structure_report';

export type OpenTarget =
  | ReportKey
  | 'asset_folder'
  | 'output_folder'
  | 'graph_reports'
  | 'notes'
  | 'defaults'
  | 'components'
  | 'devkit_report';

export type ReportMap = Record<string, boolean>;

export interface ExportQuality {
  status: string;
  hasLog: boolean;
  hasReport: boolean;
  warnings: number;
  errors: number;
  skipped: number;
  skippedAttempts: number;
  debugMessages: number;
  safeScsComponentCount: number;
  manualOrRestoredComponentCount: number;
  summary: string;
  reportCounts: Record<string, number>;
  componentSourceCounts: Record<string, number>;
}

export interface AssetSummary {
  name: string;
  path: string;
  graphs: number;
  hasGraphQueue: boolean;
  graphQueueCount: number;
  graphQueueCompactCount: number;
  graphQueueRecommendedCount: number;
  graphQueueOptionalCount: number;
  graphQueueDeferredCount: number;
  graphQueueFocusedCount: number;
  hasGraphCandidates: boolean;
  graphCandidateCount: number;
  hasUassetStructure: boolean;
  uassetEdGraphCount: number;
  uassetFunctionGraphCount: number;
  uassetCollapsedGraphCount: number;
  uassetStandaloneGraphCount: number;
  uassetFunctionCount: number;
  hasUassetGraphRead: boolean;
  hasEvidenceStore?: boolean;
  evidenceRevision?: string;
  preservedLegacyReports?: boolean;
  uassetReadGraphCount: number;
  uassetReadNodeCount: number;
  uassetReadPinCount: number;
  uassetReadLinkCount: number;
  uassetReadCompleteCount: number;
  uassetReadPartialCount: number;
  uassetReadNeedsClipboardCount: number;
  hasDefaults: boolean;
  defaultsCount: number;
  hasComponents: boolean;
  componentsCount: number;
  hasNotes: boolean;
  hasOutput: boolean;
  lastOutputAt: string;
  reports: ReportMap;
  formulaCandidateCount: number;
  unresolvedFormulaCount: number;
  assetMemoryCardExists: boolean;
  contextPackExists: boolean;
  exportQuality: ExportQuality;
}

export interface KnowledgeBaseSummary {
  exists: boolean;
  root: string;
  indexPath: string;
  reportPath: string;
  reportExists: boolean;
  globalReportPath: string;
  globalReportExists: boolean;
  priorityReportPath: string;
  priorityReportExists: boolean;
  priorityResultsPath: string;
  priorityResultsExists: boolean;
  priorityQueuePath: string;
  priorityQueueExists: boolean;
  generated: string;
  focus: string;
  assetCount: number;
  systemCount: number;
  globalAssetCount: number;
  capturedAssetCount: number;
}

export interface AppState extends ApiResult {
  ok: boolean;
  version: string;
  projectRoot: string;
  captureRoot: string;
  assets: AssetSummary[];
  knowledgeBase: KnowledgeBaseSummary;
  devkitRequestPath: string;
  devkitAssetPath: string;
  devkitPythonCommand: string;
  devkitOutputLogCommand: string;
}

export interface JobInfo {
  id: string;
  kind: string;
  title: string;
  status: 'queued' | 'running' | 'succeeded' | 'failed' | 'cancelled' | 'timed_out';
  stdout: string;
  stderr: string;
  returnCode: number | null;
  durationSeconds: number;
  error?: string;
  result?: Record<string, unknown>;
}

export interface MissingFunctionItem {
  function: string;
  sourceGraphs: string[];
  areas: string[];
  suggested: string;
}

export interface GraphQueueItem {
  name: string;
  type: string;
  line: string;
  tier: 'recommended' | 'optional' | 'deferred';
  reason: string;
}

export interface GraphQueueSummary {
  total: number;
  compact: number;
  recommended: number;
  optional: number;
  deferred: number;
  focused: number;
  items: GraphQueueItem[];
}

export interface CaptureQueueItem {
  name: string;
  type: string;
  raw: string;
}

export type MainNoticeTone = 'info' | 'good' | 'warn' | 'danger';
export type ReportLevel = 'compact' | 'standard' | 'debug';
export type KnowledgeOpenTarget =
  | 'report'
  | 'folder'
  | 'index'
  | 'global_report'
  | 'priority_report'
  | 'priority_results';
