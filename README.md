# fanedit-daily-changelog

Daily release-notes digest for the Fan Edit Fan Club. Every day at **9pm ET**,
the `daily-changelog` workflow posts a bullet list of the day's
platform/channel/automation updates to the **#discord-moderators** Discord
channel (via the Optimus bot).

## How entries get added

`changelog/entries.jsonl` holds one JSON object per line:

```json
{"ts": "2026-09-26T04:15:00+00:00", "text": "What changed, in one line."}
```

The agent appends an entry every time it ships a change to any Fan Edit Fan
Club platform, channel, or automation. The user can also add entries by
editing the file on GitHub or just saying "log: ..." in chat.

The workflow posts every entry newer than the last post (tracked in
`state/changelog.json`) and skips quietly on days with no changes.

## Setup

1. Add the `DISCORD_BOT_TOKEN` repo secret (Settings → Secrets and variables →
   Actions) — same bot token used by the other Fan Edit Fan Club automations.
2. That's it. First post goes out at the next 9pm ET run.
