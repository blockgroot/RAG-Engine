/**
 * The one runnable check on chart colour. From `frontend/`:
 *   npm run check:colors
 *
 * No bundler and no test framework: Node strips the types itself, which is
 * why the import carries an explicit `.ts` and why this directory is
 * excluded from tsconfig -- Next's compiler rejects that extension.
 *
 * There is no React test stack here on purpose (CLAUDE.md: do not add one as a
 * side effect of a chart), but colour assignment is pure logic with a defect
 * history -- index-based colouring made "Done" teal in one chart and orange in
 * the next -- so it gets a check that imports the REAL implementation and
 * cannot drift from it. Exits non-zero on failure.
 */
import { cappedCategories, categoryColors, pick } from "../chartColors.ts";

const CASES: Record<string, string[]> = {
  "Linear states": ["Backlog", "Canceled", "Done", "In Progress", "Todo"],
  "GitHub repositories": [
    "18-sana/Chain-Guard",
    "18-sana/DAO",
    "18-sana/Basic-Training",
  ],
  "Notion pages": [
    "Leave Policy",
    "Health Allowance Policy",
    "Employee Referral Policy",
    "Compensatory Leave Policy",
  ],
  "Review verdicts": ["APPROVED", "CHANGES_REQUESTED", "COMMENTED"],
  "Slack channels": ["#rag-updates", "#general", "#random"],
};

let failed = 0;
const fail = (message: string) => {
  console.error("FAIL " + message);
  failed += 1;
};

// 1. No two categories in one chart share a colour, up to the palette size.
for (const [label, names] of Object.entries(CASES)) {
  const palette = categoryColors(names);
  const colors = names.map((n) => pick(palette, n));
  const distinct = new Set(colors).size;
  if (distinct !== Math.min(names.length, 6)) {
    fail(`${label}: ${names.length} names -> ${distinct} colours (${colors.join(", ")})`);
  } else {
    console.log(`ok   ${label}: ${distinct} distinct colours`);
  }
}

// 2. Meaning, where a category has one. A lifecycle chart in which "Canceled"
//    is a reassuring green is worse than one with arbitrary colours.
const lifecycle = categoryColors(CASES["Linear states"]);
const green = pick(lifecycle, "Done");
const red = pick(lifecycle, "Canceled");
if (green === red) fail("Done and Canceled share a colour");
if (!green.startsWith("#")) fail("Done did not get its semantic colour");
if (!red.startsWith("#")) fail("Canceled did not get its semantic colour");
console.log(`ok   Done=${green} Canceled=${red}`);

// 3. Stable under a different arrival order. This is the actual regression:
//    the index came from the order rows arrived.
const a = categoryColors(["Done", "Canceled", "Todo"]);
const b = categoryColors(["Todo", "Done", "Canceled"]);
for (const name of ["Done", "Canceled", "Todo"]) {
  if (pick(a, name) !== pick(b, name)) {
    fail(`${name} moved colour when the order changed`);
  }
}
console.log("ok   colours survive a change in arrival order");

// 4. An unnamed group must not crash or take a semantic slot.
const empty = categoryColors([null, undefined, ""]);
if (!pick(empty, null)) fail("a null category has no colour");
console.log("ok   unnamed categories resolve");


// 5. Categories are capped to the palette, so a legend can never carry two
//    identical swatches. Eleven real Notion pages drew eleven slices over six
//    colours -- five of them duplicates.
const eleven = [
  "Compensatory Leave Policy", "Employee Referral Policy", "Leave Policy",
  "Mental Health Policy", "Office Parties Policy", "Office Regularization Policy",
  "Prevention of Sexual Harassment (POSH) Policy", "Upskilling Policy",
  "Performance Bonus Policy", "Syvora Medical Health Insurance Policy",
  "Health Allowance Policy",
].map((name) => ({ name, value: 1 }));

const capped = cappedCategories(eleven);
if (capped.length > 6) fail(`capped to ${capped.length}, palette holds 6`);
const cappedPalette = categoryColors(capped.map((r) => r.name));
const cappedColors = capped.map((r) => pick(cappedPalette, r.name));
if (new Set(cappedColors).size !== capped.length) {
  fail(`capped set still repeats a colour: ${cappedColors.join(", ")}`);
}
const folded = capped[capped.length - 1];
if (!folded.name.startsWith("Other")) fail("the remainder was dropped, not folded");
if (folded.value !== eleven.length - (capped.length - 1)) {
  fail(`Other holds ${folded.value}, expected ${eleven.length - (capped.length - 1)}`);
}
console.log(`ok   11 categories -> ${capped.length} distinct (${folded.name})`);

if (failed) process.exit(1);

if (failed) {
  console.error(`\n${failed} failure(s)`);
  process.exit(1);
}
console.log("\nall colour checks passed");
