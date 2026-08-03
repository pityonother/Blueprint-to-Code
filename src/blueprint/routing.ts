import type { BlueprintPrimaryTab } from './types';


export interface BlueprintTabDefinition {
  id: BlueprintPrimaryTab;
  label: string;
  description: string;
}

export const BLUEPRINT_TABS: readonly BlueprintTabDefinition[] = [
  {
    id: 'interpretation',
    label: 'Interpretation',
    description: '证据约束的语句与覆盖率',
  },
  {
    id: 'evidence',
    label: 'Evidence',
    description: '语句到 Evidence ref 的追溯',
  },
  {
    id: 'gaps',
    label: 'Gaps',
    description: '未恢复与不可确认的边界',
  },
  {
    id: 'legacy',
    label: 'Legacy',
    description: '保留的历史报告入口',
  },
  {
    id: 'experimental',
    label: 'Experimental',
    description: '采集、重建与调试工具',
  },
] as const;

const TAB_IDS = new Set<BlueprintPrimaryTab>(BLUEPRINT_TABS.map((tab) => tab.id));

export function isBlueprintPrimaryTab(value: unknown): value is BlueprintPrimaryTab {
  return typeof value === 'string' && TAB_IDS.has(value as BlueprintPrimaryTab);
}

export function blueprintPanelId(tab: BlueprintPrimaryTab): string {
  return `blueprint-panel-${tab}`;
}

export function blueprintTabId(tab: BlueprintPrimaryTab): string {
  return `blueprint-tab-${tab}`;
}
