# Third-party notices

Both bundles self-host these fonts. They are installed from exact-pinned npm packages and
copied into each build. Each build also ships the licence text at
`licenses/<family>-OFL.txt`. No font is loaded from a third-party server.

| Family | Package | Version | Files shipped | Licence | Copyright |
|---|---|---|---|---|---|
| Instrument Serif | `@fontsource/instrument-serif` | 5.3.0 | Latin, 400 upright (woff2) | SIL Open Font License 1.1 | 2022 The Instrument Serif Project Authors (github.com/Instrument/instrument-serif) |
| Inter | `@fontsource-variable/inter` | 5.3.0 | Latin, variable weight upright (woff2) | SIL Open Font License 1.1 | 2016 The Inter Project Authors (github.com/rsms/inter) |
| JetBrains Mono | `@fontsource-variable/jetbrains-mono` | 5.3.0 | Latin, variable weight upright (woff2) | SIL Open Font License 1.1 | 2020 The JetBrains Mono Project Authors (github.com/JetBrains/JetBrainsMono) |

The fonts are used unmodified and are not sold on their own. The npm packages' own
JavaScript and CSS are not shipped: `src/shared/fonts.css` declares the font faces itself.
Other runtime dependencies (React, React Router, TanStack Query) are MIT-licensed.
