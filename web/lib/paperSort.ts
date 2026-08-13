import type { Paper } from "@/components/PaperList";

function sortableDate(paper: Paper): string {
  if (paper.date) {
    if (/^\d{4}$/.test(paper.date)) return `${paper.date}-00-00`;
    if (/^\d{4}-\d{2}$/.test(paper.date)) return `${paper.date}-00`;
    return paper.date;
  }
  return `${paper.year ?? 0}-00-00`;
}

export function comparePapersByRecent(a: Paper, b: Paper): number {
  const dateDiff = sortableDate(b).localeCompare(sortableDate(a));
  if (dateDiff !== 0) return dateDiff;
  return b.id - a.id;
}
