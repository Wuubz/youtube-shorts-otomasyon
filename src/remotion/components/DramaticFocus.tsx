import React from 'react';
import {AbsoluteFill, Easing, interpolate, useCurrentFrame, useVideoConfig} from 'remotion';
import type {SceneSpec} from '../types';

const MONO = '"DejaVu Sans Mono", "Liberation Mono", Consolas, monospace';
const BAR = 150;

const pad2 = (n: number) => String(Math.floor(n)).padStart(2, '0');

// Sinematik odak: sinema şeridi çubukları, daralan ışık huzmesi ve `impact` karesinde oturan kamera odak çerçevesi.
export const DramaticFocus: React.FC<{scene: SceneSpec; frames: number; color: string}> = ({scene, frames, color}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const impact = scene.impact;

  const bars = interpolate(frame, [0, 10], [0, 1], {extrapolateRight: 'clamp', easing: Easing.out(Easing.cubic)});
  const exit = interpolate(frame, [frames - 8, frames], [1, 0], {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'});
  const focus = interpolate(frame, [Math.max(impact - 14, 0), impact], [0, 1], {
    extrapolateLeft: 'clamp', extrapolateRight: 'clamp', easing: Easing.out(Easing.cubic),
  });
  const pulse = frame >= impact ? 1 + 0.025 * Math.sin((frame - impact) / 5) : 1;
  const spot = interpolate(focus, [0, 1], [86, 52]);
  const flash = frame >= impact ? Math.max(0, 0.28 - (frame - impact) * 0.04) : 0;
  const t = (scene.start + frame) / fps;
  const size = interpolate(focus, [0, 1], [900, 560]) * pulse;

  const corner = (rot: number) => (
    <path d="M0 40V0H40" fill="none" stroke={color} strokeWidth={6} strokeLinecap="round" transform={`rotate(${rot} ${size / 2} ${size / 2})`} />
  );

  return (
    <AbsoluteFill style={{pointerEvents: 'none', opacity: exit}}>
      <AbsoluteFill style={{background: `radial-gradient(ellipse ${spot}% ${spot * 0.7}% at 50% 46%, transparent 0%, rgba(0,0,0,0.62) 100%)`}} />
      <div style={{position: 'absolute', left: 0, right: 0, top: 0, height: BAR * bars, background: '#000'}} />
      <div style={{position: 'absolute', left: 0, right: 0, bottom: 0, height: BAR * bars, background: '#000'}} />
      <div style={{position: 'absolute', left: 54, top: 44 + (1 - bars) * -60, fontFamily: MONO, fontSize: 34, fontWeight: 800, color: '#fff', opacity: bars}}>
        <span style={{color: '#FF3B3B', opacity: Math.floor(frame / 12) % 2 === 0 ? 1 : 0.15}}>●</span> REC
      </div>
      <div style={{position: 'absolute', right: 54, top: 48 + (1 - bars) * -60, fontFamily: MONO, fontSize: 30, color: '#fff', opacity: 0.85 * bars}}>
        {`00:${pad2(t / 60)}:${pad2(t % 60)}:${pad2((t % 1) * fps)}`}
      </div>

      {/* odak çerçevesi */}
      <div style={{position: 'absolute', left: 540 - size / 2, top: 880 - size / 2, width: size, height: size, opacity: focus}}>
        <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`} style={{overflow: 'visible'}}>
          {corner(0)}
          <g transform={`translate(${size} 0) scale(-1 1)`}>{corner(0)}</g>
          <g transform={`translate(0 ${size}) scale(1 -1)`}>{corner(0)}</g>
          <g transform={`translate(${size} ${size}) scale(-1 -1)`}>{corner(0)}</g>
          <path d={`M${size / 2 - 22} ${size / 2}H${size / 2 + 22}M${size / 2} ${size / 2 - 22}V${size / 2 + 22}`} stroke={color} strokeWidth={3} opacity={0.8} />
        </svg>
      </div>
      <AbsoluteFill style={{background: '#fff', opacity: flash}} />
    </AbsoluteFill>
  );
};
