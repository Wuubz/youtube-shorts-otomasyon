import React from 'react';
import {Easing, interpolate, spring, useCurrentFrame, useVideoConfig} from 'remotion';
import type {BadgeType} from '../types';

type Look = {glow: string; ring: string};
const LOOK: Record<Exclude<BadgeType, 'none'>, Look> = {
  warning: {glow: '#FFB000', ring: '#FF4D2E'},
  skull: {glow: '#E8E8E8', ring: '#E01B1B'},
  galaxy: {glow: '#8A7CFF', ring: '#22E5FF'},
  stoic_pillar: {glow: '#FFC83D', ring: '#FFE9B0'},
  broken_heart: {glow: '#FF4D6D', ring: '#FF9EB5'},
};

// Galaksi sarmalı: iki logaritmik kol, bir kez hesaplanır
const spiral = (turn: number): string => {
  const pts: string[] = [];
  for (let i = 0; i <= 60; i++) {
    const a = i * 0.16 + turn;
    const r = 4 + i * 0.7;
    pts.push(`${i === 0 ? 'M' : 'L'}${(50 + Math.cos(a) * r).toFixed(1)} ${(50 + Math.sin(a) * r * 0.62).toFixed(1)}`);
  }
  return pts.join(' ');
};
const ARM_A = spiral(0);
const ARM_B = spiral(Math.PI);

const Icon: React.FC<{type: Exclude<BadgeType, 'none'>; frame: number}> = ({type, frame}) => {
  const white = 'rgba(255,255,255,0.95)';
  switch (type) {
    case 'warning':
      return (
        <svg viewBox="0 0 100 100" width="100%" height="100%">
          <path d="M50 10 L93 86 H7 Z" fill="rgba(255,176,0,0.9)" stroke={white} strokeWidth="4" strokeLinejoin="round" />
          <rect x="46" y="34" width="8" height="30" rx="4" fill="#2b1500" />
          <circle cx="50" cy="73" r="5" fill="#2b1500" />
        </svg>
      );
    case 'skull':
      return (
        <svg viewBox="0 0 100 100" width="100%" height="100%">
          <path
            d="M50 10 C27 10 15 27 15 45 C15 57 21 64 30 68 V84 H70 V68 C79 64 85 57 85 45 C85 27 73 10 50 10 Z"
            fill="rgba(240,240,240,0.95)" stroke={white} strokeWidth="3"
          />
          <circle cx="35" cy="47" r="10" fill="#14141a" />
          <circle cx="65" cy="47" r="10" fill="#14141a" />
          <path d="M50 57 L43 70 H57 Z" fill="#14141a" />
          <path d="M40 84 V74 M50 84 V74 M60 84 V74" stroke="#14141a" strokeWidth="3" />
        </svg>
      );
    case 'galaxy':
      return (
        <svg viewBox="0 0 100 100" width="100%" height="100%" style={{transform: `rotate(${frame * 1.2}deg)`}}>
          <path d={ARM_A} fill="none" stroke="rgba(150,200,255,0.95)" strokeWidth="5" strokeLinecap="round" />
          <path d={ARM_B} fill="none" stroke="rgba(190,150,255,0.95)" strokeWidth="5" strokeLinecap="round" />
          <circle cx="50" cy="50" r="9" fill="#FFF6C8" />
          <circle cx="50" cy="50" r="15" fill="rgba(255,246,200,0.3)" />
        </svg>
      );
    case 'stoic_pillar':
      return (
        <svg viewBox="0 0 100 100" width="100%" height="100%">
          <rect x="14" y="86" width="72" height="8" rx="2" fill="rgba(255,225,150,0.95)" />
          <rect x="20" y="76" width="60" height="10" rx="2" fill="rgba(255,225,150,0.95)" />
          <rect x="29" y="28" width="42" height="48" fill="rgba(255,236,190,0.95)" />
          <path d="M38 28 V76 M50 28 V76 M62 28 V76" stroke="rgba(150,100,20,0.55)" strokeWidth="2.5" />
          <rect x="20" y="16" width="60" height="12" rx="2" fill="rgba(255,225,150,0.95)" />
          <rect x="14" y="8" width="72" height="8" rx="2" fill="rgba(255,225,150,0.95)" />
        </svg>
      );
    default: {
      // broken_heart: iki yarım kalp zikzak bir çatlaktan ayrılır
      const gap = 3 + 2 * Math.sin(frame / 7);
      const heart =
        'M50 90 C20 63 7 45 7 30 C7 16 19 8 31 8 C40 8 47 12 50 19 C53 12 60 8 69 8 C81 8 93 16 93 30 C93 45 80 63 50 90 Z';
      return (
        <svg viewBox="0 0 100 100" width="100%" height="100%">
          <defs>
            <clipPath id="hl"><polygon points="0,0 53,0 45,24 57,42 46,60 55,78 50,100 0,100" /></clipPath>
            <clipPath id="hr"><polygon points="53,0 100,0 100,100 50,100 55,78 46,60 57,42 45,24" /></clipPath>
          </defs>
          <g clipPath="url(#hl)" transform={`translate(${-gap} ${gap * 0.4}) rotate(${-gap} 30 60)`}>
            <path d={heart} fill="rgba(255,77,109,0.95)" stroke={white} strokeWidth="3" />
          </g>
          <g clipPath="url(#hr)" transform={`translate(${gap} ${-gap * 0.4}) rotate(${gap} 70 60)`}>
            <path d={heart} fill="rgba(255,77,109,0.95)" stroke={white} strokeWidth="3" />
          </g>
        </svg>
      );
    }
  }
};

// Sahnenin floating_badge simgesi: önce ekranın ortasında cam efektli, nabız gibi atan neon halkalarla belirir,
// sonra köşeye süzülüp küçük bir işaret olarak yüzmeye devam eder.
export const DynamicFocalBadge: React.FC<{badge: BadgeType; durationInFrames: number}> = ({badge, durationInFrames}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  if (badge === 'none') {
    return null;
  }
  const look = LOOK[badge];

  const appear = spring({frame: frame - 4, fps, config: {damping: 11, stiffness: 160, mass: 0.7}});
  const drift = interpolate(frame, [26, 50], [0, 1], {
    extrapolateLeft: 'clamp', extrapolateRight: 'clamp', easing: Easing.inOut(Easing.cubic),
  });
  const exit = interpolate(frame, [durationInFrames - 8, durationInFrames], [1, 0], {
    extrapolateLeft: 'clamp', extrapolateRight: 'clamp',
  });
  const cx = interpolate(drift, [0, 1], [540, 905]);
  const cy = interpolate(drift, [0, 1], [470, 390]) + Math.sin(frame / 14) * 5;
  const size = interpolate(drift, [0, 1], [230, 130]);
  const pulse = 1;
  const scale = Math.max(appear, 0) * pulse;

  return (
    <div style={{position: 'absolute', left: cx - size / 2, top: cy - size / 2, width: size, height: size, opacity: Math.min(appear, 1) * exit}}>
      <div
        style={{
          position: 'absolute', inset: 0, borderRadius: '50%', transform: `scale(${scale})`,
          background: 'radial-gradient(circle at 32% 26%, rgba(255,255,255,0.42), rgba(255,255,255,0.1) 55%, rgba(255,255,255,0.04) 100%)',
          border: `${Math.max(2, size * 0.01)}px solid rgba(255,255,255,0.55)`,
          boxShadow: `0 0 ${size * 0.22}px ${look.glow}aa, inset 0 0 ${size * 0.12}px rgba(255,255,255,0.25)`,
          padding: size * 0.2,
        }}
      >
        <Icon type={badge} frame={frame} />
      </div>
    </div>
  );
};
