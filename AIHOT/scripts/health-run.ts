// Prints sourceHealth at a given instant (argv[2], ISO) and the report text of its sources, for the Python port's tests to compare.
import { closeDb } from "@aihot/backend/db";
import { rhythmText, SOURCE_PROBLEMS, sourceHealth, sourceHealthList, type QuietSource } from "@aihot/backend/sources/health";

const groups = await sourceHealth(Date.parse(process.argv[2]!));
const texts = groups.flatMap((g) => [
  ...g.unknown.map((s) => rhythmText(s as QuietSource)),
  ...g.overdue.map((s) => rhythmText(s as QuietSource)),
  ...SOURCE_PROBLEMS.map((p) => sourceHealthList(g.sources.filter((s) => p.observedAt(s)), p.describe)),
]);
console.log(JSON.stringify({ groups, texts }));
await closeDb();
