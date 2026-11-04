// Runs the collectors' pure helpers on fixed inputs and prints JSON, for the Python port's tests (tests/engine/source_fixtures.json).
//   node scripts/gen-source-fixtures.ts > ../tests/engine/source_fixtures.json
import { parseLooseDate, readPublicationDate, readRelativeDate } from "@aihot/backend/sources/dates";

const dates = [
  "2026-09-26", "2026-09-26 10:00", "2026-09-26T10:00:00", "2026-09-26T10:00:00Z", "2026-09-26T10:00:00+08:00", "2026-09-26T10:00:00.123Z", "2026-09-26 10:00:00 +0000",
  "2026/09/26", "2026.9.26", "2026年9月26日", "2026年9月26日 10:30", "2026年9月26日10:30", "20260926103000", "2026-02-30", "2026-13-01", "2026-09-26 25:00",
  "9/26/2026", "09/26/2026 3:45 PM", "26/09/2026", "Sep 26, 2026", "September 26, 2026", "Sep 26 2026", "Sept 26, 2026", "26 Sep 2026", "26 September 2026", "26 Sep 26",
  "Sep 26, 2026 at 10:30", "Sep 26, 2026 10:30 PM", "Sep 26, 2026 10:30 PM GMT", "Sep 26, 2026 10:30 EST", "Sep 26, 2026 10:30 CST", "Sat, 26 Sep 2026 10:30:00 GMT",
  "Sat, 26 Sep 2026 10:30:00 +0200", "26 Sep 2026 10:00 UTC", "Feb 30, 2026", "Foo 26, 2026", "Published Sep 26th, 2026", "1st Oct 2026", "Oct 1st, 2026 08:00 AM", "Oct 1, 2026 13:00 PM",
  "10:00Z", "", "   ", "not a date", "2026", "2026-09", "Updated: 2026-09-26 08:15 by someone", "2026-09-26 08:15:30.500", "12/31/2026 11:59 PM PST", "Mon, 05 Oct 2026 08:00:00 -0400",
  "2026-09-26T10:00:00-05:00", "2026-09-26 10:00 PM", "2026-09-26 00:00 AM",
];
const offsets = ["+08:00", "+00:00", "-05:00"];
const relative = ["20小時前", "2天前", "昨天", "前天", "今天 15:46", "昨天 08:05", "3 hours ago", "16h ago", "5 minutes ago", "just now", "剛剛", "版面之外·今天 15:46", "47小時前", "1 day ago", "yesterday", "3天", "兩天前", "", "999分鐘前"];
const NOW = new Date("2026-11-04T09:30:15.250Z");
const out = {
  parseLooseDate: offsets.flatMap((o) => dates.map((d) => ({ in: d, offset: o, out: parseLooseDate(d, o)?.toISOString() ?? null }))),
  readPublicationDate: dates.map((d) => ({ in: d, out: ((r) => ({ at: r.publishedAt?.toISOString() ?? null, time: r.publicationTime }))(readPublicationDate(d)) })),
  readRelativeDate: offsets.flatMap((o) => relative.map((d) => ({ in: d, offset: o, out: ((r) => ({ at: r.publishedAt?.toISOString() ?? null, time: r.publicationTime }))(readRelativeDate(d, NOW, o)) }))),
  now: NOW.toISOString(),
};
process.stdout.write(JSON.stringify(out));
