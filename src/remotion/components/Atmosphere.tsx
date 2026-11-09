import React from 'react';
import {AbsoluteFill, random, useCurrentFrame} from 'remotion';
import type {Theme} from '../theme';

const W = 1080;
const H = 1920;

// Not: renk tonu ve vinyet Python tarafında görsele bir kez işlenir (remotion_bridge._bake_look). Burada yalnızca
// ucuz çizilen katmanlar var: karışım modu (blend) ve büyük blur kullanılmaz, çünkü CPU render süresini katlar.

const Particles: React.FC<{theme: Theme; seed: number}> = ({theme, seed}) => {
  const frame = useCurrentFrame();
  const cfg = theme.particles;
  if (!cfg) {
    return null;
  }
  return (
    <AbsoluteFill>
      {Array.from({length: cfg.count}, (_, i) => {
        const r = (k: string) => random(`${seed}-${i}-${k}`);
        const size = cfg.min + r('s') * (cfg.max - cfg.min);
        const speed = (0.4 + r('v')) * cfg.speed;
        const y = (((r('y') * H - speed * frame * 2) % H) + H) % H;
        const x = r('x') * W + Math.sin(frame / (30 + r('f') * 40) + r('p') * 6) * 18;
        const twinkle = 0.35 + 0.65 * Math.abs(Math.sin(frame / (18 + r('t') * 30) + r('q') * 6));
        const halo = size * 3; // ışıma: box-shadow yerine ucuz radyal gradyan
        return (
          <div
            key={i}
            style={{
              position: 'absolute',
              left: x - halo / 2,
              top: y - halo / 2,
              width: size + halo,
              height: size + halo,
              borderRadius: '50%',
              background: `radial-gradient(circle, ${cfg.color} 0%, ${cfg.color} ${(size / (size + halo)) * 50}%, transparent 70%)`,
              opacity: twinkle * 0.8,
            }}
          />
        );
      })}
    </AbsoluteFill>
  );
};

// Eski film tozu ve dikey çizikler (chronicle)
const Dust: React.FC<{seed: number}> = ({seed}) => {
  const frame = useCurrentFrame();
  const step = Math.floor(frame / 3); // 10 fps titreşim: daha doğal görünür
  return (
    <AbsoluteFill>
      {Array.from({length: 9}, (_, i) => {
        const r = (k: string) => random(`${seed}-d${step}-${i}-${k}`);
        const show = r('show') > 0.45;
        return show ? (
          <div
            key={i}
            style={{
              position: 'absolute',
              left: r('x') * W,
              top: r('y') * H,
              width: 2 + r('w') * 5,
              height: 2 + r('w') * 5,
              borderRadius: '50%',
              background: 'rgba(255,245,220,0.55)',
            }}
          />
        ) : null;
      })}
      {random(`${seed}-scratch-${step}`) > 0.82 ? (
        <div
          style={{
            position: 'absolute',
            left: random(`${seed}-sx-${step}`) * W,
            top: 0,
            width: 2,
            height: H,
            background: 'rgba(255,245,220,0.22)',
          }}
        />
      ) : null}
    </AbsoluteFill>
  );
};

export const Atmosphere: React.FC<{theme: Theme; seed: number}> = ({theme, seed}) => {
  const frame = useCurrentFrame();
  return (
    <AbsoluteFill style={{pointerEvents: 'none'}}>
      {theme.glow ? (
        <AbsoluteFill
          style={{
            background: `radial-gradient(ellipse 70% 38% at ${theme.glow.at}, ${theme.glow.color}, transparent 75%)`,
            opacity: theme.glow.opacity * (0.85 + 0.15 * Math.sin(frame / 22)),
          }}
        />
      ) : null}
      <Particles theme={theme} seed={seed} />
      {theme.flicker > 0 ? ( // eski film titreşimi: parlaklık filtresi yerine yarı saydam siyah örtü
        <AbsoluteFill
          style={{background: '#000', opacity: Math.abs(theme.flicker * 4 * Math.sin(frame * 7.3) * Math.sin(frame * 1.7))}}
        />
      ) : null}
      {theme.dust ? <Dust seed={seed} /> : null}
      {theme.filmFrame ? (
        <AbsoluteFill
          style={{
            boxShadow: 'inset 0 0 0 26px rgba(20,12,4,0.55), inset 0 0 120px 30px rgba(0,0,0,0.6)',
            borderRadius: 28,
          }}
        />
      ) : null}
    </AbsoluteFill>
  );
};
