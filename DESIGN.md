---
name: Rhythmiq
description: Official brand kit — futuristic AI healthcare console
colors:
  navy: "#0B1F3A"
  blue: "#234879"
  green: "#00B86B"
  green-bright: "#22D889"
  cyan: "#22D3EE"
  coral: "#FF5F57"
  ink: "#10243E"
  muted: "#64748B"
  bg: "#F5F7FA"
  surface: "#FFFFFF"
  border: "#DCE5EE"
typography:
  display:
    fontFamily: "Manrope, system-ui, sans-serif"
    fontWeight: 700
  body:
    fontFamily: "DM Sans, system-ui, sans-serif"
    fontSize: "15px"
    fontWeight: 400
    lineHeight: 1.55
rounded:
  sm: "12px"
  md: "20px"
  pill: "999px"
spacing:
  sm: "8px"
  md: "16px"
components:
  button-primary:
    backgroundColor: "linear-gradient(110deg, #00B86B, #22D3EE)"
    textColor: "#0B1F3A"
    rounded: "{rounded.pill}"
    height: "2.5rem"
  button-secondary:
    backgroundColor: "#FFFFFF"
    textColor: "{colors.ink}"
    rounded: "{rounded.pill}"
    height: "2.5rem"
---

# Design system — Rhythmiq

Source of truth: `rhythmiq_complete_website_brand_kit/rhythmiq_brand_kit/`

## Overview

Futuristic AI healthcare. Deep navy foundation, medical green, cyan AI accent, light clinical workspace. Sticky navy glass rail/nav, white elevated cards, green→cyan primary gradient.

## Colors

Page `#F5F7FA`. Surfaces white with `#DCE5EE` borders and soft navy shadow. Brand: navy `#0B1F3A`, green `#00B86B` / `#22D889`, cyan `#22D3EE`, coral `#FF5F57`. ECG: white paper, navy trace, green grid.

## Typography

**Manrope** display. **DM Sans** UI. Mono for measures and IDs.

## Layout

Navy left rail (desktop) / navy sticky header (mobile). Light main canvas. Auth: navy brand panel with ECG grid + light form panel.

## Elevation & Depth

20px cards, soft `0 18px 50px rgba(11,31,58,0.10)`. Rail uses navy blur glass. Primary CTA green→cyan gradient with soft green glow.

## Components

`.panel` white cards. `.btn-primary` gradient pill. `.shell-rail` / `.shell-link` for navigation. Official mark: `/brand/logo/rhythmiq-mark.svg`.

## Clinical encoding

Beat/tier: shape + pattern + colour (never colour alone). Brand greens/cyan/coral mapped for light surfaces.

## Do's and Don'ts

Do: follow brand kit tokens, logos, Manrope/DM Sans, light workspace + navy chrome.

Don't: dark glass hackathon theme, lime `#32cd32`, purple SaaS defaults, colour-only risk.
