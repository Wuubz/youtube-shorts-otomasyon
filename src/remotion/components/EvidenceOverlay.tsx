import React from 'react';
import {AbsoluteFill, Easing, interpolate, spring, useCurrentFrame, useVideoConfig} from 'remotion';
import type {SceneSpec} from '../types';

const INK = '#2a2118';
const MONO = '"DejaVu Sans Mono", "Liberation Mono", Consolas, "Courier New", monospace';

const bare = (w: string) => w.toLowerCase().replace(/[^a-z0-9']+/g, '');

// Masaya fırlatılan gizli dosya: anlatıcı cümleyi okurken her kelimenin altı kırmızı kalemle çizilir (stroke-dashoffset),
// anahtar ifade sarı fosforlu kalemle işaretlenir. Anlatım metni belgenin kendisi olduğu için bu sahnede altyazı gizlenir.
export const EvidenceOverlay: React.FC<{scene: SceneSpec; frames: number}> = ({scene, frames}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const t = (scene.start + frame) / fps;
  const impact = scene.impact;

  // 1) fırlatma: sağ üstten dönerek hızlanır, `impact` karesinde masaya çarpar
  const fly = interpolate(frame, [Math.max(impact - 12, 0), impact], [0, 1], {
    extrapolateLeft: 'clamp', extrapolateRight: 'clamp', easing: Easing.in(Easing.quad),
  });
  // 2) çarpma sonrası kısa sekme
  const land = spring({frame: frame - impact, fps, config: {damping: 7, stiffness: 240, mass: 0.5}});
  const bounce = frame >= impact ? interpolate(land, [0, 1], [22, 0]) : 0;
  const x = interpolate(fly, [0, 1], [520, 0]);
  const y = interpolate(fly, [0, 1], [-1000, 0]) + bounce;
  const rotate = interpolate(fly, [0, 1], [32, -2.5]);
  const exit = interpolate(frame, [frames - 8, frames], [1, 0], {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'});
  const backdrop = interpolate(frame, [0, impact], [0, 1], {extrapolateRight: 'clamp'}) * exit;

  const keyWords = new Set(scene.keyPhrase.split(/\s+/).map(bare).filter(Boolean));
  const crowded = scene.words.length > 24;
  const fontSize = crowded ? 38 : 46;

  // daktilo başlığı
  const header = `EVIDENCE  /  FILE ${String(1000 + scene.start % 900).slice(-3)}`;
  const typed = frame < impact ? 0 : Math.min(header.length, Math.floor((frame - impact) / 1.3));

  // damga
  const stampP = spring({frame: frame - (impact + 7), fps, config: {damping: 9, stiffness: 260, mass: 0.6}});

  return (
    <AbsoluteFill style={{pointerEvents: 'none'}}>
      <AbsoluteFill style={{background: 'rgba(0,0,0,0.38)', opacity: backdrop}} />
      <div
        style={{
          position: 'absolute', left: 90, top: 330, width: 900,
          opacity: frame < Math.max(impact - 12, 0) ? 0 : exit,
          transform: `translate(${x}px, ${y}px) rotate(${rotate}deg)`,
          transformOrigin: '50% 20%',
        }}
      >
        <div
          style={{
            position: 'relative', padding: '46px 52px 44px',
            background: 'linear-gradient(160deg, #efe3c6 0%, #e2d2ab 60%, #d8c59a 100%)',
            borderRadius: 6, boxShadow: '0 28px 60px rgba(0,0,0,0.6), inset 0 0 70px rgba(120,80,20,0.28)',
            color: INK, fontFamily: MONO,
          }}
        >
          {/* bant parçaları */}
          <div style={{position: 'absolute', top: -18, left: 60, width: 150, height: 38, background: 'rgba(235,225,170,0.78)', transform: 'rotate(-6deg)'}} />
          <div style={{position: 'absolute', top: -16, right: 70, width: 130, height: 36, background: 'rgba(235,225,170,0.78)', transform: 'rotate(5deg)'}} />

          <div style={{fontSize: 28, fontWeight: 700, letterSpacing: 4, color: '#8b1a1a', minHeight: 36}}>
            {header.slice(0, typed)}
            {typed < header.length && frame >= impact ? '▌' : ''}
          </div>
          <div style={{height: 4, background: INK, opacity: 0.8, margin: '12px 0 26px'}} />

          <div style={{display: 'flex', flexWrap: 'wrap', alignItems: 'baseline', lineHeight: 1.5, fontSize, fontWeight: 700}}>
            {scene.words.map((w, i) => {
              const isKey = keyWords.has(bare(w.t));
              const progress = interpolate(t, [w.s, Math.max(w.e, w.s + 0.12)], [0, 1], {
                extrapolateLeft: 'clamp', extrapolateRight: 'clamp',
              });
              const spoken = t >= w.s;
              return (
                <span key={i} style={{position: 'relative', display: 'inline-block', margin: '0 0.28em 6px 0', opacity: spoken ? 1 : 0.5}}>
                  {isKey ? (
                    <span
                      style={{
                        position: 'absolute', left: -6, right: -6, top: '18%', bottom: '4%', background: 'rgba(255,225,0,0.8)',
                        transform: `scaleX(${progress})`, transformOrigin: 'left center', zIndex: 0, borderRadius: 3,
                      }}
                    />
                  ) : null}
                  <span style={{position: 'relative', zIndex: 1}}>{w.t}</span>
                  <svg
                    viewBox="0 0 100 14" preserveAspectRatio="none"
                    style={{position: 'absolute', left: -2, right: -2, bottom: -9, width: 'calc(100% + 4px)', height: 14, zIndex: 2, overflow: 'visible'}}
                  >
                    <path
                      d="M1 8 Q 20 2 40 8 T 80 7 T 99 6" pathLength={1} fill="none"
                      stroke={isKey ? '#d10000' : '#c62828'} strokeWidth={isKey ? 5 : 3.2} strokeLinecap="round"
                      strokeDasharray={1} strokeDashoffset={1 - progress}
                      vectorEffect="non-scaling-stroke"
                    />
                  </svg>
                </span>
              );
            })}
          </div>

          {/* karartılmış (redacted) satırlar */}
          <div style={{marginTop: 22, display: 'flex', flexDirection: 'column', gap: 10}}>
            {[0.82, 0.55, 0.7].map((wd, i) => (
              <div key={i} style={{height: 16, width: `${wd * 100}%`, background: INK, opacity: 0.88, borderRadius: 2}} />
            ))}
          </div>

          <div
            style={{
              position: 'absolute', right: 34, bottom: 30, padding: '6px 20px', border: '6px solid #b3121b', color: '#b3121b',
              fontSize: 38, fontWeight: 800, letterSpacing: 4, borderRadius: 6, background: 'rgba(179,18,27,0.06)',
              opacity: Math.min(Math.max(stampP, 0), 1) * 0.92,
              transform: `rotate(-11deg) scale(${interpolate(stampP, [0, 1], [2.2, 1])})`,
            }}
          >
            CLASSIFIED
          </div>
        </div>
      </div>
    </AbsoluteFill>
  );
};
