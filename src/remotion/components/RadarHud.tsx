import React from 'react';
import {AbsoluteFill, Easing, interpolate, random, spring, useCurrentFrame, useVideoConfig} from 'remotion';
import type {SceneSpec} from '../types';

const CX = 540;
const CY = 820;
const R = 330;

const ticks = (() => {
  const d: string[] = [];
  for (let i = 0; i < 72; i++) {
    const a = (i / 72) * Math.PI * 2;
    const len = i % 6 === 0 ? 22 : 10;
    d.push(`M${(R * Math.cos(a)).toFixed(1)} ${(R * Math.sin(a)).toFixed(1)}L${((R - len) * Math.cos(a)).toFixed(1)} ${((R - len) * Math.sin(a)).toFixed(1)}`);
  }
  return d.join('');
})();

// Bilimkurgu radar arayüzü: dönen tarama, hedef noktaları ve `impact` karesinde kilitlenen hedef.
export const RadarHud: React.FC<{scene: SceneSpec; frames: number; color: string}> = ({scene, frames, color}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const impact = scene.impact;

  const intro = spring({frame, fps, config: {damping: 14, stiffness: 120, mass: 0.8}});
  const exit = interpolate(frame, [frames - 8, frames], [1, 0], {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'});
  const sweep = (frame * 6) % 360;

  // hedef noktaları (sahne başına sabit)
  const blips = Array.from({length: 5}, (_, i) => {
    const a = random(`blip-a-${scene.start}-${i}`) * Math.PI * 2;
    const r = (0.25 + random(`blip-r-${scene.start}-${i}`) * 0.65) * R;
    return {a, x: Math.cos(a) * r, y: Math.sin(a) * r};
  });
  const target = blips[0];
  const lock = interpolate(frame, [impact - 10, impact], [0, 1], {
    extrapolateLeft: 'clamp', extrapolateRight: 'clamp', easing: Easing.out(Easing.cubic),
  });
  const locked = frame >= impact;
  const lockPop = spring({frame: frame - impact, fps, config: {damping: 8, stiffness: 220, mass: 0.5}});

  // koordinatlar: kilitlenene kadar akar, sonra sabitlenir
  const tick = locked ? impact : frame;
  const lat = (30 + random(`lat-${scene.start}`) * 30 + (locked ? 0 : random(`lt${tick}`) * 0.9)).toFixed(4);
  const lon = (-120 + random(`lon-${scene.start}`) * 160 + (locked ? 0 : random(`ln${tick}`) * 0.9)).toFixed(4);

  return (
    <AbsoluteFill style={{pointerEvents: 'none', opacity: exit}}>
      <AbsoluteFill style={{background: 'rgba(0,10,20,0.42)', opacity: Math.min(intro, 1)}} />
      <div
        style={{
          position: 'absolute', left: CX - R - 30, top: CY - R - 30, width: (R + 30) * 2, height: (R + 30) * 2,
          transform: `scale(${interpolate(intro, [0, 1], [0.6, 1])})`, opacity: Math.min(intro, 1),
        }}
      >
        {/* dönen tarama kolu */}
        <div
          style={{
            position: 'absolute', left: 30, top: 30, width: R * 2, height: R * 2, borderRadius: '50%',
            background: `conic-gradient(from ${sweep}deg, transparent 0deg, ${color}66 62deg, ${color}00 63deg, transparent 360deg)`,
          }}
        />
        <svg viewBox={`${-R - 30} ${-R - 30} ${(R + 30) * 2} ${(R + 30) * 2}`} width="100%" height="100%" style={{position: 'absolute', inset: 0}}>
          {[R, R * 0.75, R * 0.5, R * 0.25].map((r, i) => (
            <circle key={i} cx={0} cy={0} r={r} fill="none" stroke={color} strokeWidth={i === 0 ? 4 : 2} opacity={i === 0 ? 0.95 : 0.55} />
          ))}
          <path d={`M${-R} 0H${R}M0 ${-R}V${R}`} stroke={color} strokeWidth={1.6} opacity={0.45} />
          <path d={ticks} stroke={color} strokeWidth={2} opacity={0.8} fill="none" />
          {blips.map((b, i) => {
            const angle = ((Math.atan2(b.y, b.x) * 180) / Math.PI + 360) % 360;
            const behind = (sweep - angle + 360) % 360; // süpürmenin geçtiği andan beri açı
            const glow = Math.max(0, 1 - behind / 140);
            return <circle key={i} cx={b.x} cy={b.y} r={i === 0 ? 9 : 7} fill={color} opacity={0.25 + glow * 0.75} />;
          })}
          {/* hedef kilidi */}
          <g transform={`translate(${target.x} ${target.y})`} opacity={lock}>
            <g transform={`scale(${interpolate(lock, [0, 1], [3.2, 1]) * (locked ? interpolate(lockPop, [0, 1], [1.25, 1]) : 1)})`}>
              <path
                d="M-34 -14V-34H-14M14 -34H34V-14M34 14V34H14M-14 34H-34V14"
                fill="none" stroke="#FF3B3B" strokeWidth={5} strokeLinecap="round"
              />
            </g>
            {locked ? (
              <circle
                cx={0} cy={0} r={34 + (frame - impact) * 7} fill="none" stroke="#FF3B3B" strokeWidth={3}
                opacity={Math.max(0, 1 - (frame - impact) / 14)}
              />
            ) : null}
          </g>
        </svg>
      </div>

      <div style={{position: 'absolute', left: CX - R, top: CY - R - 92, fontFamily: '"DejaVu Sans Mono", Consolas, monospace', color, fontSize: 30, letterSpacing: 2, lineHeight: 1.35, textShadow: `0 0 14px ${color}`}}>
        <div>LAT {lat}</div>
        <div>LON {lon}</div>
      </div>
      <div
        style={{
          position: 'absolute', right: CX - R, top: CY - R - 92, fontFamily: '"DejaVu Sans Mono", Consolas, monospace', fontSize: 30, fontWeight: 800,
          letterSpacing: 3, color: locked ? '#FF3B3B' : color, textShadow: `0 0 14px ${locked ? '#FF3B3B' : color}`,
          opacity: locked ? 1 : 0.5 + 0.5 * Math.sin(frame / 3),
        }}
      >
        {locked ? 'TARGET LOCKED' : 'SCANNING...'}
      </div>
    </AbsoluteFill>
  );
};
