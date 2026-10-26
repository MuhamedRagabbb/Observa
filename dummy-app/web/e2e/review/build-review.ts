// Builds the side-by-side visual review page: each captured state next to its approved artboard,
// at 1440, 390 and 375. Output goes to e2e-output/review/ (git-ignored), with every image copied
// beside index.html so the folder can be opened or shared as is.
//
//   npm run e2e              # captures screenshots into e2e-output/screens/<viewport>/<state>.png
//   npm run e2e:review       # writes e2e-output/review/index.html
//
// Artboards are read from docs/design/artboards/ (local only, never committed).
import { copyFileSync, existsSync, mkdirSync, readdirSync, rmSync, writeFileSync } from "node:fs";
import { join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { COVERAGE, VIEWPORTS, type CoverageState, type Viewport } from "../coverage.ts";

export type ReviewState = Omit<CoverageState, "slice"> & { slice: string };
export type ReviewOptions = { states: ReviewState[]; screensDir: string; artboardsDir: string; outDir: string };
export type ReviewSummary = { cells: number; captured: number; approvedCells: number; missingArtboards: string[] };

const VIEWPORT_ORDER = Object.keys(VIEWPORTS) as Viewport[];

const escape = (text: string) =>
  text.replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c] as string);

/** The approved artboard closest to a derived width: the other mobile width first, then desktop. */
function nearestArtboard(state: ReviewState, viewport: Viewport) {
  const order: Viewport[] = viewport === "desktop-1440" ? ["mobile-390", "mobile-375"] : ["mobile-375", "mobile-390", "desktop-1440"];
  for (const candidate of order) {
    const file = state.approved[candidate];
    if (file) return { file, viewport: candidate };
  }
  return null;
}

export function buildReview({ states, screensDir, artboardsDir, outDir }: ReviewOptions): ReviewSummary {
  // Clear the contents, not the folder: the folder may be open in a local web server.
  mkdirSync(outDir, { recursive: true });
  for (const entry of readdirSync(outDir)) rmSync(join(outDir, entry), { recursive: true, force: true });
  mkdirSync(join(outDir, "artboards"), { recursive: true });
  const missingArtboards = new Set<string>();
  const copiedArtboards = new Set<string>();
  let cells = 0;
  let captured = 0;
  let approvedCells = 0;

  const artboardImage = (file: string, caption: string) => {
    const source = join(artboardsDir, file);
    if (!existsSync(source)) {
      missingArtboards.add(file);
      return `<div class="missing">Artboard not found locally<br><small>${escape(file)}</small></div>`;
    }
    if (!copiedArtboards.has(file)) {
      copyFileSync(source, join(outDir, "artboards", file));
      copiedArtboards.add(file);
    }
    return `<figure><img loading="lazy" src="artboards/${encodeURIComponent(file)}" alt="${escape(file)}"><figcaption>${escape(caption)}</figcaption></figure>`;
  };

  const sections = states.map((state) => {
    const rows = VIEWPORT_ORDER.map((viewport) => {
      cells += 1;
      const shot = join(screensDir, viewport, `${state.id}.png`);
      let capture = `<div class="missing">Not captured yet</div>`;
      if (existsSync(shot)) {
        captured += 1;
        mkdirSync(join(outDir, "screens", viewport), { recursive: true });
        copyFileSync(shot, join(outDir, "screens", viewport, `${state.id}.png`));
        capture = `<figure><img loading="lazy" src="screens/${viewport}/${state.id}.png" alt="${escape(state.id)} at ${viewport}"><figcaption>Implementation</figcaption></figure>`;
      }
      const approved = state.approved[viewport];
      let reference: string;
      let label: string;
      if (approved) {
        approvedCells += 1;
        label = "Approved artboard";
        reference = artboardImage(approved, approved);
      } else {
        label = "Derived (no artboard at this width)";
        const chosen = state.reference?.[viewport];
        const nearest = nearestArtboard(state, viewport);
        if (chosen) reference = artboardImage(chosen, `Reference: ${chosen}`);
        else if (nearest) reference = artboardImage(nearest.file, `Nearest approved: ${nearest.file} (${nearest.viewport})`);
        else reference = `<div class="missing">No artboard for this state</div>`;
      }
      return `<section class="cell ${viewport.startsWith("mobile") ? "phone" : "desk"}" data-cell="${escape(state.id)}/${viewport}"><h3>${viewport} · <span class="${approved ? "approved" : "derived"}">${label}</span></h3><div class="pair">${capture}${reference}</div></section>`;
    }).join("\n");
    const note = state.note ? `<p class="note">${escape(state.note)}</p>` : "";
    return `<article id="${escape(state.id)}"><h2>${escape(state.slice)} · ${escape(state.id)}</h2><p>${escape(state.screen)}</p>${note}${rows}</article>`;
  });

  const summary = { cells, captured, approvedCells, missingArtboards: [...missingArtboards].sort() };
  const html = `<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Storefront visual review</title>
<style>
:root{--paper:#f6f6f3;--ink:#151614;--muted:#55564f;--line:#e6e6e1;--fault:#9a4a35;--ok:#4f7457}
body{margin:0;padding:24px 16px;background:var(--paper);color:var(--ink);font:14px/1.5 system-ui,sans-serif}
main{max-width:1600px;margin:0 auto}h1{font-size:28px;margin:0 0 4px}h2{margin:32px 0 4px;font-size:20px}
h3{font-size:13px;margin:12px 0 6px;color:var(--muted)}.approved{color:var(--ok)}.derived{color:var(--fault)}
.note{color:var(--muted);font-style:italic}.pair{display:grid;grid-template-columns:1fr 1fr;gap:12px;align-items:start}
figure{margin:0;border:1px solid var(--line);background:#fff}figure img{display:block;width:100%;height:auto}
figcaption{padding:4px 8px;font-size:12px;color:var(--muted);border-top:1px solid var(--line)}
.missing{border:1px dashed var(--line);padding:24px;text-align:center;color:var(--muted)}
article{border-top:1px solid var(--line)}
/* Phone cells at about true width, so a 2x artboard and a 1x capture compare at the same scale. */
.phone .pair{grid-template-columns:repeat(2,minmax(0,400px))}@media (max-width:700px){.pair{grid-template-columns:1fr}}
</style></head><body><main>
<h1>Storefront visual review</h1>
<p>${summary.captured} of ${summary.cells} state/width cells captured · ${summary.approvedCells} cells have an approved artboard · left: implementation, right: artboard. Artboards hold sample data and real photos; compare layout, type and spacing, not data.</p>
${sections.join("\n")}
</main></body></html>
`;
  writeFileSync(join(outDir, "index.html"), html);
  return summary;
}

const isCli = process.argv[1] !== undefined && resolve(process.argv[1]) === fileURLToPath(import.meta.url);
if (isCli) {
  const web = join(import.meta.dirname, "..", "..");
  const harness: ReviewState[] = [
    { id: "shell-store", slice: "P4-0", screen: "Storefront shell (harness check)", approved: {}, derived: VIEWPORT_ORDER },
    { id: "shell-operator", slice: "P4-0", screen: "Operator shell (harness check)", approved: {}, derived: VIEWPORT_ORDER },
  ];
  const summary = buildReview({
    states: [...harness, ...COVERAGE],
    screensDir: join(web, "e2e-output", "screens"),
    artboardsDir: process.env.ARTBOARDS_DIR ?? join(web, "..", "..", "docs", "design", "artboards"),
    outDir: join(web, "e2e-output", "review"),
  });
  console.log(`review: ${join(web, "e2e-output", "review", "index.html")}`);
  console.log(JSON.stringify(summary));
}
