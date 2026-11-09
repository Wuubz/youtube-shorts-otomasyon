import React from 'react';
import type {Theme} from '../theme';
import type {SceneSpec} from '../types';
import {AncientScroll} from './AncientScroll';
import {DramaticFocus} from './DramaticFocus';
import {EvidenceOverlay} from './EvidenceOverlay';
import {MindScan} from './MindScan';
import {RadarHud} from './RadarHud';

// Sahne türüne göre hareketli grafik katmanını seçer (Sequence içinde çağrılır: kare sayısı sahne başından sayılır).
export const SceneOverlay: React.FC<{scene: SceneSpec; theme: Theme}> = ({scene, theme}) => {
  switch (scene.type) {
    case 'plain':
      return null;
    case 'evidence_document':
      return <EvidenceOverlay scene={scene} frames={scene.frames} />;
    case 'radar_hud':
      return <RadarHud scene={scene} frames={scene.frames} color={theme.highlight} />;
    case 'mind_scan':
      return <MindScan scene={scene} frames={scene.frames} color={theme.highlight} />;
    case 'ancient_scroll':
      return <AncientScroll scene={scene} frames={scene.frames} />;
    default:
      return <DramaticFocus scene={scene} frames={scene.frames} color={theme.highlight} />;
  }
};
