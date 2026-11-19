# Rhythmiq Website Brand Kit

## Brand direction
Futuristic AI healthcare: confident, calm, precise, optimistic, and human-centered. Preserve the supplied identity: medical cross, ECG waveform, stethoscope motif, navy foundation, and vivid green accent.

## Core palette
| Token | Hex | Usage |
|---|---|---|
| Deep Navy | `#0B1F3A` | Primary brand, headers, dark surfaces |
| Medical Green | `#00B86B` | Primary action, positive emphasis |
| Green Bright | `#22D889` | Gradient highlight |
| Cyan | `#22D3EE` | AI/technology accent |
| Blue | `#234879` | Secondary brand blue |
| Coral | `#FF5F57` | Rare alert/accent |
| Light Gray | `#F5F7FA` | Page background |
| White | `#FFFFFF` | Cards and contrast surfaces |
| Ink | `#10243E` | Body text |
| Muted | `#64748B` | Secondary text |

## Typography
- Display/headings: Manrope, 600–800
- Body/UI: DM Sans, 400–700
- Fallback: system sans-serif
- Use generous line-height and avoid all-caps body copy.

## Logo use
- Prefer the original supplied logo for official brand applications.
- Use `rhythmiq-mark.svg` for scalable mark-only applications.
- Keep clear space around the mark approximately equal to the width of its stethoscope earpiece.
- Do not stretch, rotate, recolor, add shadows to, or place the logo on busy backgrounds.
- The original supplied PNG is preserved in `assets/logo/`.

## UI principles
- Large rounded cards (16–22px), subtle borders, restrained shadows.
- Navy for trust and structure; green for primary actions; cyan for AI/technology cues.
- Keep coral for occasional warnings, never as the primary brand color.
- Prefer accessible contrast, visible focus states, and clear labels.
- Motion should be subtle: short fades and small upward transitions, never distracting pulsing.

## Assets
- `assets/logo/`: supplied raster logo, extracted mark, scalable mark SVG, favicon.
- `assets/patterns/ecg-grid.svg`: ECG grid / waveform decorative background.
- `assets/icons/`: starter healthcare line icons.
- `assets/illustrations/brand-direction-board.png`: visual direction reference board generated for this kit.
- `css/rhythmiq.css`: reusable design tokens and responsive components.
- `index.html`: responsive landing-page starter.

## Before production
Replace placeholder contact email `hello@rhythmiq.example`, review all healthcare claims, and have a designer verify the vector mark against the original artwork before using it as a trademark master.


## Glassmorphism and gradients (updated direction)
- Use translucent navy glass surfaces over dark gradient backgrounds for navigation, feature panels, and AI dashboard previews.
- Suggested glass fill: `rgba(255,255,255,.085)`; border: `rgba(164,225,255,.26)`; blur: `18px`.
- Primary action gradient: `#20E58B → #22D3EE`.
- Heading gradient: `#16E58B → #22D3EE`.
- Add green/cyan glow sparingly to primary actions and key data visualizations.
- Keep text on glass panels near-white and maintain readable contrast; do not place small text over busy glow effects.
- Use glass as an accent, not everywhere: preserve solid light surfaces for long-form reading and forms.
- Respect `prefers-reduced-motion`; avoid constant pulsing or excessive animated glow.
