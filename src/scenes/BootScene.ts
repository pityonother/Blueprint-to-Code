import * as Phaser from 'phaser';

export class BootScene extends Phaser.Scene {
  constructor() {
    super('BootScene');
  }

  preload(): void {
    this.load.image('bg-sky', '/assets/bg/sky.png');
    this.load.image('bg-far', '/assets/bg/far.png');
    this.load.image('bg-mid', '/assets/bg/mid.png');
    this.load.image('bg-near', '/assets/bg/near.png');

    this.load.spritesheet('player', '/assets/sprites/player.png', {
      frameWidth: 96,
      frameHeight: 96,
    });
    this.load.image('petal', '/assets/sprites/petal.png');
    this.load.image('tree-cherry', '/assets/sprites/tree_cherry.png');
    this.load.image('shop-old', '/assets/sprites/shop_old.png');
    this.load.image('bench-cat', '/assets/sprites/bench_cat.png');
    this.load.image('sunset-wall', '/assets/sprites/sunset_wall.png');
    this.load.image('mailbox', '/assets/sprites/mailbox.png');
    this.load.image('door-home', '/assets/sprites/door_home.png');

    this.load.image('textbox-bg', '/assets/ui/textbox_bg.png');
    this.load.image('vignette', '/assets/ui/vignette.png');
    this.load.image('trigger-glow', '/assets/ui/trigger_glow.png');
  }

  create(): void {
    if (!this.anims.exists('player-walk')) {
      this.anims.create({
        key: 'player-walk',
        frames: this.anims.generateFrameNumbers('player', { start: 0, end: 3 }),
        frameRate: 6,
        repeat: -1,
      });
    }

    this.scene.start('TitleScene');
  }
}
