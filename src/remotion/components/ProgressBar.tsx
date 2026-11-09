import React from 'react';
import {interpolate, useCurrentFrame, useVideoConfig} from 'remotion';

// Ekranın üstünde (Shorts arayüzünün ve altyazının dışında) videonun ilerleyişini gösteren ince çubuk.
export const ProgressBar: React.FC<{color: string}> = ({color}) => {
  const frame = useCurrentFrame();
  const {durationInFrames} = useVideoConfig();
  const width = interpolate(frame, [0, Math.max(durationInFrames - 1, 1)], [0, 100], {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
  });
  return (
    <div style={{position: 'absolute', left: 0, top: 96, width: '100%', height: 8, background: 'rgba(255,255,255,0.12)'}}>
      <div
        style={{
          width: `${width}%`,
          height: '100%',
          background: color,
          borderRadius: '0 6px 6px 0',
          boxShadow: `0 0 18px ${color}`,
        }}
      />
    </div>
  );
};
