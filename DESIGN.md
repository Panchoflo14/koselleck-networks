---
name: Koselleck Machine
description: A cool-lit reading-room instrument for watching English vocabulary reorganize around the Sattelzeit, one period at a time.
colors:
  reading-room: "#ECEFF2"
  ink: "#191D24"
  muted-slate: "#4C5560"
  cool-line: "#AEB9C6"
  card-white: "#F8FAFC"
  oxidized-iron: "#8C2F1C"
  deep-navy: "#1B3A5C"
  coverage-gap: "#DCE1E7"
typography:
  display:
    fontFamily: "EB Garamond, Iowan Old Style, Palatino Linotype, Palatino, Georgia, serif"
    fontSize: "2.25rem"
    fontWeight: 600
    lineHeight: 1.2
    letterSpacing: "normal"
  headline:
    fontFamily: "EB Garamond, Iowan Old Style, Palatino Linotype, Palatino, Georgia, serif"
    fontSize: "1.625rem"
    fontWeight: 600
    lineHeight: 1.3
    letterSpacing: "normal"
  title:
    fontFamily: "EB Garamond, Iowan Old Style, Palatino Linotype, Palatino, Georgia, serif"
    fontSize: "1.25rem"
    fontWeight: 600
    lineHeight: 1.25
    letterSpacing: "normal"
  body:
    fontFamily: "Public Sans, system-ui, Segoe UI, sans-serif"
    fontSize: "1rem"
    fontWeight: 400
    lineHeight: 1.5
    letterSpacing: "normal"
  meta:
    fontFamily: "Public Sans, system-ui, Segoe UI, sans-serif"
    fontSize: "0.875rem"
    fontWeight: 400
    lineHeight: 1.5
    letterSpacing: "normal"
  label:
    fontFamily: "Public Sans, system-ui, Segoe UI, sans-serif"
    fontSize: "0.75rem"
    fontWeight: 600
    lineHeight: 1.4
    letterSpacing: "0.05em"
rounded:
  sm: "6px"
  md: "8px"
  lg: "10px"
  pill: "999px"
spacing:
  1: "4px"
  2: "8px"
  3: "12px"
  4: "16px"
  5: "24px"
  6: "32px"
  7: "48px"
components:
  button-primary:
    backgroundColor: "{colors.ink}"
    textColor: "{colors.reading-room}"
    rounded: "{rounded.md}"
    height: "2.5rem"
    padding: "0 1rem"
  button-ghost:
    backgroundColor: "transparent"
    textColor: "{colors.muted-slate}"
    rounded: "{rounded.md}"
    height: "2.5rem"
    padding: "0 0.8rem"
  tab-pill:
    backgroundColor: "{colors.card-white}"
    textColor: "{colors.muted-slate}"
    rounded: "{rounded.pill}"
    padding: "0.4rem 0.8rem"
  tab-pill-active:
    backgroundColor: "{colors.deep-navy}"
    textColor: "#FFFFFF"
    rounded: "{rounded.pill}"
    padding: "0.4rem 0.8rem"
  text-input:
    backgroundColor: "{colors.reading-room}"
    textColor: "{colors.ink}"
    rounded: "{rounded.md}"
    height: "2.5rem"
    padding: "0 0.8rem"
  card:
    backgroundColor: "{colors.card-white}"
    rounded: "{rounded.lg}"
    padding: "1rem 1.3rem"
---

# Design System: Koselleck Machine

## Overview

**Creative North Star: "The Reading Room"**

The interface is lit like a conservation reading room, not the artifact on the table in front of you: a cool, neutral, evenly-lit ground that stays quiet on purpose so the one warm color in the system - oxidized-iron red - reads as a deliberate signal every time it appears, not as decoration. This is a direct correction of an earlier direction (a warm cream ground with a terracotta accent) that an Impeccable detector pass and a human reviewer both independently flagged as a stock "AI-generated archival" look; a second attempt that leaned further into warmth (a manila "Laid Paper" tone) read as costume rather than instrument, which is what settled the cooler direction instead.

Two type voices carry the whole system, and they are never interchangeable: EB Garamond (a revival of the letterform this corpus's own period actually used) speaks whenever the interface is making a claim about the past - headings, group/community names, the timeline verdict sentence. Public Sans carries every other role - labels, body copy, controls - as an institutional, document-associated workhorse rather than a "generic SaaS" sans (Inter was deliberately rejected here after the Impeccable detector flagged it as a genre default).

The system is flat by default: bordered cards and pills, almost no shadow, because a reading room doesn't dramatize its furniture. Depth is reserved for the two elements that genuinely float above the page (tooltip, info panel), never for a card just sitting in the document flow.

**Key Characteristics:**
- Cool, evenly-lit neutral ground; warmth is rationed to a single accent
- Two accent colors with strictly separate jobs, never swapped (see Named Rule below)
- Serif for historical claims, sans for everything operational
- Flat, bordered, quiet - shadow is the exception, not the default
- One shared page width and one shared control height across all four surfaces; no per-component one-offs

## Colors

A cool neutral field with exactly two accents, each assigned a single, non-negotiable job.

### Primary
- **Deep Navy** (#1B3A5C): the interaction and stability signal. Active tabs, hover borders, focus outlines, active toggle/region buttons, the "stable" (unchanged) timeline arrow. If a user can click it, or if something did *not* change, this is the color.

### Secondary
- **Oxidized Iron** (#8C2F1C): the historical-claim signal, used sparingly. The findings-headline number, the "changed" timeline arrow and flag, the Sattelzeit band and its label, the "Known finding" callout marker. This is the only warm color in the system - its rarity is what makes it read as "the tool is telling you something happened here."

### Neutral
- **Reading Room** (#ECEFF2): the page background. Deliberately cool-lit rather than warm/archival; every other token is calibrated at least as light as this.
- **Card White** (#F8FAFC): the surface for every bordered card, toolbar, and panel - one step lighter than the page itself, never a shadow to lift it.
- **Slate Muted** (#4C5560): secondary text, captions, meta rows, disabled-feeling states.
- **Cool Line** (#AEB9C6): every border - cards, inputs, dividers, the dashed caveat rule.
- **Ink** (#191D24): primary text and the fill for the "primary" solid button (inverted: ink background, reading-room text).
- **Coverage Gap** (#DCE1E7): the fill for a period the corpus doesn't cover - a visual admission of a data gap, not an error state.

### Named Rules
**The Two-Accent Rule.** Navy means "you can act on this, or nothing changed here." Iron-red means "the tool is making a historical claim." Never use one for the other's job - a hover state should never turn iron-red, and a findings number should never turn navy.

**The Cool-Ground Rule.** The background is lit cool on purpose - the instrument should not look like the (warm, archival) artifact it measures. Any future warm palette choice for this ground repeats an already-rejected direction.

## Typography

**Display Font:** EB Garamond (variable, weights 400-700, self-hosted woff2)
**Body Font:** Public Sans (variable, weights 400-700, self-hosted woff2)

**Character:** A revival serif that speaks only when the interface makes a claim about the past, paired with a plain, institutional sans that runs everything else - the pairing is the argument that this is a research instrument reporting on history, not a history-themed product.

### Hierarchy
- **Display** (600, 2.25rem/36px, 1.2): page `<h1>` only.
- **Headline** (600, 1.625rem/26px, 1.3): the timeline verdict sentence - the single largest non-heading statement on the page, meant to be legible even to a reader who never looks at the strip below it.
- **Title** (600, 1.25rem/20px, 1.25): card and section titles - `<h2>`/`<h3>`, findings-headline, portada card titles, timeline group names (clamped to 3 lines).
- **Body** (400, 1rem/16px, 1.5): primary running text and default UI text.
- **Meta** (400, 0.875rem/14px, 1.5): captions, secondary UI text, table cells.
- **Label** (600, 0.75rem/12px, uppercase, 0.05em tracking): tab text, control labels, table headers, timeline period labels - the hard floor; nothing on the page renders smaller than this.

### Named Rules
**The Six-Role Rule.** Every piece of text maps to exactly one of the six roles above. No arbitrary in-between sizes, and nothing smaller than Label (12px) anywhere, including uppercase micro-text.

**The No-Italic Rule.** Italic-as-emphasis was eliminated project-wide (confirmed by a full-codebase grep) after it was flagged as a recognizable AI-generated-design tell. Emphasis comes from color (the Two-Accent Rule), weight, or the Title/Headline size step - never from style.

## Layout

One page-width convention for all four surfaces (portada, buscador, grafo's header, timeline): a 72rem (1152px) centered column (`--width-page`) with fluid gutters (`clamp(1rem, 4vw, 2rem)`). A `--measure-prose` token (68ch) exists for a narrower reading column but is currently unused everywhere by explicit choice - every block, running text included, shares the one page width rather than splitting into a separate prose measure. `/grafo` alone breaks from the column for its graph canvas and 380px sidebar, since that surface's job is spatial exploration, not reading.

Spacing follows a 4px base scale (`--space-1` 4px through `--space-7` 48px), applied with deliberate contrast - tight inside a card, generous around the timeline strip - rather than one repeated gap everywhere. So far this scale is only fully applied to the timeline; the rest of the stylesheet still carries older ad hoc rem-value spacing, a known, not-yet-resolved inconsistency rather than a second convention to imitate.

### Named Rules
**The One-Width Rule.** Every page-level container uses `--width-page`; prose sitting on the page background does not get its own narrower measure; text inside a bordered card fills the card. No new one-off `max-width` value below this line - if the convention doesn't fit a component, the component is wrong, not the scale.

## Elevation & Depth

Flat by default: cards and panels sit on the page as bordered surfaces at their native elevation, no ambient shadow. The two exceptions are elements that genuinely float above the page rather than sitting in its flow - the graph tooltip (`0 2px 8px rgba(0,0,0,0.15)`) and the graph info panel (`0 2px 10px rgba(0,0,0,0.2)`). Depth is reserved for "this is temporarily floating," never for "this is important."

### Named Rules
**The Flat-by-Default Rule.** A card in the document flow never gets a shadow to look more important; only a genuinely floating overlay does.

## Shapes

Three corner radii, no others: 6px for the smallest controls (the compact `k`-input, the tooltip), 8px for buttons/text-inputs/timeline cards, 10px for larger bordered containers (findings banner, search cards, changed-panel, portada cards, the timeline track itself). Fully round (999px) pill shape is reserved for anything that behaves like a toggle or a tab - nav tabs, the region toggle, sidebar tabs, the changed-words chip list. Dashed borders (as opposed to solid) mark an annotation over content rather than a container around it - the Sattelzeit band, the caveat rule, the timeline seam note.

## Components

### Buttons
- **Shape:** 8px radius, height locked to `--control-h` (2.5rem/40px) for every primary button, input, and select in the app - found six different control heights (21-39px) before this token existed.
- **Primary** (`#focus-form button`, `.search-form button`): solid ink background (#191D24), reading-room text, no border.
- **Ghost/ Secondary** (`#focus-clear`, `.reset-view-btn`): transparent background, muted-slate text, cool-line border; hover shifts border and text toward the relevant accent color (navy for interaction).
- **Hover/Focus:** primary buttons dim to 85% opacity on hover; all text-style controls get a 2px navy outline on focus (`:focus-visible` where applicable) - the one exception to the Two-Accent Rule that would matter is that focus itself is an interaction state, so navy is correct here too.

### Pills (tabs, region toggle, chips)
- **Style:** cool-line border, card-white background at rest, fully round.
- **Active/Selected:** solid deep-navy fill, white text - the same token used for every other "you can act on this / this is selected" moment.

### Cards / Containers
- **Corner Style:** 10px for standalone containers (findings banner, search-summary-card, neighbors-card, changed-panel, portada cards, timeline track), 8px for the smaller timeline period cards.
- **Background:** card-white (#F8FAFC), one step lighter than the page.
- **Shadow Strategy:** none at rest (see Elevation & Depth) - a bordered flat surface, not a lifted one.
- **Border:** 1px cool-line (#AEB9C6) solid; the portada's primary card upgrades to a 2px oxidized-iron border since it is making the "start here, this is the finding" claim.

### Inputs / Fields
- **Style:** reading-room background (not card-white - inputs sit visually "in" the page, not elevated above it), cool-line border, 8px radius, `--control-h` height, shared by every `.text-input`/`.select-input` across all three form-bearing surfaces (buscador, grafo's focus form, timeline's word search).
- **Focus:** 2px deep-navy outline, 1px offset.

### Timeline Card (signature component)
The interface's most distinctive element: a bordered, reading-room-background card per period showing a community/domain name (Title role, EB Garamond, 3-line clamp) plus a meta line (Label role, tabular numerals). States: hover shifts the border to navy; keyboard focus gets a 2px navy outline; a selected/drilled-into card gets a 2px oxidized-iron border - the same "this is the historical claim being made right now" signal as everywhere else, applied to a single card instead of a headline number.

## Do's and Don'ts

### Do:
- **Do** use `--width-page` (72rem) as the only page-level container width across all four surfaces.
- **Do** give every historical-claim moment (a changed community, the verdict number, the Sattelzeit band) the oxidized-iron treatment, and every interactive/stable moment the deep-navy treatment - never swap them.
- **Do** surface every quantitative caveat (mixed-label %, resolution choice, corpus coverage gaps) through the existing `.caveat`/`<details>` pattern rather than only in prose a reader can skip past.
- **Do** keep every primary text input, select, and button at `--control-h` (2.5rem) so controls read as one family regardless of which page they're on.

### Don't:
- **Don't** introduce a new one-off `max-width` value anywhere on the page - the width-coherence pass that unified this explicitly closed that door.
- **Don't** use italic for emphasis anywhere - eliminated project-wide as a recognizable AI-generated-design tell; use color, weight, or the type-scale step instead.
- **Don't** add a dark-mode override to `:root` - the 8-slot community palette (`app.js`) and 14-slot subject-lane palette (`word_detail.js`) are contrast-tuned specifically against this light background, and their contrast against a dark background has never been computed.
- **Don't** treat the community/subject data-visualization palettes as available for general UI use - they exist for one job (distinguishing many categories in a chart) and were tuned against the *previous* background color (#F1EDE3); the current "Reading Room" ground (#ECEFF2) was deliberately kept at least as light on the assumption that this preserves their contrast, but that assumption has not been re-measured against the new ground - worth an explicit `/impeccable audit` contrast check before relying on it further.
