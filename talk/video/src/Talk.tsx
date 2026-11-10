import React from 'react';
import {AbsoluteFill, Audio, Freeze, Img, OffthreadVideo, Sequence, staticFile} from 'remotion';
import {FPS, partFrames, SEGMENTS} from './segments';
import manifest from '../public/talk/manifest.json';

// The whole talk: each slide of the deck held for its speaker notes, read by two generic voices;
// on slide 23 the narration runs over the recorded demo instead. public/talk/manifest.json is
// written by the narration script, one entry per voiced block, in order.
type Block = {id: string; slide: number; who: string; seg: string | null; dur: number};
const BLOCKS = manifest as Block[];

const GAP = 0.35;   // between two blocks on the same slide
const PAD = 0.7;    // after a slide's last block
const FONT = '"Helvetica Neue", Helvetica, Arial, sans-serif';
const MAG = '#f472b6';

const f = (s: number) => Math.round(s * FPS);

// consecutive blocks with the same slide and demo segment share one screen
type Screen = {slide: number; seg: string | null; blocks: Block[]};
const SCREENS: Screen[] = [];
for (const b of BLOCKS) {
  const last = SCREENS[SCREENS.length - 1];
  if (last && last.slide === b.slide && last.seg === b.seg) last.blocks.push(b);
  else SCREENS.push({slide: b.slide, seg: b.seg, blocks: [b]});
}

const segment = (key: string) => SEGMENTS.find((s) => s.key === key)!;
const media = (key: string) => segment(key).parts.reduce((a, p) => a + partFrames(p), 0);
const speech = (s: Screen) => s.blocks.reduce((a, b) => a + f(b.dur + GAP), 0) + f(PAD - GAP);
const screenFrames = (s: Screen) => Math.max(speech(s), s.seg ? media(s.seg) : 0);

export const TALK_TOTAL = SCREENS.reduce((a, s) => a + screenFrames(s), 0);

const Speaker: React.FC<{who: string}> = ({who}) => (
  <div style={{position: 'absolute', right: 24, top: 20, background: 'rgba(11,27,46,0.8)', color: '#fff',
    font: `600 22px ${FONT}`, padding: '8px 16px', borderRadius: 8, borderLeft: `5px solid ${MAG}`}}>
    {who === 'WILLIAM' ? 'William' : 'Kasper'}
  </div>
);

const Demo: React.FC<{seg: string; len: number}> = ({seg, len}) => {
  const parts = segment(seg).parts;
  let at = 0;
  const pieces = parts.map((p, i) => {
    const n = partFrames(p);
    const el = (
      <Sequence key={i} from={at} durationInFrames={n}>
        <OffthreadVideo src={staticFile(p.src)} muted playbackRate={p.rate ?? 1}
          trimBefore={f(p.from)} trimAfter={f(p.to)} />
      </Sequence>
    );
    at += n;
    return el;
  });
  const last = parts[parts.length - 1];
  return (
    <AbsoluteFill style={{background: '#0d1117'}}>
      {pieces}
      {len > at ? (
        <Sequence from={at} durationInFrames={len - at}>
          <Freeze frame={0}>
            <OffthreadVideo src={staticFile(last.src)} muted trimBefore={f(last.to) - 2} />
          </Freeze>
        </Sequence>
      ) : null}
    </AbsoluteFill>
  );
};

const ScreenView: React.FC<{s: Screen; len: number}> = ({s, len}) => {
  let at = 0;
  return (
    <AbsoluteFill style={{background: '#000'}}>
      {s.seg ? <Demo seg={s.seg} len={len} /> :
        <Img src={staticFile(`talk/slides/s-${String(s.slide).padStart(2, '0')}.png`)} />}
      {s.blocks.map((b) => {
        const from = at;
        at += f(b.dur + GAP);
        return (
          <Sequence key={b.id} from={from} durationInFrames={Math.max(1, (b === s.blocks[s.blocks.length - 1] ? len : at) - from)}>
            <Audio src={staticFile(`talk/audio/${b.id}.mp3`)} />
            <Speaker who={b.who} />
          </Sequence>
        );
      })}
    </AbsoluteFill>
  );
};

export const Talk: React.FC = () => {
  let at = 0;
  return (
    <AbsoluteFill style={{background: '#000'}}>
      {SCREENS.map((s, i) => {
        const len = screenFrames(s);
        const el = <Sequence key={i} from={at} durationInFrames={len}><ScreenView s={s} len={len} /></Sequence>;
        at += len;
        return el;
      })}
    </AbsoluteFill>
  );
};
