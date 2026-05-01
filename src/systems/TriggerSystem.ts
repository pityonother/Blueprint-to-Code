import * as Phaser from 'phaser';
import type { TriggerConfig } from '../types';

export class TriggerSystem extends Phaser.Events.EventEmitter {
  private readonly fired = new Set<string>();
  private readonly elapsedById = new Map<string, number>();
  private readonly markers = new Map<string, Phaser.GameObjects.Image>();

  constructor(private scene: Phaser.Scene, private triggers: TriggerConfig[]) {
    super();
    this.createMarkers();
  }

  update(playerX: number, delta: number): void {
    for (const cfg of this.triggers) {
      if (this.fired.has(cfg.id)) {
        continue;
      }

      const elapsed = (this.elapsedById.get(cfg.id) ?? 0) + delta;
      this.elapsedById.set(cfg.id, elapsed);

      const readyForDelay = cfg.delayMs === undefined || elapsed >= cfg.delayMs;
      if (!readyForDelay) {
        continue;
      }

      const inZone = cfg.autoTrigger
        ? playerX >= cfg.x
        : Math.abs(playerX - cfg.x) <= cfg.zoneWidth / 2;

      if (inZone) {
        this.fired.add(cfg.id);
        const marker = this.markers.get(cfg.id);
        marker?.destroy();
        this.emit('trigger', cfg);
      }
    }
  }

  destroy(): void {
    for (const marker of this.markers.values()) {
      marker.destroy();
    }
    this.removeAllListeners();
  }

  private createMarkers(): void {
    for (const cfg of this.triggers) {
      if (cfg.id === 'door' || cfg.id === 'start_junction') {
        continue;
      }

      const marker = this.scene.add.image(cfg.x, 505, 'trigger-glow');
      marker.setDepth(250).setAlpha(0.45).setBlendMode(Phaser.BlendModes.SCREEN);
      this.scene.tweens.add({
        targets: marker,
        alpha: { from: 0.35, to: 0.72 },
        scale: { from: 0.92, to: 1.06 },
        duration: 2500,
        yoyo: true,
        repeat: -1,
        ease: 'Sine.InOut',
      });
      this.markers.set(cfg.id, marker);
    }
  }
}
