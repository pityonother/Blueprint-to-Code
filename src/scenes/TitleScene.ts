import * as Phaser from 'phaser';
import { GAME_HEIGHT, GAME_WIDTH } from '../config/gameConfig';

export class TitleScene extends Phaser.Scene {
  constructor() {
    super('TitleScene');
  }

  create(): void {
    const bg = this.add.rectangle(0, 0, GAME_WIDTH, GAME_HEIGHT, 0x1f1510).setOrigin(0, 0);
    const glow = this.add.rectangle(GAME_WIDTH / 2, GAME_HEIGHT / 2, GAME_WIDTH, GAME_HEIGHT, 0xe8813a, 0.12);
    glow.setBlendMode(Phaser.BlendModes.SCREEN);
    this.tweens.add({
      targets: glow,
      alpha: { from: 0.08, to: 0.18 },
      duration: 2200,
      repeat: -1,
      yoyo: true,
    });

    this.add
      .text(GAME_WIDTH / 2, 230, '《回家的路》', {
        fontFamily: '"Noto Serif SC", "Songti SC", "SimSun", serif',
        fontSize: '58px',
        color: '#fff7e6',
      })
      .setOrigin(0.5);

    this.add
      .text(GAME_WIDTH / 2, 314, 'Petal Walk', {
        fontFamily: '"Georgia", serif',
        fontSize: '24px',
        color: '#f5c97a',
        letterSpacing: 2,
      })
      .setOrigin(0.5);

    this.add
      .text(
        GAME_WIDTH / 2,
        430,
        '走在回家的路上，落日和樱花让那段初恋轻轻浮上来。',
        {
          fontFamily: '"Noto Serif SC", "Songti SC", "SimSun", serif',
          fontSize: '24px',
          color: '#f7e4c8',
        },
      )
      .setOrigin(0.5);

    const prompt = this.add
      .text(GAME_WIDTH / 2, 570, '按任意键开始', {
        fontFamily: '"Noto Serif SC", "Songti SC", "SimSun", serif',
        fontSize: '28px',
        color: '#fff7e6',
      })
      .setOrigin(0.5);

    this.tweens.add({
      targets: prompt,
      alpha: { from: 0.3, to: 1 },
      duration: 1200,
      yoyo: true,
      repeat: -1,
    });

    this.input.keyboard?.once('keydown', () => this.scene.start('WalkScene'));
    this.input.once('pointerdown', () => this.scene.start('WalkScene'));
    bg.setInteractive();
  }
}
