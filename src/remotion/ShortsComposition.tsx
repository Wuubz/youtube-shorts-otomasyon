import React from 'react';
import {AbsoluteFill, Sequence, useCurrentFrame} from 'remotion';
import {Atmosphere} from './components/Atmosphere';
import {InfoCard} from './components/InfoCard';
import {KineticSubtitles} from './components/KineticSubtitles';
import {ProgressBar} from './components/ProgressBar';
import {SceneLayer} from './components/SceneLayer';
import {SubscribeCTA} from './components/SubscribeCTA';
import {resolveTheme} from './theme';
import type {ShortsProps} from './types';

const JOLT_FRAMES = 9;

// Korku teması: yalnızca sahne geçişlerinde ~0.3 sn süren hafif bir sarsıntı (videonun tamamında titreme yok).
const joltOffset = (frame: number, starts: number[]): {x: number; y: number; r: number; k: number} => {
  for (let i = 1; i < starts.length; i++) {
    const d = frame - starts[i];
    if (d >= 0 && d < JOLT_FRAMES) {
      const k = 1 - d / JOLT_FRAMES; // 1 -> 0: sarsıntı sönerek biter
      const amp = k * 9;
      return {x: amp * Math.sin(d * 2.3 + i), y: amp * 0.7 * Math.cos(d * 1.9 + i * 2), r: amp * 0.05 * Math.sin(d * 1.7), k};
    }
  }
  return {x: 0, y: 0, r: 0, k: 0};
};

export const ShortsComposition: React.FC<ShortsProps> = (props) => {
  const frame = useCurrentFrame();
  const theme = resolveTheme(props.channel_theme);
  const {scenes, effects, transitions, xfadeFrames} = props;
  const last = scenes.length - 1;

  const jolt = theme.jolt ? joltOffset(frame, scenes.map((s) => s.start)) : {x: 0, y: 0, r: 0, k: 0};
  const shaking = jolt.k > 0;

  return (
    <AbsoluteFill style={{backgroundColor: '#000'}}>
      <AbsoluteFill
        style={{
          // sarsıntıda kenarlarda siyah boşluk görünmesin diye hafif büyütülür
          transform: shaking ? `translate(${jolt.x}px, ${jolt.y}px) rotate(${jolt.r}deg) scale(${1 + 0.04 * jolt.k})` : undefined,
        }}
      >
        {scenes.map((scene, i) => {
          const extend = i < last ? xfadeFrames : 0;
          return (
            <Sequence key={i} from={scene.start} durationInFrames={scene.frames + extend} layout="none">
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
      </AbsoluteFill>

      <Atmosphere theme={theme} seed={props.seed} />
      <ProgressBar color={theme.progress} />
      <InfoCard labels={props.labels} theme={theme} />
      <KineticSubtitles cues={props.cues} theme={theme} />
      <SubscribeCTA theme={theme} text={props.ctaText} />
    </AbsoluteFill>
  );
};
