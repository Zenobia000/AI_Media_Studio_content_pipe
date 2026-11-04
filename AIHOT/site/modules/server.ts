// The backend of the site's modules, installed by the api and the worker when they start (site/modules/index.ts).
// The collection and fetch schedules that used to be a module here (inbox) now run as `newsroom run-scheduler` (../src/newsroom/collect/schedule.py).
import type { ServerModule } from "@aihot/backend/modules";

export const SERVER_MODULES: readonly ServerModule[] = [];
