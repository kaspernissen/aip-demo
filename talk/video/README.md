# The narrated demo video

A real run of the demo flow (`scripts/demo start` through `keystone`), recorded at 1920x1080,
narrated (`narration.md`) and edited in Remotion. The last render is
`talk/export/northstar-demo-narrated.mp4`; its media is kept in `talk/export/video-media/`.
Only the sources are committed: `public/`, `work/` and `out/` are git-ignored.

## Record

Against a cluster brought up by `scripts/setup-kind.sh`, from the repo root:

```bash
.venv/bin/python talk/video/record/capture.py   # demo start, the three claims, approve, inject, evidence, keystone
.venv/bin/python talk/video/record/jaeger.py    # the Large claim's held trace, span by span
python3 talk/video/record/terminal.py           # casts -> public/clips/*.mp4, paced to the narration
./scripts/demo reset
```

`capture.py` leaves the browser videos and the event marks in `work/`. Convert the three page
videos to `public/clips/{customer,reviewer,jaeger}.mp4` (30 fps h264) and copy the marks from
`work/events.json` into `src/segments.ts`. The narration mp3s go in `public/audio/`.

`scripts/demo evidence` shows AgentEvals' verdicts only once the session has been evaluated; if the
Tricky claim says "no finished AgentEvals session", record `evidence` again a few seconds later.

## Render

```bash
cd talk/video && npm ci
npx remotion studio src/index.ts                                     # preview
npx remotion render src/index.ts NorthstarDemo out/northstar-demo.mp4 --codec h264 --crf 18
```
