import React from 'react';
import {AbsoluteFill, random, useCurrentFrame} from 'remotion';
import type {Theme} from '../theme';

// Küçük bir SVG gürültü karosu: bir kez çizilir, her karede konumu kaydırılarak film tanesi taklit edilir (ucuz).
const GRAIN_TILE =
  "url(\"data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' width='256' height='256'>" +
  "<filter id='n'><feTurbulence type='fractalNoise' baseFrequency='0.9' numOctaves='2' stitchTiles='stitch'/>" +
  "<feColorMatrix type='saturate' values='0'/></filter><rect width='100%25' height='100%25' filter='url(%23n)'/></svg>\")";

// Arkadaki AI görselini sinematik bir fona çevirir: hafif karartma (üstteki grafikler okunsun), üst/alt koyulaşma
// (altyazı ve kamera derinliği), film tanesi. Renk tonu ve vinyet Python tarafında görsele zaten işlenmiştir.
export const CinematicVignette: React.FC<{theme: Theme}> = ({theme}) => {
  const frame = useCurrentFrame();
  return (
    <AbsoluteFill style={{pointerEvents: 'none'}}>
      <AbsoluteFill
        style={{
          background:
            'linear-gradient(to bottom, rgba(0,0,0,0.5) 0%, rgba(0,0,0,0.2) 22%, rgba(0,0,0,0.26) 60%, rgba(0,0,0,0.68) 100%)',
        }}
      />
      {theme.grain > 0 ? (
        <AbsoluteFill
          style={{
            backgroundImage: GRAIN_TILE,
            backgroundSize: '256px 256px',
            backgroundPosition: `${Math.floor(random(`gx${frame}`) * 256)}px ${Math.floor(random(`gy${frame}`) * 256)}px`,
            opacity: Math.max(theme.grain, 5) * 0.012,
          }}
        />
      ) : null}
    </AbsoluteFill>
  );
};
