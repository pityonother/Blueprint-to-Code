import type { WarmthParams } from '../types';

export const WARMTH: Record<1 | 2 | 3, WarmthParams> = {
  1: {
    skyTint: 0xfff0d0,
    overlayColor: 0xffa44a,
    overlayAlpha: 0.08,
    parallaxSpeedMul: 1,
    petalDensityMul: 0.5,
    vignetteAlpha: 0.05,
    playerAnimSpeed: 1,
  },
  2: {
    skyTint: 0xffcc88,
    overlayColor: 0xff7a2e,
    overlayAlpha: 0.2,
    parallaxSpeedMul: 0.85,
    petalDensityMul: 1,
    vignetteAlpha: 0.15,
    playerAnimSpeed: 0.95,
  },
  3: {
    skyTint: 0xff9966,
    overlayColor: 0xd94a1a,
    overlayAlpha: 0.32,
    parallaxSpeedMul: 0.7,
    petalDensityMul: 1.4,
    vignetteAlpha: 0.28,
    playerAnimSpeed: 0.9,
  },
};

export const WARMTH_TWEEN_DURATION = 3000;
