import React from 'react';
import {interpolate, spring, useCurrentFrame, useVideoConfig} from 'remotion';
import type {Theme} from '../theme';
import type {Label} from '../types';

// Sahnenin zaman/yer/seri etiketi: sol üstte, kanal temasına göre cam, daktilo, rozet veya yuvarlak kart.
export const InfoCard: React.FC<{labels: Label[]; theme: Theme}> = ({labels, theme}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const t = frame / fps;
  const label = labels.find((l) => t >= l.start && t < l.end);
  if (!label) {
    return null;
  }
  const card = theme.card;
  const startFrame = Math.round(label.start * fps);
  const endFrame = Math.round(label.end * fps);
  const local = frame - startFrame;

  const p = spring({frame: local, fps, config: {damping: 14, stiffness: 170, mass: 0.7}});
  const exit = interpolate(frame, [endFrame - 8, endFrame], [1, 0], {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'});
  const x = interpolate(p, [0, 1], [-90, 0]);

  let text = label.text;
  let cursor = false;
  if (card.kind === 'typewriter') {
    const chars = Math.min(text.length, Math.floor(local / 1.6));
    text = text.slice(0, chars);
    cursor = chars < label.text.length || Math.floor(frame / 8) % 2 === 0;
  }
  const upper = card.kind === 'badge' || card.kind === 'quote' || card.kind === 'dark';

  const glass = card.kind === 'glass' || card.kind === 'dark';
  return (
    <div
      style={{
        position: 'absolute',
        left: 56,
        top: 250,
        maxWidth: 760,
        padding: card.kind === 'soft' ? '20px 38px' : '18px 32px',
        display: 'flex',
        alignItems: 'center',
        gap: 18,
        background: card.bg,
        border: `${card.kind === 'badge' ? 3 : 2}px solid ${card.border}`,
        borderRadius: card.radius,
        backdropFilter: glass ? 'blur(18px) saturate(1.4)' : undefined,
        boxShadow: card.kind === 'badge' ? `0 0 28px ${card.border}66` : '0 10px 30px rgba(0,0,0,0.35)',
        color: card.text,
        fontFamily: card.fontFamily,
        fontWeight: 700,
        fontSize: 44,
        letterSpacing: upper ? 2 : 0.5,
        textTransform: upper ? 'uppercase' : 'none',
        opacity: Math.min(p, 1) * exit,
        transform: `translateX(${x}px)`,
      }}
    >
      {card.kind === 'badge' ? (
        <div style={{width: 18, height: 18, background: card.accent, transform: 'rotate(45deg)', flex: 'none'}} />
      ) : (
        <div style={{width: 6, alignSelf: 'stretch', background: card.accent, borderRadius: 4, flex: 'none'}} />
      )}
      <div style={{whiteSpace: 'pre-wrap'}}>
        {text}
        {cursor ? <span style={{color: card.accent}}>▌</span> : null}
      </div>
    </div>
  );
};
