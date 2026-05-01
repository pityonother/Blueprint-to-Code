import * as Phaser from 'phaser';
import { GROUND_Y, PLAYER_START_X, PLAYER_START_Y, TRIGGERS, WORLD_WIDTH } from '../config/triggers';
import { Player } from '../entities/Player';
import { ParallaxBackground } from '../systems/ParallaxBackground';
import { PetalEmitter } from '../systems/PetalEmitter';
import { TextBox } from '../systems/TextBox';
import { TriggerSystem } from '../systems/TriggerSystem';
import { WarmthSystem } from '../systems/WarmthSystem';
import type { TriggerConfig } from '../types';

export class WalkScene extends Phaser.Scene {
  private player!: Player;
  private bg!: ParallaxBackground;
  private petals!: PetalEmitter;
  private textBox!: TextBox;
  private triggers!: TriggerSystem;
  private warmth!: WarmthSystem;
  private rightKey!: Phaser.Input.Keyboard.Key;
  private dKey!: Phaser.Input.Keyboard.Key;
  private escKey!: Phaser.Input.Keyboard.Key;
  private touchWalking = false;
  private dialogueActive = false;
  private pauseOverlay?: Phaser.GameObjects.Container;

  constructor() {
    super('WalkScene');
  }

  create(): void {
    this.cameras.main.setBounds(0, 0, WORLD_WIDTH, 720);
    this.bg = new ParallaxBackground(this);
    this.createLandmarks();
    this.warmth = new WarmthSystem(this);
    this.petals = new PetalEmitter(this, 1280);
    this.player = new Player(this, PLAYER_START_X, PLAYER_START_Y);
    this.textBox = new TextBox(this);
    this.triggers = new TriggerSystem(this, TRIGGERS);

    this.cameras.main.startFollow(this.player.sprite, true, 0.06, 0.06, -280, 0);

    const keyboard = this.input.keyboard;
    if (!keyboard) {
      throw new Error('Keyboard input unavailable');
    }

    this.rightKey = keyboard.addKey(Phaser.Input.Keyboard.KeyCodes.RIGHT);
    this.dKey = keyboard.addKey(Phaser.Input.Keyboard.KeyCodes.D);
    this.escKey = keyboard.addKey(Phaser.Input.Keyboard.KeyCodes.ESC);

    this.input.on('pointerdown', (pointer: Phaser.Input.Pointer) => {
      if (this.dialogueActive) {
        return;
      }
      this.touchWalking = pointer.x >= this.scale.width / 2;
    });
    this.input.on('pointerup', () => {
      this.touchWalking = false;
    });

    this.triggers.on('trigger', (cfg: TriggerConfig) => {
      void this.runTrigger(cfg);
    });

    this.warmth.setLevel(1);
  }

  update(_time: number, delta: number): void {
    if (Phaser.Input.Keyboard.JustDown(this.escKey)) {
      this.togglePause();
    }

    if (!this.dialogueActive) {
      const walking = this.rightKey.isDown || this.dKey.isDown || this.touchWalking;
      if (walking) {
        this.player.walk(delta);
      } else {
        this.player.idle();
      }
    }

    this.player.setAnimationSpeed(this.warmth.currentParams.playerAnimSpeed);
    this.bg.update(
      this.cameras.main.scrollX,
      this.warmth.currentParams.parallaxSpeedMul,
      this.warmth.currentParams.skyTint,
    );
    this.petals.updateDensity(this.warmth.currentParams.petalDensityMul);
    this.triggers.update(this.player.x, delta);
  }

  private async runTrigger(cfg: TriggerConfig): Promise<void> {
    this.dialogueActive = true;
    this.touchWalking = false;
    this.player.stop();

    await this.zoomCamera(1.05, 450);
    await this.textBox.showLines(cfg.lines);
    await this.zoomCamera(1, 400);

    if (cfg.advanceWarmthTo) {
      this.warmth.setLevel(cfg.advanceWarmthTo);
    }

    this.dialogueActive = false;

    if (cfg.id === 'door') {
      this.player.sprite.x = cfg.x - 24;
      await this.player.raiseHand();
      this.scene.start('EndingScene');
    }
  }

  private zoomCamera(targetZoom: number, duration: number): Promise<void> {
    return new Promise((resolve) => {
      this.tweens.add({
        targets: this.cameras.main,
        zoom: targetZoom,
        duration,
        ease: 'Sine.InOut',
        onComplete: () => resolve(),
      });
    });
  }

  private togglePause(): void {
    if (this.pauseOverlay) {
      this.pauseOverlay.destroy(true);
      this.pauseOverlay = undefined;
      return;
    }

    const bg = this.add.rectangle(0, 0, 1280, 720, 0x000000, 0.5).setOrigin(0, 0);
    const title = this.add
      .text(640, 320, '暂停', {
        fontFamily: '"Noto Serif SC", "Songti SC", "SimSun", serif',
        fontSize: '42px',
        color: '#fff7e6',
      })
      .setOrigin(0.5);
    const hint = this.add
      .text(640, 372, '按 Esc 返回这条路', {
        fontFamily: '"Noto Serif SC", "Songti SC", "SimSun", serif',
        fontSize: '24px',
        color: '#f5c97a',
      })
      .setOrigin(0.5);
    this.pauseOverlay = this.add.container(0, 0, [bg, title, hint]).setScrollFactor(0).setDepth(1200);
  }

  private createLandmarks(): void {
    this.add.rectangle(WORLD_WIDTH / 2, GROUND_Y + 40, WORLD_WIDTH, 100, 0x46342c).setDepth(80);
    this.add.rectangle(WORLD_WIDTH / 2, GROUND_Y + 62, WORLD_WIDTH, 32, 0x2f2320).setDepth(81);

    this.add.image(1500, GROUND_Y + 6, 'tree-cherry').setOrigin(0.5, 1).setDepth(205);
    this.add.image(2700, GROUND_Y + 6, 'shop-old').setOrigin(0.5, 1).setDepth(185);
    this.add.image(3900, GROUND_Y + 6, 'bench-cat').setOrigin(0.5, 1).setDepth(185);
    this.add.image(5300, GROUND_Y + 10, 'sunset-wall').setOrigin(0.5, 1).setDepth(175);
    this.add.image(6700, GROUND_Y + 8, 'mailbox').setOrigin(0.5, 1).setDepth(185);
    this.add.image(7800, GROUND_Y + 6, 'door-home').setOrigin(0.5, 1).setDepth(185);
  }
}
