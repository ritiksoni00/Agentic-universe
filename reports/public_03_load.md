# public_03_load notes (smoke)

Not a full end-to-end controller run (would dominate wall-clock here). Measured on the same 4-vCPU-class box:

| Metric | Value |
|---|---|
| Simulator history generation | ~45s wall |
| Controller history bootstrap (ingest + feature joins) | **204.8s** |
| One simulated day ingest (60-min ticks, no governance) | **44.78s** |
| Events after 1 sim day (incl. history) | 6,377,543 |
| Simulator RSS | ~403 MB |

**At 10×:** history bootstrap and per-day ingest would push past the 10-minute/day budget if left single-process and SQLite-synchronous. First break: SQLite write amplification on `call_stats` upserts and person_hour. Partition by `workflow_id` (process or shard DB), batch inserts, and drop per-person hour tracking under load.
