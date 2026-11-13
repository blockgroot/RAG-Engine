/** Filename -> a short format badge ("PDF", "DOCX"). One place, so the composer
 *  chip and the provenance pill always agree. */
const KINDS: Record<string, string> = {
  pdf: "PDF",
  docx: "DOCX",
  doc: "DOC",
  csv: "CSV",
  tsv: "TSV",
  txt: "TXT",
  md: "MD",
  markdown: "MD",
  log: "LOG",
  json: "JSON",
};

export function fileKind(filename: string): string {
  const ext = filename.split(".").pop()?.toLowerCase() ?? "";
  return KINDS[ext] ?? "FILE";
}

/** The badge for a set of files: the one kind when they agree, otherwise
 *  "FILES". Listing three formats in a pill is longer than the pill. */
export function fileKindLabel(filenames: string[]): string {
  const kinds = new Set(filenames.map(fileKind));
  if (kinds.size === 0) return "FILE";
  if (kinds.size === 1) return [...kinds][0];
  return "FILES";
}
