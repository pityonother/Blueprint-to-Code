import * as Phaser from 'phaser';
import { GAME_HEIGHT, GAME_WIDTH } from '../config/gameConfig';

export class TextBox {
  private readonly container: Phaser.GameObjects.Container;
  private readonly bg: Phaser.GameObjects.Image;
  private readonly text: Phaser.GameObjects.Text;
  private resolver?: () => void;
  private closing = false;

  constructor(private scene: Phaser.Scene) {
    const width = GAME_WIDTH * 0.8;
    const height = 112;
    const x = (GAME_WIDTH - width) / 2;
    const y = GAME_HEIGHT - height - 40;

    this.bg = scene.add.image(0, 0, 'textbox-bg').setOrigin(0, 0).setDisplaySize(width, height);
    this.bg.setAlpha(0.88);
    this.text = scene.add.text(32, 20, '', {
      fontFamily: '"Noto Serif SC", "Songti SC", "SimSun", serif',
      fontSize: '27px',
      color: '#fff7e6',
      wordWrap: { width: width - 64 },
      lineSpacing: 10,
      align: 'left',
    });
    this.container = scene.add.container(x, y, [this.bg, this.text]).setScrollFactor(0).setDepth(1000).setAlpha(0);
  }

  async showLines(lines: string[]): Promise<void> {
    for (const line of lines) {
      await this.showOne(line);
    }
  }

  destroy(): void {
    this.detachAdvanceListeners();
    this.container.destroy(true);
  }

  private showOne(line: string): Promise<void> {
    return new Promise((resolve) => {
      this.closing = false;
      this.text.setText(line);
      this.scene.tweens.add({ targets: this.container, alpha: 1, duration: 600, ease: 'Sine.Out' });

      this.resolver = () => {
        if (this.closing) {
          return;
        }
        this.closing = true;
        this.detachAdvanceListeners();
        this.scene.tweens.add({
          targets: this.container,
          alpha: 0,
          duration: 400,
          ease: 'Sine.In',
          onComplete: () => resolve(),
        });
      };

      this.scene.input.keyboard?.once('keydown-SPACE', this.resolver);
      this.scene.input.keyboard?.once('keydown-E', this.resolver);
      this.scene.input.once('pointerdown', this.resolver);
    });
  }

  private detachAdvanceListeners(): void {
    if (!this.resolver) {
      return;
    }
    this.scene.input.keyboard?.off('keydown-SPACE', this.resolver);
    this.scene.input.keyboard?.off('keydown-E', this.resolver);
    this.scene.input.off('pointerdown', this.resolver);
    this.resolver = undefined;
  }
}
