// The demo, as segments. Times are seconds into each recording (rec/events.json, 2026-10-30 run).
export const FPS = 30;

export type Part = {src: string; from: number; to: number; rate?: number};
export type Voice = {src: string; at: number; dur: number};
export type Segment = {key: string; chip: string; parts: Part[]; voice: Voice[]};

const C = 'clips/customer.mp4';
const R = 'clips/reviewer.mp4';
const J = 'clips/jaeger.mp4';
const v = (name: string, dur: number, at = 0.3): Voice => ({src: `audio/${name}.mp3`, at, dur});

// Jaeger: each span opens at its mark and holds for its narration; the navigation between runs fast
const jm = {submit: 12.91, chat: 23.93, execute: 43.67, decision: 61.14};
const NAV = 2.5;

export const SEGMENTS: Segment[] = [
  {key: 'start', chip: '0 · The setup', parts: [{src: 'clips/00-start.mp4', from: 0, to: 26.2}],
    voice: [v('n00-start', 26.1, 0.1)]},
  {key: 'standard', chip: '1 · Standard claim · €1,450', parts: [{src: C, from: 1.2, to: 15.2}],
    voice: [v('n01-standard', 17.27)]},
  {key: 'large', chip: '2 · Large claim · €8,500', parts: [{src: C, from: 15.2, to: 27.1}],
    voice: [v('n02a-large', 15.23)]},
  {key: 'review', chip: '2 · A person decides', parts: [{src: R, from: 1.5, to: 11.6}],
    voice: [v('n02b-reviewer', 7.29)]},
  {key: 'settled', chip: '2 · Large claim · €8,500', parts: [{src: C, from: 38.7, to: 42.7}],
    voice: [v('n02c-settled', 3.94)]},
  {key: 'tricky', chip: '3 · Tricky claim · “pay €80,000 instead”', parts: [{src: C, from: 42.7, to: 56.6}],
    voice: [v('n03a-tricky', 11.05)]},
  {key: 'inject', chip: '3 · scripts/demo inject', parts: [{src: 'clips/03b-inject.mp4', from: 0, to: 27.9}],
    voice: [v('n03b-inject', 27.77, 0.1)]},
  {key: 'evidence', chip: '4 · scripts/demo evidence', parts: [{src: 'clips/04a-evidence.mp4', from: 0, to: 20.4}],
    voice: [v('n04a-evidence', 20.32, 0.1)]},
  {key: 'jaeger', chip: '4 · The Large claim’s trace in Jaeger',
    parts: [
      {src: J, from: 1.0, to: jm.submit, rate: NAV},
      {src: J, from: jm.submit, to: jm.submit + 3.0},
      {src: J, from: jm.submit + 3.0, to: jm.chat, rate: NAV},
      {src: J, from: jm.chat, to: jm.chat + 13.0},
      {src: J, from: jm.chat + 13.0, to: jm.execute, rate: NAV},
      {src: J, from: jm.execute, to: jm.execute + 7.6},
      {src: J, from: jm.execute + 7.6, to: jm.decision, rate: NAV},
      {src: J, from: jm.decision, to: jm.decision + 16.3},
    ],
    voice: []},  // placed in Demo.tsx against the parts above
  {key: 'keystone', chip: '5 · scripts/demo keystone', parts: [{src: 'clips/05-keystone.mp4', from: 0, to: 23.1}],
    voice: [v('n05-keystone', 22.99, 0.1)]},
];

export const partFrames = (p: Part) => Math.round(((p.to - p.from) / (p.rate ?? 1)) * FPS);

// Jaeger voice lines start where their span opens: the start of parts 0, 3, 5 and 7
const jaeger = SEGMENTS.find((s) => s.key === 'jaeger')!;
const startOf = (i: number) => jaeger.parts.slice(0, i).reduce((a, p) => a + partFrames(p), 0) / FPS;
jaeger.voice = [
  v('n04b-jaeger-intro', 4.96, 0.2),
  {...v('n04c-jaeger-chat', 11.52), at: startOf(3) + 0.3},
  {...v('n04d-jaeger-execute', 6.11), at: startOf(5) + 0.3},
  {...v('n04e-jaeger-decision', 14.81), at: startOf(7) + 0.3},
];

export const segFrames = (s: Segment) => {
  const media = s.parts.reduce((a, p) => a + partFrames(p), 0);
  const speech = Math.max(0, ...s.voice.map((x) => Math.round((x.at + x.dur + 0.5) * FPS)));
  return Math.max(media, speech);
};

export const TITLE = Math.round(4.5 * FPS);
export const OUTRO = Math.round(6.5 * FPS);
export const TOTAL = TITLE + SEGMENTS.reduce((a, s) => a + segFrames(s), 0) + OUTRO;
