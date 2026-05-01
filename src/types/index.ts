export type WarmthLevel = 1 | 2 | 3;

export interface TriggerConfig {
  id: string;
  x: number;
  zoneWidth: number;
  lines: string[];
  advanceWarmthTo?: WarmthLevel;
  autoTrigger?: boolean;
  delayMs?: number;
}

export interface WarmthParams {
  skyTint: number;
  overlayColor: number;
  overlayAlpha: number;
  parallaxSpeedMul: number;
  petalDensityMul: number;
  vignetteAlpha: number;
  playerAnimSpeed: number;
}
