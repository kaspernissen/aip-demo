# Handoff: Broadsheet Design System

## Overview
Broadsheet is a newsprint-inspired visual system for the web: near-black Source Serif 4 on a paper-white ground, with cyan and magenta used sparingly as spot colors (plus a print yellow reserved for image/number print treatments). Structure comes from type scale and whitespace — no boxes, no section dividers. This package contains the full token sheet, component CSS, reference pages, and two templates (landing page, slide deck).

## About the Design Files
The files in this bundle are **design references created in HTML** — they show intended look and behavior, not production code to paste in. The task is to **recreate this system in the target codebase's existing environment** (React, Vue, Svelte, native, etc.) using its established patterns — e.g. as CSS variables + a component library, Tailwind theme config, or styled components. If no environment exists yet, choose the most appropriate framework and implement there.

`styles.css` is plain CSS with no build step and can be adopted nearly verbatim as the token/base layer; the component classes should be mapped onto the codebase's component primitives.

## Fidelity
**High-fidelity.** Colors, type, spacing, radii, shadows and interaction states are final. Match exactly.

## Design Tokens

### Color — roles
| Token | Value | Use |
|---|---|---|
| `--color-bg` | `#f3f2f2` | Page ground (paper) |
| `--color-surface` | `#eae9e9` | Cards, inputs, dialogs |
| `--color-text` | `#201e1d` | Ink |
| `--color-accent` | `#0088b0` | Cyan — interactive elements |
| `--color-accent-2` | `#d6006c` | Magenta — rarer second spot color |
| `--color-process-yellow` | `#edbb00` | Print treatments only — never UI chrome or copy |
| `--color-divider` | `#201e1d` @ 16% | Hairline borders (inputs, seg control, table) |

### Color — tonal ramps (OKLCH, shared lightness scale)
| Step | Neutral | Accent (cyan) | Accent-2 (magenta) |
|---|---|---|---|
| 100 | `#f8f4f4` | `#e9f8ff` | `#fff1f4` |
| 200 | `#eae7e7` | `#cbeeff` | `#ffdee6` |
| 300 | `#d7d3d3` | `#99e0ff` | `#ffc0d0` |
| 400 | `#bab6b6` | `#62c5ee` | `#ff90b1` |
| 500 | `#9b9797` | `#38a6cf` | `#ff458e` |
| 600 | `#7d7979` | `#1186ac` | `#d82071` |
| 700 | `#605d5d` | `#006786` | `#aa0b56` |
| 800 | `#444141` | `#004961` | `#790e3d` |
| 900 | `#2d2b2b` | `#0a303e` | `#4b1528` |

Rules: 100–300 for tinted fills/hovers, 500 base, 700–900 for text on tints and pressed states. Accent on ground is ≥3:1 only — for body-size cyan text use `--color-accent-700`.

### Typography
- Family: **Source Serif 4** (Google Fonts; weights 400, 600, italic 400) for everything — headings, body, and UI chrome. No sans-serif anywhere.
- Heading weight: 600. Heading line-height 1.12, letter-spacing −0.015em.
- Body: 15px / 1.55, weight 400.

| Element | Size |
|---|---|
| h1 | 42px |
| h2 | 32px |
| h3 | 25px |
| h4 | 20px |
| h5 | 16px |
| h6 | 13px, uppercase, 0.08em tracking |
| Button / input | 14px |
| figcaption | 11px, ink @ 55% |

Emphasis and pull quotes use the true italic — never synthesized oblique.

### Spacing (density 1.25× baked in)
`--space-1` 5px · `--space-2` 10px · `--space-3` 15px · `--space-4` 20px · `--space-6` 30px · `--space-8` 40px. Do not tighten.

### Radius
`--radius-sm` 1px · `--radius-md` 2px (default) · `--radius-lg` 4px (dialog).

### Shadows (ink #2d2b2b tinted)
- `--shadow-sm`: `0 1px 2px` @ 14%
- `--shadow-md`: `0 3px 10px` @ 16%
- `--shadow-lg`: `0 12px 32px` @ 22%

## Components
Full CSS is in `styles.css`; live demos in `components/*.html`.

- **Button** (`.btn`): Source Serif 600, 14px, padding 10px × 18px, radius 2px, 6px icon gap.
  - `primary`: accent fill, bg-colored text; hover `accent-600`, active `accent-700`.
  - `secondary`: 1px divider border; hover ink @ 7%, active ink @ 14%.
  - `ghost`: accent text, 5px inline padding; hover accent @ 10%, active @ 18%.
  - `icon`: 36×36, no padding. `block`: full width.
  - Disabled: opacity 0.45, `not-allowed`.
- **Tag** (`.tag`): 11px, 0.02em tracking, padding 3px 10px, radius 1.5px. Variants: accent (100 fill / 800 text), accent-2, neutral, outline (1px accent border + text).
- **Field / Input**: label 12px, ink @ 70%, 5px below. Input min-height 36px, padding 6px 10px, 14px, surface fill, 1px divider border, cyan caret; hover border ink @ 45%; focus border accent. Placeholder ink @ 65%. Textarea min-height 90px.
- **Radio**: 16px circle, 1.5px divider border; hover accent border; checked = accent fill with 4px inset bg ring.
- **Segmented control** (`.seg`): 1px divider border, radius 2px; options 13px, padding 7px 12px, 1px separators; checked = accent fill + bg text; unchecked hover ink @ 7%.
- **Card** (`.card`): surface fill, padding 15px, radius 2px, 10px gap. Kicker 10px uppercase 0.1em accent; title 17px/1.2 600; body 13px @ 80%; meta 11px ink @ 50%. Use only for discrete items (listings), never for layout.
- **Nav** (`.nav`): flex, 20px gap, padding 15px 20px, no border. Brand 18px 600, pushes links right. Links 14px, inherit color; hover/current = accent.
- **Table**: 14px. Header 11px uppercase 0.08em, ink @ 60%, 1px divider bottom. Rows 10px padding, 1px ink @ 8% rule; hover ink @ 4%.
- **Dialog**: backdrop neutral-900 @ 50%; panel max 440px, padding 20px, radius 4px, surface fill, `--shadow-lg`; title 20px 600; body 14px @ 85%; actions right-aligned, 10px gap.

### Print treatments (signature)
- **`.cmyk`** — a photo printed as four misregistered process plates (C/M/Y/K) via SVG `feColorMatrix` filters defined in `print-plates.js`, multiplied onto the paper with a 3px halftone dot overlay. The plates ease into register on hover (driven by `print-plates.js`; disabled under reduced-motion / touch).
- **`.cmyk-num` / `.cmyk-head`** — display numerals/headlines as three offset C/M/Y text plates with `mix-blend-mode: multiply`; the dark core comes from overlap. Offsets are em-scaled; plates lean slightly toward the pointer via `--press-nx/--press-ny`.
- **`.halftone`** — simpler treatment for interface imagery: grayscale 0.35, contrast 1.15, 3px radial dot screen multiplied on top.

These depend on SVG filters and blend modes; port `print-plates.js` as-is (or as a component that injects the `<svg><defs>` once per document).

## Interactions & Behavior
- Hover: tint from accent ramp or ink `color-mix`; pressed: one ramp step darker (`accent-600` → `accent-700`).
- Focus: `:focus-visible { outline: 2px solid var(--color-accent); outline-offset: 2px }`. Never the browser default ring.
- `::selection`: accent @ 30%.
- Links: accent, underline offset 3px.
- Respect `prefers-reduced-motion` for the print-plate animation.

## Layout principles
- Left-aligned, asymmetric. Flush-left headings; whitespace on the right.
- Sections separated by whitespace only — no rules, borders, or boxes. The single exception is front-page furniture (landing's thick-thin rule pair around the dateline).
- No dark surfaces.

## Icons
Phosphor Icons (https://phosphoricons.com), **duotone** weight throughout.

## Assets
- `assets/photo.jpg`, `assets/photo-color.jpg` — sample photography used in the demos/templates. Replace with product imagery.
- Font: Source Serif 4 via Google Fonts (imported at the top of `styles.css`).

## Files
- `styles.css` — source of truth: tokens + base + components.
- `print-plates.js` — SVG separation filters and the press-motion driver for `.cmyk`.
- `theme.json` — machine-readable theme parameters.
- `GUIDE.md` — original design-system guide (usage rules, do/don't).
- `foundations/` — type, color, layout, icons, image reference pages.
- `components/` — buttons, forms, cards, navigation, table, dialog demos.
- `templates/landing/index.html` — one-page landing reference.
- `templates/deck/Deck.dc.html` — 23-slide deck reference (cover, dividers, charts, table, quote, close).
- `theme.html`, `thumbnail.html` — reference sheet and cover.
- `_ds_bundle.js` — compiled bundle the templates load (includes `print-plates.js`).

Open any HTML file directly in a browser to view it.
