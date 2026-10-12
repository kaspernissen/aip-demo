# Verticalizing Platform Intelligence for Regulated Domains

The talk deck for KubeCon + CloudNativeCon North America 2026, Salt Lake City. It is built as HTML in the
**Broadsheet** design system (`bs/`, rules in `bs/GUIDE.md`), with 1920×1080 slides in `<deck-stage>`, a speaker-notes
follower, and a folio with the speaker's handles.

- Subtitle and thesis: **If you can't trace it, you can't govern it.**
- 82 slides, 51:00 of content: the **long cut**, for a 30-minute slot (Wed 11 Nov, 11:00–11:30). Kasper cuts
  it down himself. Six live demo beats run against Northstar at stage 7: 1, 2, 4 and 7 in the browser, each
  with a hidden screenshot fallback, and R (reach) and K (keystone) in a terminal, each with a hidden
  fallback that typesets the real transcript of a live run (2026-10-12). Three speaker-only appendix slides
  sit after "Thank you": pre-flight, reset, triage.
- "Decide" is defined by William's *Bounded Agent Autonomy* (OSFF 2026): the platform enforces an agent's
  authority limits, in four rings (Core · Identity, Ring 1 · Reach, Ring 2 · Capability, Keystone). Slides
  derived from his demo carry the origin tag `BOUNDED` in `PLAN.md`; Act 3's tools slides carry their ring
  as a `.ring` marker.
- `PLAN.md` is the slide-by-slide plan: timing, origin (reused or new), visual and point. Its table is
  generated: after `python3 build/build.py`, run `python3 build/plan.py` (`--check` exits 1 if it is stale). It implements
  Ch 14 of the research book *If You Can't Trace It*, plus one slide that maps the CFP abstract's five
  building blocks.
- Slides carry little text. What to say is in the speaker notes, written to be read out loud.

## Run it

```bash
./start.sh                  # serves on :8002, opens the deck and the speaker-notes follower
```

**Keys:** `←` / `→` / `Space` to move, `Home` / `End` to jump, number keys to seek, `R` to reset.
`index.html?slide=27` deep-links to a slide. Fallback slides sit right after each live beat and carry a
hollow "Fallback · Beat N" marker. When the beat works live, press → to skip past its fallback slide.

## Build

The deck lives in `talk/` of the aip-demo repo, next to the Northstar code it demos. Run every command below from `talk/`; `../.venv` is the repo's own venv. `export/` and `preview-*` are git-ignored.

`index.html` is **generated**. Edit the fragments, never the output:

```bash
python3 build/build.py            # compose index.html + talk.css; asserts one note per slide, prints running time
python3 build/build.py --list     # the same, plus the numbered slide list
python3 build/plan.py             # regenerate PLAN.md's slide table (--check: exit 1 if stale)
python3 build/build.py --only slides/30-act3a.html   # preview one fragment -> preview-30-act3a.html
../.venv/bin/python build/shoot.py                   # PNG per slide + export/contact-sheet.png
../.venv/bin/python build/shoot.py 40-44                 # a range
../.venv/bin/python build/shoot.py --deck preview-30-act3a.html  # a preview -> export/preview-30-act3a/
```

| Path | What it is |
|---|---|
| `slides/NN-name.html` | One fragment per act: a run of `<section>` slides, in order. `@@EVENT@@` expands to the event name. |
| `slides/NN-name.css` | That fragment's own components, scoped under its class prefix (`x30-*`, `a2-*` …). Tokens only, no raw hex. |
| `notes/NN-name.json` | One `{"secs", "note"}` per slide in the fragment. The composer stamps each note with its start and end time. |
| `bs/` | The Broadsheet design system: `styles.css` (tokens, type, colour plates), `print-plates.js`, `GUIDE.md`. Don't fork it. |
| `build/broadsheet-deck.css` | Broadsheet's deck template layouts: cover, divider, content, hero, quote, split, bleed. |
| `build/talk-base-bs.css` | Talk-wide components: beat marker (`.beat`, `.beat.fallback`), ring marker (`.ring`), terminal transcript (`.term`), screenshot frame, span tree, attribute list, origin chip, and the `.bfig` layer that recolours book figures into Broadsheet tokens. |
| `build/head.html`, `chrome.js` | Page head (Broadsheet CSS, Phosphor icons, deck-stage) and the folio / `?slide=N` handling. |
| `deck-stage.js`, `notes.html` | Deck engine and speaker-notes follower, copied unchanged from the reference deck. |
| `reports/` | The sources the deck cites: *If You Can't Trace It*, full edition (303 pp) and study edition (154 pp), and *Instrumenting AI Agents* (Dash0 DevRel, Aug 2026, 53 pp; the notes cite its printed page numbers, one lower than the PDF index). |
| `assets/shots/` | Screenshots from a live Northstar run on 2026-09-28 (stage 6, commit `0af552f`). |

**Lifting a book figure:** paste the book's inline `<svg>` inside `<figure class="bfig">`. The `.bfig` rules
map the book's fills and strokes to Broadsheet tokens. Crop the viewBox, delete what the slide doesn't need, and
raise small labels to about 20px at stage size. Don't redraw.

## Before you present

- Every fast-moving fact carries a `[verify: …]` line in its speaker note. They map to Appendix B of the book.
- The demo hazards are in Ch 14 and in the beat notes:
  - Beat 6 scores 0.0 as scripted, because of the shared claim id. A clean 1.0 needs the permit run under its
    own claim id; otherwise keep beat 6 as the slide.
  - Don't close the customer tab within 5 seconds of a submit.
  - Switch Jaeger from GenAI View to Trace Timeline.
  - Leave the autonomy contract at `assisted`, and the keystone over-grant absent.
  - Kyverno must be Ready, with its webhook registered, before Beat K. A gracefully stopped Kyverno deletes its own webhooks, which turns the keystone off silently; `start-demo.sh` checks for this. Backstage's flip is never affected by a Kyverno outage.
- Slide 3's note has a placeholder line for William; he should write his own.
- Two speakers: William Rizzo opens with the auditor's questions and Act 1, Kasper runs Acts 2–4 through the evidence, William closes from "From requirement to artifact". Every note starts with WILLIAM or KASPER; `PLAN.md` has a *Who* column.
