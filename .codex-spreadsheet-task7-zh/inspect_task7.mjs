import { FileBlob, SpreadsheetFile } from "@oai/artifact-tool";
const input = await FileBlob.load("../outputs/formal-task007-3x3-minimaxm3-20260821-02-audit/task7_final_audit.xlsx");
const wb = await SpreadsheetFile.importXlsx(input);
console.log((await wb.inspect({kind:"workbook,sheet,table",maxChars:20000,tableMaxRows:8,tableMaxCols:12,tableMaxCellChars:160})).ndjson);
for (const s of wb.worksheets.items) {
  const used = s.getUsedRange();
  console.log("SHEET", s.name, used?.address, used?.values?.slice(0,12));
  console.log("FORMULAS", s.name, used?.formulas?.flat().filter(Boolean).slice(0,30));
}
