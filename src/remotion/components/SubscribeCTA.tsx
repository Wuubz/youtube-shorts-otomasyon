import React from 'react';
import {interpolate, spring, useCurrentFrame, useVideoConfig} from 'remotion';
import type {Theme} from '../theme';

const SHOW_SECONDS = 3;

// Son 3 saniyede alt-ortada beliren abone ol rozeti (altyazı ile Shorts arayüzü arasında kalır).
export const SubscribeCTA: React.FC<{theme: Theme; text: string}> = ({theme, text}) => {
  const frame = useCurrentFrame();
  const {fps, durationInFrames} = useVideoConfig();
  const startFrame = durationInFrames - SHOW_SECONDS * fps;
  if (frame < startFrame) {
    return null;
  }
  const local = frame - startFrame;
  const p = spring({frame: local, fps, config: {damping: 11, stiffness: 190, mass: 0.7}});
  const pulse = 1 + 0.035 * Math.sin(local / 5);
  const fade = interpolate(frame, [durationInFrames - 6, durationInFrames], [1, 0], {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
  });
  const color = theme.card.accent;

  return (
    <div style={{position: 'absolute', left: 0, right: 0, bottom: 330, display: 'flex', justifyContent: 'center'}}>
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 18,
          padding: '20px 44px',
          borderRadius: 999,
          background: `linear-gradient(135deg, ${color}, ${theme.progress})`,
          boxShadow: `0 10px 34px rgba(0,0,0,0.45), 0 0 30px ${color}88`,
          color: '#fff',
          fontFamily: theme.fontFamily,
          fontWeight: 800,
          fontSize: 48,
          letterSpacing: 3,
          textShadow: '0 2px 8px rgba(0,0,0,0.55)',
          opacity: Math.min(p, 1) * fade,
          transform: `translateY(${interpolate(p, [0, 1], [80, 0])}px) scale(${interpolate(p, [0, 1], [0.7, 1]) * pulse})`,
        }}
      >
        <svg width="46" height="46" viewBox="0 0 24 24" fill="#fff" style={{transform: `rotate(${Math.sin(local / 3) * 12}deg)`}}>
          <path d="M12 22a2.5 2.5 0 0 0 2.45-2h-4.9A2.5 2.5 0 0 0 12 22zm7-6V11a7 7 0 0 0-5.5-6.85V3.5a1.5 1.5 0 0 0-3 0v.65A7 7 0 0 0 5 11v5l-2 2v1h18v-1l-2-2z" />
        </svg>
        <span>{text}</span>
      </div>
    </div>
  );
};
