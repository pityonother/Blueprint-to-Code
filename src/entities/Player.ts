import * as Phaser from 'phaser';
import { WALK_SPEED } from '../config/triggers';

export class Player {
  public readonly sprite: Phaser.GameObjects.Sprite;
  private readonly bounceTween: Phaser.Tweens.Tween;
  private readonly baseY: number;

  constructor(scene: Phaser.Scene, x: number, y: number) {
    this.baseY = y;
    this.sprite = scene.add.sprite(x, y, 'player', 0);
    this.sprite.setOrigin(0.5, 1);
    this.sprite.setDepth(320);

    this.bounceTween = scene.tweens.add({
      targets: this.sprite,
      y: y - 3,
      duration: 420,
      ease: 'Sine.InOut',
      yoyo: true,
      repeat: -1,
      paused: true,
    });
  }

  get x(): number {
    return this.sprite.x;
  }

  setAnimationSpeed(multiplier: number): void {
    this.bounceTween.timeScale = multiplier;
    this.sprite.anims.msPerFrame = 1000 / (6 * multiplier);
  }

  walk(delta: number): void {
    this.sprite.x += (WALK_SPEED * delta) / 1000;
    if (!this.sprite.anims.isPlaying) {
      this.sprite.play('player-walk');
    }
    if (!this.bounceTween.isPlaying()) {
      this.bounceTween.resume();
    }
  }

  idle(): void {
    this.bounceTween.pause();
    this.sprite.anims.stop();
    this.resetPose();
  }

  stop(): void {
    this.idle();
  }

  async raiseHand(): Promise<void> {
    this.stop();
    this.sprite.setFrame(0);
    await new Promise<void>((resolve) => {
      this.sprite.scene.tweens.add({
        targets: this.sprite,
        angle: -8,
        y: this.baseY - 2,
        duration: 800,
        ease: 'Sine.Out',
        onComplete: () => resolve(),
      });
    });
  }

  destroy(): void {
    this.bounceTween.destroy();
    this.sprite.destroy();
  }

  private resetPose(): void {
    this.sprite.y = this.baseY;
    this.sprite.angle = 0;
    this.sprite.setFrame(0);
  }
}
