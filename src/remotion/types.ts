// Python (src/remotion_bridge.py) ve Remotion arasındaki sözleşme: render_props.json bu şekle uyar.
export type ThemeName = 'cosmic' | 'chronicle' | 'noir' | 'epic' | 'horror' | 'warm';

export type Word = {
  t: string; // kelime metni
  s: number; // okunmaya başladığı an (sn, videonun başından)
  e: number; // okunması bittiği an (sn)
  emph: boolean; // rakam veya güç kelimesi: vurgu renginde gösterilir
};

export type Cue = {
  start: number; // altyazı bloğunun ekrana giriş anı (sn)
  end: number; // ekrandan çıkış anı (sn)
  words: Word[];
  highlight: boolean; // kelime zamanları güvenilirse o an okunan kelime parlatılır
  closing: boolean; // kapanıştaki yorum sorusu: daha büyük ve vurgu renginde
};

export type Label = {start: number; end: number; text: string};

export type SceneType = 'evidence_document' | 'radar_hud' | 'mind_scan' | 'ancient_scroll' | 'dramatic_focus' | 'plain';
export type BadgeType = 'warning' | 'skull' | 'galaxy' | 'stoic_pillar' | 'broken_heart' | 'none';

export type SceneWord = {t: string; s: number; e: number};

export type SceneSpec = {
  image: string; // public klasörüne göre dosya adı
  start: number; // başlangıç karesi
  frames: number; // sahnenin kendi süresi (kare)
  type: SceneType; // Gemini'nin seçtiği hareketli grafik katmanı
  impact: number; // animasyonun ekrana çarptığı kare (sahne başından); ses efekti aynı anda patlar
  badge: BadgeType;
  keyPhrase: string; // ekranda vurulacak şok ifade (boşsa yok)
  keyStart: number | null; // ifadenin söylendiği an (sn, videonun başından)
  keyEnd: number | null;
  words: SceneWord[]; // kanıt belgesinde altı çizilecek kelimeler (yalnızca evidence_document)
  sfx: string | null; // null: ses efekti yok
};

export type ShortsProps = {
  channel_theme: ThemeName;
  fps: number;
  totalFrames: number;
  scenes: SceneSpec[];
  effects: string[]; // sahne başına kamera hareketi: zoom_in | zoom_out | push_left | push_right | drift_up
  transitions: string[]; // sahne sınırı başına geçiş adı (n-1 eleman)
  xfadeFrames: number;
  zoomMax: number;
  hookZoom: number;
  cues: Cue[];
  labels: Label[];
  ctaText: string;
  seed: number;
};
