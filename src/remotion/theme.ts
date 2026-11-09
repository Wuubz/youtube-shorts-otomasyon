import type {ThemeName} from './types';

export type Anim = 'pop' | 'fade' | 'rise' | 'shake' | 'bounce';
export type CardKind = 'glass' | 'typewriter' | 'quote' | 'badge' | 'soft' | 'dark';

export type Theme = {
  fontFamily: string;
  fontSize: number;
  fontWeight: number;
  uppercase: boolean;
  letterSpacing: number;
  colors: string[]; // altyazı blokları bu renkleri sırayla kullanır
  highlight: string; // o an okunan kelime
  accent: string; // rakamlar, güç kelimeleri, kapanış sorusu
  outline: string;
  outlineWidth: number;
  shadow: string;
  box: string | null; // altyazı arkasındaki kutu (yoksa null)
  anim: Anim;
  bottom: number; // altyazının alt kenar boşluğu (Shorts arayüzünden uzak durmak için yüksek)
  grain: number; // renk tonu ve vinyet Python'da görsele işlenir (src/styles.py)
  glow: {color: string; opacity: number; at: string} | null;
  flicker: number; // eski film parlaklık titreşimi
  jolt: boolean; // sahne başlarında kısa, hafif sarsıntı (sürekli titreme değil)
  dust: boolean; // eski film tozu ve çizikleri
  filmFrame: boolean;
  particles: {count: number; color: string; min: number; max: number; speed: number} | null;
  progress: string;
  card: {
    kind: CardKind;
    bg: string;
    border: string;
    text: string;
    accent: string;
    radius: number;
    fontFamily: string;
  };
};

const SANS = '"DejaVu Sans", "Liberation Sans", "Segoe UI", Arial, sans-serif';
const BOLD_SANS = '"Liberation Sans", "DejaVu Sans", "Arial Black", Arial, sans-serif';
const SERIF = '"DejaVu Serif", "Liberation Serif", Georgia, "Times New Roman", serif';
const MONO = '"DejaVu Sans Mono", "Liberation Mono", Consolas, "Courier New", monospace';

export const THEMES: Record<ThemeName, Theme> = {
  // Space Paradox: derin uzay moru, neon camgöbeği kelime parlaması, mavi ışıma, parçacıklar
  cosmic: {
    fontFamily: SANS, fontSize: 86, fontWeight: 800, uppercase: true, letterSpacing: 1,
    colors: ['#FFFFFF', '#E9E4FF'], highlight: '#22E5FF', accent: '#FFD84A',
    outline: '#1B0A4A', outlineWidth: 14, shadow: '0 6px 22px rgba(80,40,200,0.65)',
    box: null, anim: 'pop', bottom: 520,
    grain: 4,
    glow: {color: 'rgba(60,140,255,0.35)', opacity: 0.55, at: '50% 28%'},
    flicker: 0, jolt: false, dust: false, filmFrame: false,
    particles: {count: 16, color: '#BFEFFF', min: 2, max: 6, speed: 0.9},
    progress: '#22D3EE',
    card: {kind: 'glass', bg: 'rgba(110,70,220,0.22)', border: 'rgba(170,150,255,0.55)', text: '#FFFFFF', accent: '#22E5FF', radius: 26, fontFamily: SANS},
  },
  // Forgotten Echoes: sepya ve antik altın, eski film vinyeti, daktilo kartları
  chronicle: {
    fontFamily: SERIF, fontSize: 82, fontWeight: 700, uppercase: false, letterSpacing: 0,
    colors: ['#F5E6C8', '#EBD9B0'], highlight: '#E8B84A', accent: '#E0553C',
    outline: '#241505', outlineWidth: 12, shadow: '0 5px 18px rgba(0,0,0,0.7)',
    box: null, anim: 'fade', bottom: 500,
    grain: 7, glow: null,
    flicker: 0, jolt: false, dust: false, filmFrame: false,
    particles: null,
    progress: '#D6A24A',
    card: {kind: 'typewriter', bg: 'rgba(36,24,10,0.78)', border: 'rgba(214,162,74,0.7)', text: '#F5E6C8', accent: '#D6A24A', radius: 6, fontFamily: MONO},
  },
  // Psych Decoded: yüksek kontrast siyah-kırmızı/amber, koyu camgöbeği kartlar, agresif zıplama
  noir: {
    fontFamily: BOLD_SANS, fontSize: 80, fontWeight: 800, uppercase: true, letterSpacing: 1,
    colors: ['#FFFFFF', '#FFFFFF'], highlight: '#FFB000', accent: '#EF4444',
    outline: '#000000', outlineWidth: 6, shadow: '0 4px 14px rgba(0,0,0,0.8)',
    box: 'rgba(0,0,0,0.62)', anim: 'bounce', bottom: 540,
    grain: 6, glow: null,
    flicker: 0, jolt: false, dust: false, filmFrame: false,
    particles: null,
    progress: '#EF4444',
    card: {kind: 'quote', bg: 'rgba(5,48,58,0.86)', border: '#18B7C9', text: '#E8FBFF', accent: '#FFB000', radius: 10, fontFamily: BOLD_SANS},
  },
  // Mythic Realms: parlak altın ve şimşek mavisi, ilahi parıltı, başlık rozetleri
  epic: {
    fontFamily: SERIF, fontSize: 88, fontWeight: 800, uppercase: true, letterSpacing: 2,
    colors: ['#FFE08A', '#FFFFFF'], highlight: '#5CC8FF', accent: '#FFC83D',
    outline: '#3A1E00', outlineWidth: 14, shadow: '0 6px 24px rgba(255,170,40,0.55)',
    box: null, anim: 'pop', bottom: 500,
    grain: 4,
    glow: {color: 'rgba(255,200,80,0.55)', opacity: 0.6, at: '50% 8%'},
    flicker: 0, jolt: false, dust: false, filmFrame: false,
    particles: {count: 12, color: '#FFD878', min: 2, max: 5, speed: 0.7},
    progress: '#F5B83D',
    card: {kind: 'badge', bg: 'linear-gradient(135deg, rgba(70,40,5,0.9), rgba(140,90,10,0.85))', border: '#FFC83D', text: '#FFE9B0', accent: '#5CC8FF', radius: 14, fontFamily: SERIF},
  },
  // Never Look Behind: soğuk yeşil ve kan kırmızısı, koyu vinyet, sahne başlarında kısa sarsıntı
  horror: {
    fontFamily: SANS, fontSize: 84, fontWeight: 800, uppercase: true, letterSpacing: 1,
    colors: ['#F0F0F0', '#F0F0F0'], highlight: '#5CFFA0', accent: '#E01B1B',
    outline: '#100000', outlineWidth: 14, shadow: '0 6px 20px rgba(0,0,0,0.85)',
    box: null, anim: 'shake', bottom: 480,
    grain: 8, glow: null,
    flicker: 0, jolt: false, dust: false, filmFrame: false,
    particles: null,
    progress: '#B91C1C',
    card: {kind: 'dark', bg: 'rgba(10,10,10,0.78)', border: 'rgba(224,27,27,0.75)', text: '#F0F0F0', accent: '#E01B1B', radius: 8, fontFamily: SANS},
  },
  // Pure Paws Stories: sıcak pastel sarı ve beyaz, yuvarlak kartlar, neşeli zıplama
  warm: {
    fontFamily: SANS, fontSize: 90, fontWeight: 800, uppercase: false, letterSpacing: 0,
    colors: ['#FFFFFF', '#FFF3B0'], highlight: '#FF9EB5', accent: '#FFC94D',
    outline: '#7A3A1E', outlineWidth: 14, shadow: '0 6px 18px rgba(120,60,30,0.5)',
    box: null, anim: 'bounce', bottom: 540,
    grain: 0,
    glow: {color: 'rgba(255,220,150,0.5)', opacity: 0.4, at: '50% 20%'},
    flicker: 0, jolt: false, dust: false, filmFrame: false,
    particles: {count: 8, color: '#FFF1C7', min: 10, max: 26, speed: 0.35},
    progress: '#FB7185',
    card: {kind: 'soft', bg: 'rgba(255,243,176,0.92)', border: '#FFFFFF', text: '#6B3A1A', accent: '#FB7185', radius: 40, fontFamily: SANS},
  },
};

export const resolveTheme = (name: string): Theme => THEMES[name as ThemeName] ?? THEMES.cosmic;
