# Telegram webhook to the Worker, with D1 snapshots and queue

The Owner wanted status and chart replies within seconds, while price checks can take about a minute. Telegram pushes updates to a Cloudflare Worker webhook (not polled), which answers 📋 status / 📈 chart / list / delete-confirm in about 1 s from **snapshots the job pushes to D1** after each run (pre-rendered texts and chart PNGs). It queues everything else in D1 and dispatches a GitHub run at once.

`getUpdates` is unusable while a webhook is set, so the job reads commands from the D1 queue via the Worker.

**Considered:**
- **KV:** rejected; 1,000 writes/day and up to 60 s propagation are too tight.
- **The earlier 1-minute peek:** rejected; up to 90 s replies.
