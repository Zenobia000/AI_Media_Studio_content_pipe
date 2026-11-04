// Computes the hot ranking and the hourly heat snapshot at a given instant (argv[2], ISO), for the Python port's tests to compare.
import { closeDb } from "@aihot/backend/db";
import { computeHotRanking, snapshotHeat } from "@aihot/backend/events/hot";

const at = new Date(process.argv[2]!);
const ranking = await computeHotRanking(at);
const heat = await snapshotHeat(at);
console.log(JSON.stringify({ ranking, heat }));
await closeDb();
