# Verticalizing Platform Intelligence for Regulated Domains

The talk deck for KubeCon + CloudNativeCon North America 2026, Salt Lake City. It is built as HTML in the
**Broadsheet** design system (`bs/`, rules in `bs/GUIDE.md`), with 1920×1080 slides in `<deck-stage>`, a speaker-notes
follower, and a folio with the speaker's handles.

- Subtitle and thesis: **If you can't trace it, you can't govern it.**
- 38 slides, 34:45 including the demo (Wed 11 Nov, 11:00–11:30, with William Rizzo); the cut-first slides
  named in `PLAN.md` take it to 30 minutes. The long cut's slides stay in the fragments as `data-cut`. William
  covers the problem and every guardrail (`slides/10-open-act1.html`, `slides/15-guardrails.html`, one ring slide
  per ring: the rule beside what the demo cluster printed); Kasper takes over at the auditor's questions for
  Northstar, the demo, and the observability and eval layer; William closes. The live demo is six steps in two
  browser tabs (customer-ui with three claim chips, reviewer-ui), Jaeger and one terminal (`scripts/demo start`,
  then `inject`, `evidence`, `keystone`; about 6:30 with narration), against one Northstar cluster brought up by
  `scripts/setup-kind.sh` (runbook "The run sheet · one screen"). No slide shows a command during the demo:
  "What you'll see" previews it, one dark "Live" slide stays up and carries the run sheet in its note, and
  `slides/30-live.html` is one visual summary, "What you just saw", ring by ring. A few statement slides are
  dark sheets (`.x-dark`, in `ocean/ocean.css`). The operator checklists (pre-flight, reset, triage) live in
  `docs/demo-runbook.md`.
- "Decide" is defined by **The Safe Operating Model** (Francesco Beltramini, ControlPlane), as William
  presents it: rings of least privilege (Core · identity, Ring 1 · reach, Ring 2 · capability, and the
  Keystone beneath them); the data ring is left out of this talk. The deck uses nine of William's KCD Sofia
  2026 slides, set on Northstar (origin tag `WILLIAM`). Slides derived from his OSFF demo carry
  the origin tag `BOUNDED` in `PLAN.md`; Act 3's tools slides carry their ring as a `.ring` marker.
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
`index.html?slide=27` deep-links to a slide.

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
| `assets/shots/` | Screenshots from live Northstar runs: 2026-09-28 (`0af552f`; the evidence dashboard and the eval scores, from an older build), 2026-10-15 (the Backstage and customer UI shots) and 2026-10-17 (the two Jaeger shots). The other 16 files in the folder are 2026-09-28 originals no slide uses. |

**Lifting a book figure:** paste the book's inline `<svg>` inside `<figure class="bfig">`. The `.bfig` rules
map the book's fills and strokes to Broadsheet tokens. Crop the viewBox, delete what the slide doesn't need, and
raise small labels to about 20px at stage size. Don't redraw.

## Before you present

- Every fast-moving fact carries a `[verify: …]` line in its speaker note. They map to Appendix B of the book.
- The demo hazards are in Ch 14 and in the beat notes:
  - Run `./scripts/demo start` before the audience arrives and `./scripts/demo reset` after every run:
    forgetting `start` leaves the contract at `assisted`, and the Standard claim waits instead of settling.
  - `demo inject` waits up to 30 s for the claim's trace in Jaeger, `demo evidence` up to 90 s.
  - The model's reading of the injection claim varies run to run; if it takes the bait, `inject` says
    FOLLOWED and the ceiling still refuses. The Live note has the line for it.
  - In the browser: don't close the customer tab within 5 seconds of a submit, and switch Jaeger from GenAI View to Trace Timeline.
  - After the run, leave the autonomy contract at `assisted` (`demo reset`), and the keystone over-grant absent.
  - Kyverno must be Ready, with its webhook registered, before step 6 (`demo keystone`). A gracefully stopped Kyverno deletes its own webhooks, which turns the keystone off silently; `scripts/preflight.sh` checks for this.
- Slide 2's note has a placeholder line for William; he should write his own.
- Two speakers: William Rizzo opens with the auditor's questions and Act 1, Kasper runs Acts 2–4 through the evidence, William closes from "Three things to remember". Every note starts with WILLIAM or KASPER; `PLAN.md` has a *Who* column.
