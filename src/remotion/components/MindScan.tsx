import React from 'react';
import {AbsoluteFill, interpolate, random, spring, useCurrentFrame, useVideoConfig} from 'remotion';
import type {SceneSpec} from '../types';

const CX = 540;
const CY = 800;
const RX = 320;
const RY = 390;
const NODES = 34;

type Node = {x: number; y: number};

// Nöron ağı: elips içinde sahne başına sabit düğümler, her düğüm en yakın iki komşusuna bağlanır
const buildGraph = (seed: number): {nodes: Node[]; edges: [number, number][]} => {
  const nodes: Node[] = [];
  for (let i = 0; i < NODES; i++) {
    const a = random(`n-a-${seed}-${i}`) * Math.PI * 2;
    const r = Math.sqrt(random(`n-r-${seed}-${i}`)) * 0.96;
    nodes.push({x: Math.cos(a) * r * RX, y: Math.sin(a) * r * RY});
  }
  const edges: [number, number][] = [];
  nodes.forEach((n, i) => {
    const near = nodes
      .map((m, j) => ({j, d: (m.x - n.x) ** 2 + (m.y - n.y) ** 2}))
      .filter((e) => e.j !== i)
      .sort((p, q) => p.d - q.d)
      .slice(0, 2);
    near.forEach((e) => {
      if (!edges.some(([a, b]) => (a === i && b === e.j) || (a === e.j && b === i))) {
        edges.push([i, e.j]);
      }
    });
  });
  return {nodes, edges};
};

// Beyin röntgeni / zihin taraması: `impact` karesinde merkezden yayılan aktivasyon dalgası düğümleri yakar.
export const MindScan: React.FC<{scene: SceneSpec; frames: number; color: string}> = ({scene, frames, color}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const impact = scene.impact;
  const {nodes, edges} = React.useMemo(() => buildGraph(scene.start), [scene.start]);

  const intro = spring({frame, fps, config: {damping: 15, stiffness: 110, mass: 0.8}});
  const exit = interpolate(frame, [frames - 8, frames], [1, 0], {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'});

  // dalga: impact'ten önce hazırlanır, merkezden dışa 26 px/kare yayılır; sonra yavaşça tekrarlar
  const wave = (frame - impact + 6) * 26;
  const lit = (n: Node, offset: number) => {
    const d = Math.hypot(n.x, n.y);
    const w = ((wave - offset) % 1500 + 1500) % 1500;
    const front = Math.exp(-(((d - w) / 90) ** 2));
    return Math.min(1, 0.18 + front + 0.1 * Math.sin(frame / 10 + d / 40));
  };

  const scanY = ((frame * 9) % (RY * 2 + 160)) - RY - 80;
  const percent = Math.min(100, Math.round(interpolate(frame, [4, Math.max(frames - 14, 12)], [0, 100], {extrapolateRight: 'clamp'})));
  const eeg = Array.from({length: 48}, (_, i) => {
    const x = (i / 47) * 560 - 280;
    const spike = Math.max(0, 1 - Math.abs(((i + frame * 0.7) % 16) - 8) / 1.4);
    return `${i === 0 ? 'M' : 'L'}${x.toFixed(1)} ${(Math.sin(i * 0.8 + frame / 5) * 8 - spike * 34).toFixed(1)}`;
  }).join(' ');

  return (
    <AbsoluteFill style={{pointerEvents: 'none', opacity: exit}}>
      <AbsoluteFill style={{background: 'rgba(0,12,22,0.5)', opacity: Math.min(intro, 1)}} />
      <svg
        viewBox={`${-540} ${-CY} 1080 1920`} width={1080} height={1920}
        style={{position: 'absolute', left: 0, top: 0, opacity: Math.min(intro, 1), transform: `scale(${interpolate(intro, [0, 1], [0.85, 1])})`, transformOrigin: `${CX}px ${CY}px`}}
      >
        <ellipse cx={0} cy={0} rx={RX + 18} ry={RY + 18} fill="rgba(20,180,220,0.07)" stroke={color} strokeWidth={3} strokeDasharray="14 10" opacity={0.85} />
        <path d={`M0 ${-RY}C-70 -120 70 120 0 ${RY}`} stroke={color} strokeWidth={2} fill="none" opacity={0.4} />
        {[-240, -120, 0, 120, 240].map((y) => (
          <line key={y} x1={-RX} x2={RX} y1={y} y2={y} stroke={color} strokeWidth={1} opacity={0.18} />
        ))}
        {edges.map(([a, b], i) => {
          const o = (lit(nodes[a], 0) + lit(nodes[b], 0)) / 2;
          return <line key={i} x1={nodes[a].x} y1={nodes[a].y} x2={nodes[b].x} y2={nodes[b].y} stroke={color} strokeWidth={1.2 + o * 2.2} opacity={0.15 + o * 0.7} />;
        })}
        {nodes.map((n, i) => {
          const o = lit(n, 0);
          return (
            <g key={i}>
              <circle cx={n.x} cy={n.y} r={11 + o * 14} fill={color} opacity={o * 0.28} />
              <circle cx={n.x} cy={n.y} r={4 + o * 5} fill={o > 0.7 ? '#FFFFFF' : color} opacity={0.4 + o * 0.6} />
            </g>
          );
        })}
        {/* tarama çubuğu */}
        <rect x={-RX - 20} y={scanY} width={(RX + 20) * 2} height={46} fill={`${color}33`} />
        <rect x={-RX - 20} y={scanY + 21} width={(RX + 20) * 2} height={4} fill={color} opacity={0.9} />
        <g transform={`translate(0 ${RY + 96})`}>
          <path d={eeg} stroke={color} strokeWidth={3} fill="none" opacity={0.9} />
        </g>
      </svg>
      <div style={{position: 'absolute', left: CX - RX, top: CY - RY - 86, fontFamily: '"DejaVu Sans Mono", Consolas, monospace', color, fontSize: 30, letterSpacing: 3, textShadow: `0 0 14px ${color}`}}>
        NEURAL SCAN {percent}%
      </div>
    </AbsoluteFill>
  );
};
