"""Set when the process is stopping: work in flight finishes the stage it is in and never starts the next (lib/shutdown.ts)."""

import threading

SHUTDOWN = threading.Event()
