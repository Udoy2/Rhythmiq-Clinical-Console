---
name: Rhythmiq
description: ECG paper and ink, an instrument that prints the rhythm quietly and gets loud only when it changes.
colors:
  ink: "#17130F"
  ink-soft: "#3A332C"
  paper: "#FBF6EE"
  paper-raised: "#FFFDF8"
  paper-sunk: "#F3ECE0"
  paper-deep: "#E8DFD0"
  paper-edge: "#D9CDBA"
  hairline: "#E3D9C8"
  text-muted: "#4B433B"
  text-faint: "#6A6158"
  rose-grid-minor: "rgba(200, 110, 120, 0.16)"
  rose-grid-major: "rgba(200, 110, 120, 0.34)"
  accent: "#0F766E"
  accent-text: "#0B5F58"
  tier-normal: "#2E7D4F"
  tier-normal-text: "#1F6B3F"
  tier-watch: "#C26A04"
  tier-watch-text: "#8A4B08"
  tier-urgent: "#E2570F"
  tier-urgent-text: "#A8420A"
  tier-critical: "#B3122E"
  tier-critical-text: "#9B0F26"
  beat-fusion: "#0E7490"
typography:
  display:
    fontFamily: "Public Sans, system-ui, sans-serif"
    fontSize: "1.5rem"
    fontWeight: 700
    lineHeight: 1.1
    letterSpacing: "-0.015em"
  body:
    fontFamily: "Public Sans, system-ui, sans-serif"
    fontSize: "15px"
    fontWeight: 400
    lineHeight: 1.55
  label:
    fontFamily: "Public Sans, system-ui, sans-serif"
    fontSize: "0.75rem"
    fontWeight: 600
  data:
    fontFamily: "IBM Plex Mono, ui-monospace, monospace"
    fontSize: "1rem"
    fontWeight: 400
rounded:
  sm: "4px"
  md: "6px"
  lg: "8px"
spacing:
  sm: "8px"
  md: "16px"
components:
  button-primary:
    backgroundColor: "{colors.ink}"
    textColor: "{colors.paper}"
    rounded: "{rounded.md}"
    height: "2.5rem"
    padding: "0 1.25rem"
  button-primary-hover:
    backgroundColor: "{colors.ink-soft}"
  button-secondary:
    backgroundColor: "{colors.paper-raised}"
    textColor: "{colors.ink}"
    rounded: "{rounded.md}"
    height: "2.5rem"
    padding: "0 1.25rem"
  panel:
    backgroundColor: "{colors.paper-raised}"
    textColor: "{colors.ink}"
    rounded: "{rounded.lg}"
---

# Design System: Rhythmiq

## Overview

**Creative North Star: "The Strip Chart"**

Rhythmiq looks like the paper a rhythm strip is printed on. Warm white surfaces, near-black ink for every word and for the trace, and a faint rose millimetre grid that appears only where a waveform is drawn. The product reads like an instrument: calm by default, with one deep crimson that means critical and nothing else. It says what it sees and how sure it is, and it never decorates the working screens.

The interface is built for a clinician scanning a live console on a desktop, with tablet and phone widths kept usable. The ECG is the largest thing on the page, a state strip above it is the loudest thing when the rhythm turns Urgent or Critical, and everything else is hairline and quiet. The landing and sign-in screens use the same paper and ink system; there is no separate marketing skin.

**Key Characteristics:**
- Flat by default: hairline borders, no shadows on working surfaces, no gradients, no glows.
- The rose grid belongs to trace surfaces only (ECG canvas, evidence strips, trend charts, the landing strip). It is never a page background.
- Meaning is never carried by colour alone: every tier and beat class has a word, a shape and (for beats) a fill pattern.
- Tier language is one scheme everywhere: Normal, Watch, Urgent, Critical. Colour names appear only in tooltips.
- Honest degradation: stale or suppressed data is shown muted and labelled "Last window", never as a live tinted state.

## Colors

A warm paper and ink palette with four tier colours drawn from clinical convention, kept distinct from the rose grid.

### Primary
- **Ink** (#17130F): body text, the ECG trace, primary buttons, the active nav item. Contrast 17.2:1 on paper.
- **Teal Accent** (#0F766E; text #0B5F58): focus rings, links, emphasised words. Used sparingly.

### Neutral
- **Paper** (#FBF6EE): page background.
- **Raised Paper** (#FFFDF8): panels, inputs, trace surfaces.
- **Sunk Paper** (#F3ECE0): the nav rail, the sign-in side panel, alternating landing bands, table hover.
- **Paper Deep / Edge** (#E8DFD0 / #D9CDBA): hover and strong borders.
- **Hairline** (#E3D9C8): every border and divider.
- **Muted / Faint Text** (#4B433B / #6A6158): secondary and tertiary text, 9.0:1 and 5.6:1 on paper.
- **Rose Grid** (rgba(200,110,120,0.16) minor, 0.34 major): the millimetre grid on trace surfaces, 8px minor and 40px major cells.

### Tier colours (fill / text)
- **Normal** green (#2E7D4F / #1F6B3F), circle.
- **Watch** amber (#C26A04 / #8A4B08), triangle.
- **Urgent** orange (#E2570F / #A8420A), diamond.
- **Critical** crimson (#B3122E / #9B0F26), octagon.

Beat classes reuse these colours with a letter, shape and fill pattern: N circle solid, S triangle diagonal, V square cross, F diamond dots in cyan (#0E7490), Unknown hollow ring in grey.

### Named Rules
**The One Red Rule.** Crimson means critical or a ventricular beat, and nothing else. It is never used for decoration, hover, or error styling that is not clinical.

**The Grid Stays Home Rule.** The rose grid is drawn only behind a waveform or chart. Amber and orange fills must never be drawn directly over the major grid (they fall to about 2.4:1 there).

**The Never Colour Alone Rule.** Every state shows a word and a shape next to its colour; an affordance that needs colour to be understood is a defect.

## Typography

**Display and Body Font:** Public Sans (variable, self-hosted, with system-ui fallback)
**Data Font:** IBM Plex Mono (400 and 700, self-hosted), used for numbers, timers and identifiers

**Character:** Plain, wide-open and legible at small sizes, a public-signage sans paired with a mono that keeps vital signs aligned. Tabular numerals are on for the whole app.

### Hierarchy
- **Display** (700, 1.5rem, 1.1): state-strip tier name and page titles. Letter-spacing -0.015em.
- **Title** (700, 0.875rem, panel headers): panel headings.
- **Body** (400, 15px, 1.55): prose, tables, forms. Keep prose under 75 characters.
- **Label** (600, 0.75rem): form labels, table headers, hints. Nothing on signed-in screens goes below 12px.
- **Data** (Plex Mono 400/700, 1rem to 1.5rem): HR, counts, timers.

### Named Rules
**The Twelve Pixel Floor Rule.** No text under 12px anywhere in the app, landing included.

## Layout

A left rail (sunk paper, hairline right edge) with a main column. Container width on the landing is 72rem; app pages fill the main column. The live console is a two-column layout: the ECG card and its evidence on the left, one merged right column (Insight, Rhythm, Risk, Alerts, then a closed "More detail" disclosure) on the right; the columns stack at narrow widths. A one-line disclaimer stays visible on every authenticated page. Spacing runs on an 8px rhythm.

Responsive: desktop first, usable at tablet and phone widths. Tables scroll inside their containers, never the page. Touch targets are at least 40px high.

## Elevation & Depth

Flat by default. Depth is carried by tonal layering (paper, raised paper, sunk paper) and hairline borders, never by shadow. The only shadows in the system are small fixed ones for popovers and toasts (`0 6px 16px rgba(23,19,15,0.12)`). No backdrop blur, no glow, no gradient washes.

### Named Rules
**The Flat Until Floating Rule.** A surface that sits in the page has a hairline border and no shadow. Only something that floats above the page (View popover, toast, dialog) may cast one.

## Shapes

Small, restrained radii: 4px chips, 6px buttons and inputs, 8px panels. Borders are 1px hairlines; the state strip uses a 2px border in the tier colour and the Critical alert row a heavier left stripe (8px). Shapes carry meaning (circle, triangle, diamond, octagon, square, ring), so decorative geometry is avoided.

## Components

### Buttons
- **Shape:** 6px radius, 2.5rem tall.
- **Primary:** ink fill, paper text, bold; hover lightens to #3A332C; disabled at 50% opacity.
- **Secondary:** raised paper with a hairline border; hover shifts to sunk paper.
- **Destructive:** crimson fill with white text, only for disabling users or closing alerts.

### Panels
- **Corner Style:** 8px. **Background:** raised paper. **Border:** 1px hairline. **Shadow:** none.
- Panel headers are a hairline-separated row with a small bold title.

### State strip
- Sits above the ECG. Shows tier shape and name, HR and open-alert count. Watch and Normal tint the background lightly; Urgent and Critical flip to a solid fill (#A8420A / #9B0F26) with white text, the loudest element on the page. When the stream is down or alerts are suppressed it drops the tint, shows a hollow glyph and "Last window: {tier}", and greys the HR.

### ECG canvas and beat ribbon
- Ink trace on the rose grid, calibrated to 25 mm/s and 10 mm/mV. Beat markers use letter, shape, fill pattern and colour; the ribbon repeats them over the last 60 seconds.

### Toolbar
- Playback controls (Pause, back, forward, Go live) as labelled icon buttons, and one "View" disclosure for speed, gain and ground truth.

### Alerts
- Row button acknowledges. Urgent and Critical send immediately; Watch shows a secondary "Undo" for 8 seconds first. Alerts are never closed automatically.

### Inputs
- Raised paper, 1px hairline, 6px radius; focus shifts the border to teal with a 2px soft ring. Visible labels above every field.

### Navigation
- Rail on desktop, scrollable tab row for patient and admin sub-pages; active item is a solid ink pill with paper text.

### Dr. Rhidoy help
- A static Help button. One proactive nudge, once per session, on the Patients page only.

## Do's and Don'ts

### Do:
- **Do** use ink on paper for all text; keep tier colours for tier meaning.
- **Do** pair every colour with a word and a shape.
- **Do** keep the rose grid behind waveforms and charts only.
- **Do** show stale data muted and labelled "Last window".
- **Do** keep text at or above 12px and contrast at or above 4.5:1.
- **Do** respect `prefers-reduced-motion`; motion is rare and only shows what changed.

### Don't:
- **Don't** add gradients, gradient text, glows, backdrop blur or shadows to working surfaces.
- **Don't** use crimson for anything but critical meaning and V beats.
- **Don't** draw amber or orange directly on the major rose grid.
- **Don't** auto-close alerts or hide the alert path behind a disclosure.
- **Don't** invent a second tier vocabulary or put colour names in visible labels.
- **Don't** bring back decorative hero art, orbital spheres or mascot nudges on /live and /alerts.
