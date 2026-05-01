import * as Phaser from 'phaser';
import { BootScene } from '../scenes/BootScene';
import { EndingScene } from '../scenes/EndingScene';
import { TitleScene } from '../scenes/TitleScene';
import { WalkScene } from '../scenes/WalkScene';

export const GAME_WIDTH = 1280;
export const GAME_HEIGHT = 720;

export const gameConfig: Phaser.Types.Core.GameConfig = {
  type: Phaser.AUTO,
  width: GAME_WIDTH,
  height: GAME_HEIGHT,
  parent: 'game',
  backgroundColor: '#1a1208',
  render: {
    antialias: true,
    pixelArt: false,
    roundPixels: true,
  },
  scale: {
    mode: Phaser.Scale.FIT,
    autoCenter: Phaser.Scale.CENTER_BOTH,
  },
  scene: [BootScene, TitleScene, WalkScene, EndingScene],
};
