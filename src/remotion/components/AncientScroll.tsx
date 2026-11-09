import React from 'react';
import {AbsoluteFill, Easing, interpolate, spring, useCurrentFrame, useVideoConfig} from 'remotion';
import type {SceneSpec} from '../types';

const SERIF = '"DejaVu Serif", "Liberation Serif", Georgia, "Times New Roman", serif';
const CY = 800;
const WIDTH = 820;
const FULL_HEIGHT = 620;

// Antik parşömen: açılır, anahtar ifade mürekkeple yazılır ve `impact` karesinde mühür basılır.
// Bu sahnede anahtar ifadeyi parşömen taşıdığı için ayrıca ekran vuruşu çizilmez; sarsıntı mühür anında olur.
export const AncientScroll: React.FC<{scene: SceneSpec; frames: number}> = ({scene, frames}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const impact = scene.impact;

  const unroll = spring({frame, fps, config: {damping: 16, stiffness: 90, mass: 0.9}});
  const height = interpolate(Math.min(unroll, 1.04), [0, 1], [70, FULL_HEIGHT]);
  const exit = interpolate(frame, [frames - 8, frames], [1, 0], {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'});

  const ink = interpolate(frame, [Math.round(impact * 0.35), impact - 2], [0, 100], {
    extrapolateLeft: 'clamp', extrapolateRight: 'clamp', easing: Easing.inOut(Easing.quad),
  });
  const fall = interpolate(frame, [Math.max(impact - 9, 0), impact], [0, 1], {
    extrapolateLeft: 'clamp', extrapolateRight: 'clamp', easing: Easing.in(Easing.cubic),
  });
  const sealBounce = spring({frame: frame - impact, fps, config: {damping: 7, stiffness: 280, mass: 0.5}});
  const sealScale = frame < impact ? interpolate(fall, [0, 1], [3.2, 1]) : interpolate(sealBounce, [0, 1], [1.22, 1]);
  const sealOpacity = interpolate(fall, [0, 0.4], [0, 1], {extrapolateRight: 'clamp'});
  const flash = frame >= impact ? Math.max(0, 1 - (frame - impact) / 10) : 0;

  const text = scene.keyPhrase || scene.words.map((w) => w.t).slice(0, 3).join(' ');
  const rod = (top: number) => (
    <div
      style={{
        position: 'absolute', left: -34, right: -34, top, height: 46, borderRadius: 23,
        background: 'linear-gradient(to bottom, #8a5a2b, #5b3718 55%, #3a220e)', boxShadow: '0 10px 18px rgba(0,0,0,0.5)',
      }}
    />
  );

  return (
    <AbsoluteFill style={{pointerEvents: 'none', opacity: exit}}>
      <AbsoluteFill style={{background: 'rgba(8,4,0,0.45)', opacity: Math.min(unroll, 1)}} />
      <div style={{position: 'absolute', left: (1080 - WIDTH) / 2, top: CY - height / 2, width: WIDTH, height}}>
        <div
          style={{
            position: 'absolute', inset: 0, overflow: 'hidden',
            background: 'radial-gradient(ellipse at 50% 45%, #f0deb0 0%, #e2c88c 62%, #c79f5e 100%)',
            boxShadow: 'inset 0 0 90px rgba(110,60,10,0.55), 0 24px 60px rgba(0,0,0,0.55)',
          }}
        >
          <div style={{position: 'absolute', inset: 22, border: '3px solid rgba(110,60,10,0.45)'}} />
          <div style={{position: 'absolute', left: 0, right: 0, top: '50%', transform: 'translateY(-54%)', textAlign: 'center', padding: '0 60px'}}>
            <div style={{fontFamily: SERIF, fontSize: 30, letterSpacing: 10, color: 'rgba(90,50,10,0.75)', marginBottom: 22}}>✦ ✦ ✦</div>
            <div
              style={{
                fontFamily: SERIF, fontStyle: 'italic', fontWeight: 800, fontSize: text.length > 14 ? 84 : 104, lineHeight: 1.08,
                color: '#3b2412', textTransform: 'capitalize', clipPath: `inset(0 ${100 - ink}% 0 0)`,
                textShadow: '0 2px 0 rgba(255,235,190,0.6)',
              }}
            >
              {text}
            </div>
            <div style={{width: 360, height: 3, background: 'rgba(90,50,10,0.55)', margin: '28px auto 0', clipPath: `inset(0 ${100 - ink}% 0 0)`}} />
          </div>
        </div>
        {rod(-24)}
        {rod(height - 22)}

        {/* mum mühür */}
        <div
          style={{
            position: 'absolute', right: 70, bottom: 26, width: 170, height: 170, marginBottom: -20,
            opacity: sealOpacity, transform: `scale(${sealScale}) rotate(${interpolate(fall, [0, 1], [-30, -8])}deg)`,
          }}
        >
          <div
            style={{
              position: 'absolute', inset: 0, borderRadius: '50%',
              background: 'radial-gradient(circle at 35% 30%, #e0443a, #a3151a 60%, #6d0b10)',
              boxShadow: '0 8px 18px rgba(0,0,0,0.55), inset 0 0 14px rgba(0,0,0,0.4)',
            }}
          />
          <div style={{position: 'absolute', inset: 20, borderRadius: '50%', border: '5px solid rgba(255,190,170,0.5)'}} />
          <div style={{position: 'absolute', inset: 0, display: 'flex', alignItems: 'center', justifyContent: 'center', fontFamily: SERIF, fontSize: 78, color: 'rgba(255,200,180,0.7)'}}>✦</div>
          <div style={{position: 'absolute', inset: -28 * (1 - flash), borderRadius: '50%', border: '4px solid rgba(255,120,100,0.8)', opacity: flash}} />
        </div>
      </div>
    </AbsoluteFill>
  );
};
