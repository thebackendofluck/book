# data/assets — card, chip, and table art

All artwork in this directory is original work released into the **public
domain under the [CC0 1.0 Universal](https://creativecommons.org/publicdomain/zero/1.0/)**
dedication. You may copy, modify, distribute, or perform the work — even
for commercial purposes — without asking permission.

Files are produced deterministically by `build/generate-assets.js`; re-run
the generator to rebuild them from source.

```
cards/   52 SVGs (<rank>_of_<suit>.svg) + back.svg
chips/   denomination discs: 1.svg, 5.svg, 25.svg, 100.svg, 500.svg, 1000.svg
table/   felt.svg, logo.svg
manifest.json   SHA-256 of every asset (startup integrity check)
```

Every asset carries `<!-- CC0-1.0 Public Domain -->` in-file so downstream
forks inherit the dedication.

## Why not import an existing deck?

Most popular "free" SVG decks ship under LGPL (e.g. the `htdebeer/SVG-cards`
and Google's `vector-playing-cards`). LGPL assets would contaminate the
proprietary poker-client licence. These in-house SVGs avoid that risk
entirely and stay small (~1 KB per card) so git diffs remain readable.

## Integrity

`lib/graphics/render.ts` verifies every asset's SHA-256 against
`manifest.json` at startup and logs a warning on any mismatch. If an
attacker swaps a card image on disk, the warning fires immediately.
