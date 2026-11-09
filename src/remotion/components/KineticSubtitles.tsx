import React from 'react';
import {interpolate, spring, useCurrentFrame, useVideoConfig} from 'remotion';
import type {Theme} from '../theme';
import type {Cue} from '../types';

const ENTER_CONFIG = {
  pop: {damping: 12, stiffness: 260, mass: 0.6},
  bounce: {damping: 7, stiffness: 240, mass: 0.6},
  rise: {damping: 16, stiffness: 200, mass: 0.7},
  shake: {damping: 9, stiffness: 300, mass: 0.5},
  fade: {damping: 30, stiffness: 120, mass: 0.8},
} as const;

export const KineticSubtitles: React.FC<{cues: Cue[]; theme: Theme}> = ({cues, theme}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const t = frame / fps;

  const index = cues.findIndex((c) => t >= c.start && t < c.end);
  if (index < 0) {
    return null;
  }
  const cue = cues[index];
  const startFrame = Math.round(cue.start * fps);
  const endFrame = Math.round(cue.end * fps);
  const local = frame - startFrame;

  // Blok girişi: kanalın animasyon türüne göre spring
  const p = spring({frame: local, fps, config: ENTER_CONFIG[theme.anim]});
  let transform = '';
  let opacity = interpolate(local, [0, 2], [0, 1], {extrapolateRight: 'clamp'});
  switch (theme.anim) {
    case 'pop':
      transform = `scale(${interpolate(p, [0, 1], [0.86, 1])})`;
      break;
    case 'bounce':
      transform = `scale(${interpolate(p, [0, 1], [0.65, 1])})`;
      break;
    case 'rise':
      transform = `translateY(${interpolate(p, [0, 1], [50, 0])}px)`;
      break;
    case 'shake':
      transform = `rotate(${interpolate(p, [0, 1], [-3, 0])}deg) scale(${interpolate(p, [0, 1], [1.06, 1])})`;
      break;
    default:
      opacity = interpolate(local, [0, 6], [0, 1], {extrapolateRight: 'clamp'});
  }
  opacity *= interpolate(frame, [endFrame - 2, endFrame], [1, 0], {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'});

  const base = cue.closing ? theme.accent : theme.colors[index % theme.colors.length];
  const glow = cue.closing ? '#FFFFFF' : theme.highlight;
  const fontSize = theme.fontSize * (cue.closing ? 1.1 : 1);

  return (
    <div
      style={{
        position: 'absolute',
        left: 70,
        right: 70,
        bottom: theme.bottom,
        display: 'flex',
        justifyContent: 'center',
        pointerEvents: 'none',
      }}
    >
      <div
        style={{
          display: 'flex',
          flexWrap: 'wrap',
          justifyContent: 'center',
          alignItems: 'baseline',
          maxWidth: 940,
          textAlign: 'center',
          lineHeight: 1.12,
          opacity,
          transform,
          transformOrigin: '50% 80%',
          padding: theme.box ? '14px 34px 22px' : 0,
          background: theme.box ?? undefined,
          borderRadius: theme.box ? 16 : undefined,
          fontFamily: theme.fontFamily,
          fontWeight: theme.fontWeight,
          fontSize,
          letterSpacing: theme.letterSpacing,
          textTransform: theme.uppercase ? 'uppercase' : 'none',
        }}
      >
        {cue.words.map((w, i) => {
          const active = cue.highlight && t >= w.s && t < w.e;
          const wp = active
            ? spring({
                frame: frame - Math.round(w.s * fps),
                fps,
                config: {damping: 9, stiffness: 220, mass: 0.5},
              })
            : 0;
          const color = active ? glow : w.emph ? theme.accent : base;
          return (
            <span
              key={i}
              style={{
                display: 'inline-block',
                margin: '0 0.27em',
                color,
                transform: `translateY(${-8 * wp}px) scale(${1 + 0.1 * wp})`,
                WebkitTextStroke: `${theme.outlineWidth}px ${theme.outline}`,
                paintOrder: 'stroke fill',
                textShadow: active ? `0 0 34px ${glow}, ${theme.shadow}` : theme.shadow,
              }}
            >
              {w.t}
            </span>
          );
        })}
      </div>
    </div>
  );
};
