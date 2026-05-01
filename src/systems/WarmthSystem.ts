import * as Phaser from 'phaser';
import { WARMTH, WARMTH_TWEEN_DURATION } from '../config/warmth';
import { GAME_HEIGHT, GAME_WIDTH } from '../config/gameConfig';
import type { WarmthLevel, WarmthParams } from '../types';

export class WarmthSystem {
  public currentLevel: WarmthLevel = 1;
  public currentParams: WarmthParams;

  private readonly overlay: Phaser.GameObjects.Rectangle;
  private readonly vignette: Phaser.GameObjects.Image;

  constructor(private scene: Phaser.Scene) {
    this.currentParams = { ...WARMTH[1] };
    this.overlay = scene.add
      .rectangle(0, 0, GAME_WIDTH, GAME_HEIGHT, WARMTH[1].overlayColor, WARMTH[1].overlayAlpha)
      .setOrigin(0, 0)
      .setScrollFactor(0)
      .setDepth(900);
    this.vignette = scene.add
      .image(GAME_WIDTH / 2, GAME_HEIGHT / 2, 'vignette')
      .setScrollFactor(0)
      .setDepth(910)
      .setAlpha(WARMTH[1].vignetteAlpha);
  }

  setLevel(level: WarmthLevel): void {
    if (this.currentLevel === level) {
      return;
    }

    const start = { ...this.currentParams };
    const target = WARMTH[level];
    this.currentLevel = level;

    this.scene.tweens.addCounter({
      from: 0,
      to: 1,
      duration: WARMTH_TWEEN_DURATION,
      ease: 'Sine.InOut',
      onUpdate: (tween) => {
        const t = tween.getValue() ?? 0;
        const skyColor = Phaser.Display.Color.Interpolate.ColorWithColor(
          Phaser.Display.Color.IntegerToColor(start.skyTint),
          Phaser.Display.Color.IntegerToColor(target.skyTint),
          100,
          t * 100,
        );
        const overlayColor = Phaser.Display.Color.Interpolate.ColorWithColor(
          Phaser.Display.Color.IntegerToColor(start.overlayColor),
          Phaser.Display.Color.IntegerToColor(target.overlayColor),
          100,
          t * 100,
        );

        this.currentParams = {
          skyTint: Phaser.Display.Color.GetColor(skyColor.r, skyColor.g, skyColor.b),
          overlayColor: Phaser.Display.Color.GetColor(overlayColor.r, overlayColor.g, overlayColor.b),
          overlayAlpha: Phaser.Math.Linear(start.overlayAlpha, target.overlayAlpha, t),
          parallaxSpeedMul: Phaser.Math.Linear(start.parallaxSpeedMul, target.parallaxSpeedMul, t),
          petalDensityMul: Phaser.Math.Linear(start.petalDensityMul, target.petalDensityMul, t),
          vignetteAlpha: Phaser.Math.Linear(start.vignetteAlpha, target.vignetteAlpha, t),
          playerAnimSpeed: Phaser.Math.Linear(start.playerAnimSpeed, target.playerAnimSpeed, t),
        };
        this.overlay.fillColor = this.currentParams.overlayColor;
        this.overlay.fillAlpha = this.currentParams.overlayAlpha;
        this.vignette.setAlpha(this.currentParams.vignetteAlpha);
      },
      onComplete: () => {
        this.currentParams = { ...target };
        this.overlay.fillColor = target.overlayColor;
        this.overlay.fillAlpha = target.overlayAlpha;
        this.vignette.setAlpha(target.vignetteAlpha);
      },
    });
  }
}
