import React from 'react';
import {interpolate, spring, useCurrentFrame, useVideoConfig} from 'remotion';
import type {Theme} from '../theme';
import type {Cue, SceneSpec} from '../types';

// Sade tipografi: kelimeler söylendikleri anda yumuşakça yerine oturur,
// o an okunan kelime kanalın parlama rengindedir. Blok değişince tüm kelimelerin yeri önceden ayrılır, metin zıplamaz.
const SIZE_FACTOR = 1.1;

const WordLayer: React.FC<{cues: Cue[]; theme: Theme}> = ({cues, theme}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const t = frame / fps;

  const index = cues.findIndex((c) => t >= c.start && t < c.end);
  if (index < 0) {
    return null;
  }
  const cue = cues[index];
  const endFrame = Math.round(cue.end * fps);
  const exit = interpolate(frame, [endFrame - 2, endFrame], [1, 0], {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'});
  const base = cue.closing ? theme.accent : theme.colors[index % theme.colors.length];
  const glow = cue.closing ? '#FFFFFF' : theme.highlight;
  const fontSize = theme.fontSize * SIZE_FACTOR * (cue.closing ? 1.08 : 1);

  return (
    <div style={{position: 'absolute', left: 60, right: 60, bottom: theme.bottom, display: 'flex', justifyContent: 'center', pointerEvents: 'none'}}>
      <div
        style={{
          display: 'flex', flexWrap: 'wrap', justifyContent: 'center', alignItems: 'baseline', maxWidth: 960, textAlign: 'center',
          lineHeight: 1.1, opacity: exit,
          padding: theme.box ? '14px 34px 22px' : 0, background: theme.box ?? undefined, borderRadius: theme.box ? 16 : undefined,
          fontFamily: theme.fontFamily, fontWeight: 900, fontSize, letterSpacing: theme.letterSpacing,
          textTransform: theme.uppercase ? 'uppercase' : 'none',
        }}
      >
        {cue.words.map((w, i) => {
          const startFrame = Math.round(w.s * fps) - 1;
          const shown = frame >= startFrame;
          const hit = spring({frame: frame - startFrame, fps, config: {damping: 18, stiffness: 220, mass: 0.7}});
          const active = cue.highlight && t >= w.s && t < w.e;
          const color = active ? glow : w.emph ? theme.accent : base;
          return (
            <span
              key={i}
              style={{
                display: 'inline-block', margin: '0 0.27em', color, opacity: shown ? 1 : 0,
                transform: `translateY(${interpolate(hit, [0, 1], [12, 0])}px) scale(${interpolate(hit, [0, 1], [1.12, 1])})`,
                WebkitTextStroke: `${theme.outlineWidth}px ${theme.outline}`, paintOrder: 'stroke fill',
                textShadow: active ? `0 0 22px ${glow}, ${theme.shadow}` : theme.shadow,
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

export const KineticTypography: React.FC<{cues: Cue[]; scenes: SceneSpec[]; theme: Theme}> = ({cues, scenes, theme}) => {
  const frame = useCurrentFrame();
  // Kanıt belgesinde anlatım metni belgenin kendisidir (kelime kelime altı çizilir): o aralıkta alt yazı gizlenir
  const hidden = scenes.some((s) => s.type === 'evidence_document' && frame >= s.start + s.impact - 2 && frame < s.start + s.frames);
  return (
    <>
      {hidden ? null : <WordLayer cues={cues} theme={theme} />}
    </>
  );
};
