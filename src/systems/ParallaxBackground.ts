import * as Phaser from 'phaser';
import { GAME_HEIGHT, GAME_WIDTH } from '../config/gameConfig';

export class ParallaxBackground {
  private readonly sky: Phaser.GameObjects.TileSprite;
  private readonly far: Phaser.GameObjects.TileSprite;
  private readonly mid: Phaser.GameObjects.TileSprite;
  private readonly near: Phaser.GameObjects.TileSprite;
  private readonly ground: Phaser.GameObjects.Rectangle;

  constructor(scene: Phaser.Scene) {
    this.sky = scene.add.tileSprite(0, 0, GAME_WIDTH, GAME_HEIGHT, 'bg-sky').setOrigin(0, 0).setScrollFactor(0);
    this.far = scene.add.tileSprite(0, 248, GAME_WIDTH, 260, 'bg-far').setOrigin(0, 0).setScrollFactor(0).setAlpha(0.88);
    this.mid = scene.add.tileSprite(0, 292, GAME_WIDTH, 280, 'bg-mid').setOrigin(0, 0).setScrollFactor(0);
    this.near = scene.add.tileSprite(0, 408, GAME_WIDTH, 240, 'bg-near').setOrigin(0, 0).setScrollFactor(0);
    this.ground = scene.add.rectangle(0, 580, GAME_WIDTH, 140, 0x3a2b24).setOrigin(0, 0).setScrollFactor(0);

    this.sky.setDepth(-40);
    this.far.setDepth(-30);
    this.mid.setDepth(-20);
    this.near.setDepth(-10);
    this.ground.setDepth(30);
  }

  update(scrollX: number, speedMul: number, skyTint: number): void {
    this.sky.tilePositionX = scrollX * 0.03 * speedMul;
    this.far.tilePositionX = scrollX * 0.14 * speedMul;
    this.mid.tilePositionX = scrollX * 0.28 * speedMul;
    this.near.tilePositionX = scrollX * 0.45 * speedMul;

    this.sky.setTint(skyTint);
    this.far.setTint(Phaser.Display.Color.GetColor(117, 90, 92));
    this.mid.setTint(Phaser.Display.Color.GetColor(86, 63, 67));
    this.near.setTint(Phaser.Display.Color.GetColor(58, 42, 36));
  }
}
