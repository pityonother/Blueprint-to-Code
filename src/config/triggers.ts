import type { TriggerConfig } from '../types';

export const WORLD_WIDTH = 8000;
export const GROUND_Y = 610;
export const PLAYER_START_X = 200;
export const PLAYER_START_Y = 560;
export const WALK_SPEED = 110;

export const TRIGGERS: TriggerConfig[] = [
  {
    id: 'start_junction',
    x: 300,
    zoneWidth: 1,
    lines: ['这条路我走了好多年了。', '今天好像特别安静。'],
    autoTrigger: true,
    delayMs: 2000,
  },
  {
    id: 'cherry_tree',
    x: 1500,
    zoneWidth: 100,
    lines: ['樱花又开了。', '她站在这棵树下的时候，头发也被风吹起来过。'],
    advanceWarmthTo: 2,
  },
  {
    id: 'old_shop',
    x: 2700,
    zoneWidth: 100,
    lines: ['这家店还在。', '她喜欢这里的橘子汽水，说便宜，但好喝。'],
  },
  {
    id: 'bench_with_cat',
    x: 3900,
    zoneWidth: 100,
    lines: ['那只猫不是同一只了吧。', '我们在这张椅子上坐过，当时什么话都不用说。'],
  },
  {
    id: 'sunset_wall',
    x: 5300,
    zoneWidth: 120,
    lines: [
      '她说过，这样的天光像是会把人融化。',
      '那时候我不明白。',
      '现在好像明白了一点。',
    ],
    advanceWarmthTo: 3,
  },
  {
    id: 'mailbox_downstairs',
    x: 6700,
    zoneWidth: 100,
    lines: ['快到家了。', '她现在，应该也在回自己家的路上吧。'],
  },
  {
    id: 'door',
    x: 7800,
    zoneWidth: 1,
    lines: ['该进去了。'],
    autoTrigger: true,
  },
];
