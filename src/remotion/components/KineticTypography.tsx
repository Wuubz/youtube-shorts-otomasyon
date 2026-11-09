import React from 'react';
import {interpolate, spring, useCurrentFrame, useVideoConfig} from 'remotion';
import type {Theme} from '../theme';
import type {Cue, SceneSpec} from '../types';

// Belgesel başlığı gibi büyük tipografi: kelimeler söylendikleri anda tek tek ekrana vurur (spring ile büyükten oturur),
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
          const hit = spring({frame: frame - startFrame, fps, config: {damping: 11, stiffness: 340, mass: 0.55}});
          const active = cue.highlight && t >= w.s && t < w.e;
          const color = active ? glow : w.emph ? theme.accent : base;
          return (
            <span
              key={i}
              style={{
                display: 'inline-block', margin: '0 0.27em', color, opacity: shown ? 1 : 0,
                transform: `translateY(${interpolate(hit, [0, 1], [26, 0])}px) scale(${interpolate(hit, [0, 1], [1.7, 1])})`,
                WebkitTextStroke: `${theme.outlineWidth}px ${theme.outline}`, paintOrder: 'stroke fill',
                textShadow: active ? `0 0 38px ${glow}, ${theme.shadow}` : theme.shadow,
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

// key_phrase anında ekranı dolduran şok ifade: neon çerçeveli kutu büyükten çarpar (sarsıntı ShortsComposition'da tetiklenir)
const KeyCallout: React.FC<{scenes: SceneSpec[]; theme: Theme}> = ({scenes, theme}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();

  for (const scene of scenes) {
    if (scene.type === 'ancient_scroll' || scene.keyStart === null || !scene.keyPhrase) {
      continue; // parşömen anahtar ifadeyi kendisi taşır
    }
    const start = Math.round(scene.keyStart * fps);
    const sceneEnd = scene.start + scene.frames;
    const end = Math.min(Math.max(Math.round((scene.keyEnd ?? scene.keyStart) * fps) + 12, start + 34), sceneEnd);
    if (frame < start || frame >= end) {
      continue;
    }
    const local = frame - start;
    const p = spring({frame: local, fps, config: {damping: 10, stiffness: 330, mass: 0.6}});
    const fade = interpolate(frame, [end - 6, end], [1, 0], {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'});
    const text = scene.keyPhrase;
    const size = Math.max(70, Math.min(124, 134 - text.length * 2.8));
    const neon = theme.highlight;
    const flicker = 0.85 + 0.15 * Math.sin(local * 1.7);
    return (
      <div style={{position: 'absolute', left: 50, right: 50, top: 700, display: 'flex', justifyContent: 'center', pointerEvents: 'none'}}>
        <div
          style={{
            padding: '26px 46px', maxWidth: 980, textAlign: 'center', borderRadius: 18,
            background: 'rgba(0,0,0,0.62)', border: `7px solid ${neon}`,
            boxShadow: `0 0 34px ${neon}, inset 0 0 26px ${neon}55`,
            fontFamily: theme.fontFamily, fontWeight: 900, fontSize: size, lineHeight: 1.05, letterSpacing: 2,
            color: '#FFFFFF', textTransform: 'uppercase', textShadow: `0 0 ${24 * flicker}px ${neon}`,
            opacity: Math.min(local / 2, 1) * fade,
            transform: `scale(${interpolate(p, [0, 1], [2.5, 1]) * (1 - (1 - fade) * 0.12)}) rotate(${interpolate(p, [0, 1], [-5, 0])}deg)`,
          }}
        >
          {text}
        </div>
      </div>
    );
  }
  return null;
};

export const KineticTypography: React.FC<{cues: Cue[]; scenes: SceneSpec[]; theme: Theme}> = ({cues, scenes, theme}) => {
  const frame = useCurrentFrame();
  // Kanıt belgesinde anlatım metni belgenin kendisidir (kelime kelime altı çizilir): o aralıkta alt yazı gizlenir
  const hidden = scenes.some((s) => s.type === 'evidence_document' && frame >= s.start + s.impact - 2 && frame < s.start + s.frames);
  return (
    <>
      {hidden ? null : <WordLayer cues={cues} theme={theme} />}
      <KeyCallout scenes={scenes} theme={theme} />
    </>
  );
};
