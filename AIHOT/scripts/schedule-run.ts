// Runs the source schedule, the daily interval adaptation and the processing sweep once, and prints their results.
import { closeDb } from "@aihot/backend/db";
import { adaptIntervals, scheduleDueSources } from "@aihot/backend/sources/collect";
import { sweepUnprocessed } from "@aihot/backend/jobs/content";
import { stopBoss } from "@aihot/backend/jobs/queue";

const scheduled = await scheduleDueSources();
const adapted = await adaptIntervals();
const swept = await sweepUnprocessed();
console.log(JSON.stringify({ scheduled, adapted, swept }));
await stopBoss();
await closeDb();
