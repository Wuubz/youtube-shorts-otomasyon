import React from 'react';
import {Composition, type CalculateMetadataFunction} from 'remotion';
import {ShortsComposition} from './ShortsComposition';
import type {ShortsProps} from './types';

// Studio'da (npm run studio) önizleme için örnek değerler; gerçek render'da Python props dosyası bunların yerine geçer.
const defaultProps: ShortsProps = {
  channel_theme: 'cosmic',
  fps: 30,
  totalFrames: 900,
  scenes: [],
  effects: [],
  transitions: [],
  xfadeFrames: 12,
  zoomMax: 1.28,
  hookZoom: 1.38,
  cues: [
    {
      start: 0.3,
      end: 2.4,
      highlight: true,
      closing: false,
      words: [
        {t: 'THE', s: 0.3, e: 0.7, emph: false},
        {t: 'UNIVERSE', s: 0.7, e: 1.4, emph: false},
        {t: 'IS', s: 1.4, e: 1.7, emph: false},
        {t: '13.8', s: 1.7, e: 2.4, emph: true},
      ],
    },
  ],
  labels: [{start: 0.2, end: 3, text: 'Cosmic File #1'}],
  ctaText: 'SUBSCRIBE',
  seed: 7,
};

// Süre, ses uzunluğuna göre Python'dan gelen totalFrames ile belirlenir (varsayılan 900 kare / 30 sn yalnızca önizleme içindir).
const calculateMetadata: CalculateMetadataFunction<ShortsProps> = ({props}) => ({
  durationInFrames: Math.max(Math.round(props.totalFrames), 1),
  fps: props.fps,
});

export const Root: React.FC = () => (
  <Composition
    id="ShortsComposition"
    component={ShortsComposition}
    width={1080}
    height={1920}
    fps={30}
    durationInFrames={900}
    defaultProps={defaultProps}
    calculateMetadata={calculateMetadata}
  />
);
