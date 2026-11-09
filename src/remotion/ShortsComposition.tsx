import React from 'react';
import {AbsoluteFill, Sequence, useCurrentFrame} from 'remotion';
import {Atmosphere} from './components/Atmosphere';
import {CinematicVignette} from './components/CinematicVignette';
import {DynamicFocalBadge} from './components/DynamicFocalBadge';
import {InfoCard} from './components/InfoCard';
import {KineticTypography} from './components/KineticTypography';
import {ProgressBar} from './components/ProgressBar';
import {SceneLayer} from './components/SceneLayer';
import {SceneOverlay} from './components/SceneOverlay';
import {SubscribeCTA} from './components/SubscribeCTA';
import {resolveTheme} from './theme';
import type {ShortsProps} from './types';

const JOLT_FRAMES = 9;
const SHAKE_FRAMES = 10;

type Offset = {x: number; y: number; r: number; k: number};
const NONE: Offset = {x: 0, y: 0, r: 0, k: 0};

// Korku teması: yalnızca sahne geçişlerinde ~0.3 sn süren hafif bir sarsıntı (videonun tamamında titreme yok).
const joltOffset = (frame: number, starts: number[]): Offset => {
  for (let i = 1; i < starts.length; i++) {
    const d = frame - starts[i];
    if (d >= 0 && d < JOLT_FRAMES) {
      const k = 1 - d / JOLT_FRAMES; // 1 -> 0: sarsıntı sönerek biter
      const amp = k * 9;
      return {x: amp * Math.sin(d * 2.3 + i), y: amp * 0.7 * Math.cos(d * 1.9 + i * 2), r: amp * 0.05 * Math.sin(d * 1.7), k};
    }
  }
  return NONE;
};

// key_phrase vurulduğunda (ya da parşömende mühür basıldığında) ekranı hafifçe sarsan darbe
const shakeOffset = (frame: number, hits: number[]): Offset => {
  for (const hit of hits) {
    const d = frame - hit;
    if (d >= 0 && d < SHAKE_FRAMES) {
      const k = 1 - d / SHAKE_FRAMES;
      const amp = k * 8;
      return {x: amp * Math.sin(d * 3.1 + hit), y: amp * Math.cos(d * 2.6 + hit), r: amp * 0.04 * Math.sin(d * 2.2), k};
    }
  }
  return NONE;
};

export const ShortsComposition: React.FC<ShortsProps> = (props) => {
  const frame = useCurrentFrame();
  const theme = resolveTheme(props.channel_theme);
  const {scenes, effects, transitions, xfadeFrames, fps} = props;
  const last = scenes.length - 1;

  const hits = scenes
    .map((s) => (s.type === 'ancient_scroll' ? s.start + s.impact : s.keyStart !== null ? Math.round(s.keyStart * fps) - 1 : -1))
    .filter((h) => h >= 0);
  const jolt = theme.jolt ? joltOffset(frame, scenes.map((s) => s.start)) : NONE;
  const shake = shakeOffset(frame, hits);
  const kick = Math.max(jolt.k, shake.k);
  const moving = kick > 0;
  const ox = jolt.x + shake.x;
  const oy = jolt.y + shake.y;
  const or = jolt.r + shake.r;

  return (
    <AbsoluteFill style={{backgroundColor: '#000'}}>
      <AbsoluteFill
        style={{
          // sarsıntıda kenarlarda siyah boşluk görünmesin diye darbe şiddetiyle orantılı hafif büyütülür
          transform: moving ? `translate(${ox}px, ${oy}px) rotate(${or}deg) scale(${1 + 0.04 * kick})` : undefined,
        }}
      >
        {scenes.map((scene, i) => {
          const extend = i < last ? xfadeFrames : 0;
          return (
            <Sequence key={`s${i}`} from={scene.start} durationInFrames={scene.frames + extend} layout="none">
              <SceneLayer
                image={scene.image}
                effect={effects[i] ?? 'zoom_in'}
                zoomMax={i === 0 ? props.hookZoom : props.zoomMax}
                durationInFrames={scene.frames + extend}
                transition={i === 0 ? null : (transitions[i - 1] ?? 'dissolve')}
                xfade={xfadeFrames}
              />
            </Sequence>
          );
        })}
        <CinematicVignette theme={theme} />
        <Atmosphere theme={theme} seed={props.seed} />
        {scenes.map((scene, i) => (
          <Sequence key={`o${i}`} from={scene.start} durationInFrames={scene.frames + 2} layout="none">
            <SceneOverlay scene={scene} theme={theme} />
            <DynamicFocalBadge badge={scene.badge} durationInFrames={scene.frames} />
          </Sequence>
        ))}
      </AbsoluteFill>

      <ProgressBar color={theme.progress} />
      <InfoCard labels={props.labels} theme={theme} />
      <KineticTypography cues={props.cues} scenes={scenes} theme={theme} />
      <SubscribeCTA theme={theme} text={props.ctaText} />
    </AbsoluteFill>
  );
};
