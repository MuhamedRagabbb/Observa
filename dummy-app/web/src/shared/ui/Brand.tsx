// The OBSERVA wordmark and mark, redrawn as vector paths from the approved artboards (blocker
// B3: no logo files were delivered). Letters sit on a 100-unit cap height in high-contrast
// roman capitals: thick stems 12, hairlines 3.5, before the outline added in Wordmark.
// Designer files can replace these paths without changing any caller.
import styles from "./Brand.module.css";

/** A rectangle as path data (stems, bars and serifs). */
const r = (x: number, y: number, w: number, h: number) => `M${x} ${y}h${w}v${h}h${-w}z`;

/** The mark: an open, weighted ring whose hairline top sweeps out into a tail. */
const MARK =
  "M98 8C80 14 60 16 45 18C22 18 7 36 7 58C7 82 24 98 44 98C66 98 83 82 83 62C83 48 78 36 70 28" +
  "L66 31C72 40 74 50 74 62C74 79 62 90 46 90C31 90 22 79 22 60C22 41 33 24 48 22C64 20 82 14 98 8Z";

/** Each letter is a list of shapes, drawn as separate paths so overlaps never cut holes. */
const LETTERS: { x: number; shapes: string[]; evenOdd?: boolean }[] = [
  {
    // O: thick sides, hairline top and bottom (vertical stress).
    x: 200,
    evenOdd: true,
    shapes: ["M0 50A53 51.5 0 1 0 106 50A53 51.5 0 1 0 0 50ZM13 50A40 48 0 1 1 93 50A40 48 0 1 1 13 50Z"],
  },
  {
    // B
    x: 336,
    shapes: [
      r(8, 0, 12, 100),
      r(2, 0, 46, 3.5),
      r(20, 47, 32, 3.5),
      r(2, 96.5, 52, 3.5),
      "M48 0A23 25.25 0 0 1 48 50.5L48 47A11 21.75 0 0 0 48 3.5Z",
      "M52 47A27 26.5 0 0 1 52 100L52 96.5A14 23 0 0 0 52 50.5Z",
    ],
  },
  {
    // S: one outline, hairline ends with small vertical serifs, thick diagonal spine.
    x: 446,
    shapes: [
      "M50 2C44 0.5 37 0 30 0C14 0 3.5 10 3.5 24C3.5 38 15 46 26 53C37 60 45.5 64 45.5 74" +
        "C45.5 88 38 96.5 28 96.5C18 96.5 10 93 6 86L3 86L3 97C10 99.5 18 100 28 100" +
        "C45 100 56.5 90 56.5 74C56.5 60 46 52 34 45C23 39 14.5 34 14.5 24C14.5 11 21 3.5 30 3.5" +
        "C38 3.5 46 8 50 15L53 15L53 1Z",
    ],
  },
  {
    // E
    x: 532,
    shapes: [
      r(8, 0, 12, 100),
      r(2, 0, 62, 3.5),
      r(61, 0, 3, 20),
      r(20, 48, 34, 3.5),
      r(51, 41, 3, 17),
      r(2, 96.5, 66, 3.5),
      r(65, 78, 3, 22),
    ],
  },
  {
    // R
    x: 632,
    shapes: [
      r(8, 0, 12, 100),
      r(2, 0, 46, 3.5),
      "M48 0A25 26 0 0 1 48 52L48 48.5A12 22.5 0 0 0 48 3.5Z",
      r(20, 48.5, 28, 3.5),
      "M38 50L51 50L86 100L73 100Z",
      r(0, 96.5, 28, 3.5),
      r(66, 96.5, 32, 3.5),
    ],
  },
  {
    // V: thick left diagonal, hairline right.
    x: 760,
    shapes: ["M6 0L20 0L55 100L49 100Z", "M97 0L101 0L55 100L51 100Z", r(0, 0, 28, 3.5), r(88, 0, 18, 3.5)],
  },
  {
    // A: hairline left, thick right diagonal.
    x: 894,
    shapes: [
      "M46 0L50 0L7 100L3 100Z",
      "M46 0L59 0L98 100L85 100Z",
      r(22, 63, 49, 3.5),
      r(-2, 96.5, 20, 3.5),
      r(76, 96.5, 32, 3.5),
    ],
  },
];

export function Wordmark({ className }: { className?: string }) {
  return (
    <svg
      className={[styles.wordmark, className].filter(Boolean).join(" ")}
      viewBox="0 -30 1004 140"
      role="img"
      aria-label="OBSERVA"
      focusable="false"
      fill="currentColor"
      // A thin outline of the same colour adds weight evenly (stems 14, hairlines 5.5), matching
      // the artboards' weight at header size.
      stroke="currentColor"
      strokeWidth="2"
      strokeLinejoin="round"
    >
      <path d={MARK} transform="translate(0 -40) scale(1.5)" strokeWidth="1.4" />
      {LETTERS.map(({ x, shapes, evenOdd }) => (
        <g key={x} transform={`translate(${x} 0)`}>
          {shapes.map((d) => (
            <path key={d} d={d} fillRule={evenOdd ? "evenodd" : undefined} />
          ))}
        </g>
      ))}
    </svg>
  );
}

/** The mark alone (Cart drawer, empty state). Decorative unless given a label. */
export function BrandMark({ className, label }: { className?: string; label?: string }) {
  return (
    <svg
      className={className}
      viewBox="4 4 98 98"
      fill="currentColor"
      focusable="false"
      {...(label ? { role: "img", "aria-label": label } : { "aria-hidden": true })}
    >
      <path d={MARK} />
    </svg>
  );
}
