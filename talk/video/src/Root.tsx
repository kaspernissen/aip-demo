import React from 'react';
import {Composition} from 'remotion';
import {Demo} from './Demo';
import {FPS, TOTAL} from './segments';
import {Talk, TALK_TOTAL} from './Talk';

export const Root: React.FC = () => (
  <>
    <Composition id="NorthstarDemo" component={Demo} durationInFrames={TOTAL} fps={FPS} width={1920} height={1080} />
    <Composition id="FullTalk" component={Talk} durationInFrames={TALK_TOTAL} fps={FPS} width={1920} height={1080} />
  </>
);
