import type { CaptureQueueItem } from './types';


export const graphTypes = [
  'EventGraph',
  'Function',
  'Macro',
  'ConstructionScript',
  'Unknown',
];


export function graphTypeLabel(type: string): string {
  if (type === 'EventGraph') {
    return '事件图';
  }
  if (type === 'Function') {
    return '函数';
  }
  if (type === 'Macro') {
    return '宏';
  }
  if (type === 'ConstructionScript') {
    return 'Construction Script';
  }
  return '未知';
}


export function graphQueueTierLabel(tier: string): string {
  if (tier === 'recommended') {
    return '推荐采集';
  }
  if (tier === 'optional') {
    return '可选采集';
  }
  if (tier === 'deferred') {
    return '暂不采集';
  }
  return '未分类';
}


export function graphQueueModeLabel(mode: string): string {
  if (mode === 'compact') {
    return '精简采集队列';
  }
  if (mode === 'recommended') {
    return '推荐分页';
  }
  if (mode === 'focused') {
    return '推荐+可选分页';
  }
  if (mode === 'all') {
    return '全部分页';
  }
  return '分页队列';
}


export function normalizeGraphType(value: string): string {
  const text = value.trim().toLowerCase();
  if (!text) {
    return 'Unknown';
  }
  if (text === 'eventgraph' || text === 'event graph' || text === 'event' || text === '事件图') {
    return 'EventGraph';
  }
  if (text === 'function' || text === 'func' || text === '函数') {
    return 'Function';
  }
  if (text === 'macro' || text === '宏') {
    return 'Macro';
  }
  if (text === 'constructionscript' || text === 'construction script' || text === 'construction' || text === '构造脚本') {
    return 'ConstructionScript';
  }
  return graphTypes.includes(value.trim()) ? value.trim() : 'Unknown';
}


export function parseCaptureQueue(text: string): CaptureQueueItem[] {
  return text
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter((line) => line && !line.startsWith('#'))
    .map((line) => {
      let name = line;
      let type = 'Unknown';
      const pipeParts = line.split('|').map((part) => part.trim()).filter(Boolean);
      const tabParts = line.split(/\t|,/).map((part) => part.trim()).filter(Boolean);
      if (pipeParts.length >= 2) {
        name = pipeParts[0];
        type = normalizeGraphType(pipeParts[1]);
      } else if (tabParts.length >= 2 && normalizeGraphType(tabParts[tabParts.length - 1]) !== 'Unknown') {
        name = tabParts.slice(0, -1).join(' ');
        type = normalizeGraphType(tabParts[tabParts.length - 1]);
      }
      return { name: name.trim(), type, raw: line };
    })
    .filter((item) => item.name);
}
