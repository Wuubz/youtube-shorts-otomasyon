import React from 'react';
import {AbsoluteFill, Easing, Img, interpolate, staticFile, useCurrentFrame} from 'remotion';

const W = 1080;
const H = 1920;

type Camera = {s: number; ox: number; oy: number};

// Kamera penceresi görselin (ox, oy) noktasından başlar ve W/s x H/s boyutundadır (FFmpeg zoompan ile aynı mantık).
const camera = (effect: string, p: number, zmax: number): Camera => {
  const amp = zmax - 1;
  const center = (s: number) => ({ox: (W - W / s) / 2, oy: (H - H / s) / 2});
  switch (effect) {
    case 'zoom_out': {
      const s = zmax - amp * p;
      return {s, ...center(s)};
    }
    case 'push_right': {
      const s = 1.16 + 0.06 * p;
      return {s, ox: (W - W / s) * p, oy: center(s).oy};
    }
    case 'push_left': {
      const s = 1.16 + 0.06 * p;
      return {s, ox: (W - W / s) * (1 - p), oy: center(s).oy};
    }
    case 'drift_up': {
      const s = 1.16 + 0.06 * p;
      return {s, ox: center(s).ox, oy: (H - H / s) * (1 - p)};
    }
    default: {
      const s = 1 + amp * p;
      return {s, ...center(s)};
    }
  }
};

type Entry = {opacity: number; transform?: string; clipPath?: string; filter?: string; dip?: {color: string; alpha: number}};

// Gelen sahnenin ilk `xfade` karesinde uygulanan geçiş (FFmpeg xfade adları aşağıdaki gruplara eşlenir).
const entry = (kind: string, t: number, frame: number): Entry => {
  const e = Easing.inOut(Easing.cubic)(t);
  switch (kind) {
    case 'fadeblack':
      return {opacity: t, dip: {color: '0,0,0', alpha: Math.sin(Math.PI * t) * 0.9}};
    case 'fadewhite':
      return {opacity: t, dip: {color: '255,255,255', alpha: Math.sin(Math.PI * t) * 0.85}};
    case 'circleopen':
    case 'radial':
      return {opacity: 1, clipPath: `circle(${e * 1300}px at 50% 50%)`};
    case 'circleclose':
      return {opacity: 1, clipPath: `circle(${e * 1300}px at 50% 85%)`};
    case 'slideleft':
    case 'smoothleft':
      return {opacity: 1, transform: `translateX(${(1 - e) * W}px)`};
    case 'slideright':
    case 'smoothright':
      return {opacity: 1, transform: `translateX(${-(1 - e) * W}px)`};
    case 'slideup':
    case 'smoothup':
      return {opacity: 1, transform: `translateY(${(1 - e) * H}px)`};
    case 'wipeleft':
      return {opacity: 1, clipPath: `inset(0 0 0 ${(1 - e) * 100}%)`};
    case 'wiperight':
      return {opacity: 1, clipPath: `inset(0 ${(1 - e) * 100}% 0 0)`};
    case 'hblur':
      return {opacity: t, filter: `blur(${(1 - t) * 22}px)`};
    case 'pixelize':
    case 'distance':
      return {opacity: t, transform: `translateX(${(1 - t) * 26 * Math.sin(frame * 2.4)}px)`};
    default: // dissolve, fade, fadegrays
      return {opacity: t};
  }
};

export const SceneLayer: React.FC<{
  image: string;
  effect: string;
  zoomMax: number;
  durationInFrames: number; // sahne + geçiş payı
  transition: string | null; // bu sahneye giriş geçişi (ilk sahnede null)
  xfade: number;
}> = ({image, effect, zoomMax, durationInFrames, transition, xfade}) => {
  const frame = useCurrentFrame();
  const p = interpolate(frame, [0, Math.max(durationInFrames - 1, 1)], [0, 1], {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
    easing: Easing.inOut(Easing.sin), // (1 - cos(pi p)) / 2: kamera hareketi başta ve sonda yumuşar
  });
  const {s, ox, oy} = camera(effect, p, zoomMax);

  const t = transition && xfade > 0 ? interpolate(frame, [0, xfade], [0, 1], {extrapolateRight: 'clamp'}) : 1;
  const en = entry(transition ?? 'fade', t, frame);

  return (
    <>
      <AbsoluteFill
        style={{
          opacity: en.opacity,
          transform: en.transform,
          clipPath: en.clipPath,
          filter: en.filter,
          overflow: 'hidden',
          backgroundColor: '#000',
        }}
      >
        <Img
          src={staticFile(image)}
          style={{
            position: 'absolute',
            left: 0,
            top: 0,
            width: W,
            height: H,
            objectFit: 'cover',
            transformOrigin: '0 0',
            transform: `translate(${-ox * s}px, ${-oy * s}px) scale(${s})`,
            willChange: 'transform',
          }}
        />
      </AbsoluteFill>
      {en.dip && en.dip.alpha > 0.01 ? (
        <AbsoluteFill style={{backgroundColor: `rgba(${en.dip.color},${en.dip.alpha})`}} />
      ) : null}
    </>
  );
};
