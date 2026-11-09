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

export const ShortsComposition: React.FC<ShortsProps> = (props) => {
  const frame = useCurrentFrame();
  const theme = resolveTheme(props.channel_theme);
  const {scenes, effects, transitions, xfadeFrames, fps} = props;
  const last = scenes.length - 1;

  return (
    <AbsoluteFill style={{backgroundColor: '#000'}}>
      <AbsoluteFill>
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
