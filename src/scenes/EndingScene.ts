import * as Phaser from 'phaser';
import { GAME_HEIGHT, GAME_WIDTH } from '../config/gameConfig';

export class EndingScene extends Phaser.Scene {
  constructor() {
    super('EndingScene');
  }

  create(): void {
    this.cameras.main.fadeIn(600, 18, 11, 8);

    this.add.rectangle(0, 0, GAME_WIDTH, GAME_HEIGHT, 0x2b1d16).setOrigin(0, 0);
    this.add.rectangle(0, 520, GAME_WIDTH, 200, 0x2a1f1a).setOrigin(0, 0);
    const doorFrame = this.add.rectangle(980, 286, 180, 308, 0x3d2b23).setOrigin(0.5, 0.5);
    const door = this.add.rectangle(980, 320, 126, 238, 0x1f1613).setOrigin(0.5, 0.5);
    const handle = this.add.circle(1025, 322, 5, 0xf5c97a);
    const silhouette = this.add.container(860, 558, [
      this.add.rectangle(-10, -28, 18, 64, 0x201612),
      this.add.rectangle(-14, 28, 9, 36, 0x201612),
      this.add.rectangle(2, 28, 9, 36, 0x201612),
      this.add.rectangle(16, -34, 7, 34, 0x201612).setOrigin(0.5, 0),
      this.add.circle(-10, -74, 13, 0x201612),
    ]);

    const camera = this.cameras.main;
    camera.setZoom(1.05);
    this.tweens.add({
      targets: silhouette.list[3] as Phaser.GameObjects.Rectangle,
      angle: -54,
      x: 28,
      y: -42,
      duration: 800,
      ease: 'Sine.Out',
    });
    this.tweens.add({
      targets: camera,
      zoom: 0.95,
      duration: 2000,
      ease: 'Sine.InOut',
    });

    this.time.delayedCall(850, () => {
      const line = this.add
        .text(GAME_WIDTH / 2, 210, '樱花会再开的。', {
          fontFamily: '"Noto Serif SC", "Songti SC", "SimSun", serif',
          fontSize: '40px',
          color: '#fff7e6',
        })
        .setOrigin(0.5)
        .setAlpha(0);

      this.tweens.add({
        targets: line,
        alpha: 1,
        duration: 1000,
        hold: 3000,
        yoyo: true,
        ease: 'Sine.InOut',
      });
    });

    this.time.delayedCall(5800, () => {
      const fade = this.add.rectangle(0, 0, GAME_WIDTH, GAME_HEIGHT, 0x000000).setOrigin(0, 0).setAlpha(0);
      this.tweens.add({
        targets: fade,
        alpha: 1,
        duration: 1500,
        onComplete: () => {
          this.add
            .text(GAME_WIDTH / 2, GAME_HEIGHT / 2 - 16, 'THE END', {
              fontFamily: '"Georgia", serif',
              fontSize: '34px',
              color: '#fff7e6',
            })
            .setOrigin(0.5);
          this.add
            .text(GAME_WIDTH / 2, GAME_HEIGHT / 2 + 58, '按任意键重新开始', {
              fontFamily: '"Noto Serif SC", "Songti SC", "SimSun", serif',
              fontSize: '24px',
              color: '#d8c6ab',
            })
            .setOrigin(0.5);

          this.input.keyboard?.once('keydown', () => this.scene.start('TitleScene'));
          this.input.once('pointerdown', () => this.scene.start('TitleScene'));
        },
      });
    });

    void doorFrame;
    void door;
    void handle;
  }
}
