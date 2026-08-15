# Typeface licences

All three faces are **SIL Open Font License 1.1**, which permits bundling and serving
from a public repository and a public demo, including commercially, provided the fonts
are not sold on their own and the licence travels with them. That is why they are
self-hosted here rather than fetched from a third party at runtime: the spec requires no
request leaves our domain, and OFL compliance is easiest when the files and the notice
live together.

| File | Family | Licence | Source |
|---|---|---|---|
| `public-sans-latin-wght-normal.woff2` | Public Sans (variable) | OFL 1.1 | `@fontsource-variable/public-sans`, upstream github.com/uswds/public-sans |
| `source-serif-4-latin-wght-normal.woff2` | Source Serif 4 (variable) | OFL 1.1 | `@fontsource-variable/source-serif-4`, upstream github.com/adobe-fonts/source-serif |
| `jetbrains-mono-latin-wght-normal.woff2` | JetBrains Mono (variable) | OFL 1.1 | `@fontsource-variable/jetbrains-mono`, upstream github.com/JetBrains/JetBrainsMono |

Each is the **latin subset, variable weight axis**, retrieved 2026-08-15. Verified as
real WOFF2 by magic bytes (`wOF2`) rather than by HTTP status, because a CDN that serves
an error page with a 200 is a thing that happens and a status code is not content.

## Why these three

From the locked design spec, and each verdict has a reason rather than a preference:

- **Public Sans** for UI chrome. Tabular figures, variable, and it is the USWDS face, so
  its provenance reinforces the institutional tone of a curtailment console.
- **Source Serif 4** for drafted legal prose. It reads as a formal transitional document
  serif, which matters because those panes are supposed to look like real orders.
- **JetBrains Mono** for identifiers. Best-in-class glyph disambiguation (`0/O`, `1/l/I`)
  which is the entire job when the strings are application numbers and gage ids.
  Ligatures are OFF: these are identifiers, not code.
