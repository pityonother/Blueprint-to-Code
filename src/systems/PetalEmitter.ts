import * as Phaser from 'phaser';

export class PetalEmitter {
  private readonly particles: Phaser.GameObjects.Particles.ParticleEmitter;

  constructor(scene: Phaser.Scene, width: number) {
    this.particles = scene.add.particles(0, 0, 'petal', {
      x: { min: 0, max: width },
      y: -20,
      lifespan: 7500,
      speedX: { min: -28, max: 16 },
      speedY: { min: 18, max: 42 },
      scale: { start: 0.8, end: 0.25 },
      alpha: { start: 0.85, end: 0.15 },
      rotate: { min: 0, max: 180 },
      frequency: 900,
      quantity: 1,
      blendMode: 'ADD',
    });
    this.particles.setDepth(340).setScrollFactor(0);
  }

  updateDensity(multiplier: number): void {
    const frequency = Phaser.Math.Linear(1200, 430, Phaser.Math.Clamp((multiplier - 0.5) / 0.9, 0, 1));
    this.particles.setFrequency(frequency);
  }
}
