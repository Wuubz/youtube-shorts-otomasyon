import React from 'react';
import {AbsoluteFill, Easing, interpolate, useCurrentFrame} from 'remotion';
import type {SceneSpec} from '../types';

const BAR = 120;

// Sinematik odak: sinema şeridi çubukları, daralan ışık huzmesi ve `impact` karesinde oturan kamera odak çerçevesi.
export const DramaticFocus: React.FC<{scene: SceneSpec; frames: number; color: string}> = ({scene, frames, color}) => {
  const frame = useCurrentFrame();
  const impact = scene.impact;

  const bars = interpolate(frame, [0, 10], [0, 1], {extrapolateRight: 'clamp', easing: Easing.out(Easing.cubic)});
  const exit = interpolate(frame, [frames - 8, frames], [1, 0], {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'});
  const focus = interpolate(frame, [Math.max(impact - 14, 0), impact], [0, 1], {
    extrapolateLeft: 'clamp', extrapolateRight: 'clamp', easing: Easing.out(Easing.cubic),
  });
  const pulse = 1;
  const spot = interpolate(focus, [0, 1], [86, 52]);
  const size = interpolate(focus, [0, 1], [900, 560]) * pulse;

  const corner = (rot: number) => (
    <path d="M0 40V0H40" fill="none" stroke={color} strokeWidth={6} strokeLinecap="round" transform={`rotate(${rot} ${size / 2} ${size / 2})`} />
  );

  return (
    <AbsoluteFill style={{pointerEvents: 'none', opacity: exit}}>
      <AbsoluteFill style={{background: `radial-gradient(ellipse ${spot}% ${spot * 0.7}% at 50% 46%, transparent 0%, rgba(0,0,0,0.62) 100%)`}} />
      <div style={{position: 'absolute', left: 0, right: 0, top: 0, height: BAR * bars, background: '#000'}} />
      <div style={{position: 'absolute', left: 0, right: 0, bottom: 0, height: BAR * bars, background: '#000'}} />
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
    </AbsoluteFill>
  );
};
