import React from 'react';
import {AbsoluteFill, Audio, Freeze, interpolate, OffthreadVideo, Sequence, staticFile, useCurrentFrame} from 'remotion';
import {FPS, OUTRO, partFrames, SEGMENTS, segFrames, Segment, TITLE} from './segments';

const INK = '#0b1b2e';
const CYAN = '#38bdf8';
const MAG = '#f472b6';
const FONT = '"Helvetica Neue", Helvetica, Arial, sans-serif';

const fadeIn = (frame: number, len = 12) => interpolate(frame, [0, len], [0, 1], {extrapolateRight: 'clamp'});

const Chip: React.FC<{text: string}> = ({text}) => {
  const f = useCurrentFrame();
  return (
    <div style={{position: 'absolute', right: 36, bottom: 36, opacity: fadeIn(f) * 0.94,
      background: 'rgba(11,27,46,0.88)', color: '#fff', font: `600 30px ${FONT}`,
      padding: '14px 24px', borderRadius: 12, borderLeft: `6px solid ${MAG}`,
      boxShadow: '0 8px 30px rgba(0,0,0,0.35)'}}>
      {text}
    </div>
  );
};

const Clip: React.FC<{seg: Segment}> = ({seg}) => {
  let at = 0;
  const pieces = seg.parts.map((p, i) => {
    const len = partFrames(p);
    const el = (
      <Sequence key={i} from={at} durationInFrames={len}>
        <OffthreadVideo src={staticFile(p.src)} muted playbackRate={p.rate ?? 1}
          trimBefore={Math.round(p.from * FPS)} trimAfter={Math.round(p.to * FPS)} />
      </Sequence>
    );
    at += len;
    return el;
  });
  const total = segFrames(seg);
  const last = seg.parts[seg.parts.length - 1];
  // the narration runs longer than the action: hold the last frame
  const hold = total > at ? (
    <Sequence from={at} durationInFrames={total - at}>
      <Freeze frame={0}>
        <OffthreadVideo src={staticFile(last.src)} muted trimBefore={Math.round(last.to * FPS) - 2} />
      </Freeze>
    </Sequence>
  ) : null;
  return (
    <AbsoluteFill style={{background: '#0d1117'}}>
      {pieces}
      {hold}
      {seg.voice.map((x, i) => (
        <Sequence key={`v${i}`} from={Math.round(x.at * FPS)}>
          <Audio src={staticFile(x.src)} />
        </Sequence>
      ))}
      <Chip text={seg.chip} />
    </AbsoluteFill>
  );
};

const Title: React.FC = () => {
  const f = useCurrentFrame();
  return (
    <AbsoluteFill style={{background: INK, color: '#fff', fontFamily: FONT, justifyContent: 'center', padding: '0 160px'}}>
      <div style={{opacity: fadeIn(f, 15), font: `600 28px ${FONT}`, letterSpacing: '0.14em', color: MAG, textTransform: 'uppercase'}}>
        Verticalizing Platform Intelligence for Regulated Domains
      </div>
      <div style={{opacity: fadeIn(f - 6, 15), font: `700 120px ${FONT}`, marginTop: 24}}>Northstar, live</div>
      <div style={{opacity: fadeIn(f - 18, 15), font: `400 44px ${FONT}`, color: CYAN, marginTop: 28}}>
        A customer writes a claim. A model reads it. Agents prepare the payment. The platform decides what can be paid.
      </div>
    </AbsoluteFill>
  );
};

const Outro: React.FC = () => {
  const f = useCurrentFrame();
  const lines = ['The agent proposed.', 'The platform decided.', 'Every decision is in the trace.'];
  return (
    <AbsoluteFill style={{background: INK, color: '#fff', justifyContent: 'center', padding: '0 160px'}}>
      <Audio src={staticFile('audio/n06-outro.mp3')} />
      {lines.map((l, i) => (
        <div key={l} style={{opacity: fadeIn(f - i * 38, 12), font: `700 ${i === 2 ? 84 : 76}px ${FONT}`,
          color: i === 2 ? CYAN : '#fff', marginBottom: 18}}>{l}</div>
      ))}
    </AbsoluteFill>
  );
};

export const Demo: React.FC = () => {
  let at = TITLE;
  return (
    <AbsoluteFill style={{background: INK}}>
      <Sequence durationInFrames={TITLE}><Title /></Sequence>
      {SEGMENTS.map((s) => {
        const len = segFrames(s);
        const el = <Sequence key={s.key} from={at} durationInFrames={len}><Clip seg={s} /></Sequence>;
        at += len;
        return el;
      })}
      <Sequence from={at} durationInFrames={OUTRO}><Outro /></Sequence>
    </AbsoluteFill>
  );
};
