# Purumi Music Bot

Telegram group voice-chat music bot using a Telegram bot account for commands and a separate assistant user account for voice chat. Python + Pyrogram + PyTgCalls + yt-dlp + Flask health endpoints.

## Features

- Search public YouTube results by title with `/play <song title>`.
- Stream audio into a group voice chat through the assistant account.
- Queue, now-playing, pause, resume, skip and stop commands.
- `/` and `/healthz` HTTP endpoints for hosting health checks.
- Environment-based secrets; no tokens are stored in source files.

## Important limits

This repository has been scaffolded for deployment but has **not been verified against your Telegram account, target group, or Faable runtime yet**. Telegram voice-chat APIs and PyTgCalls versions can change. Deploy first, inspect logs, then test in a private group.

The assistant account must already be a member of the target group. Start the group's voice chat manually first. If Telegram requires the assistant to have permission to speak/manage the call, grant the required permissions. Do not use an account you cannot afford to lose; Telegram may restrict accounts that automate activity excessively.

Search results and audio availability are controlled by YouTube and the uploader. Respect copyright and the service's terms. This project does not bypass DRM or access private content.

## 1. Create Telegram credentials

1. Create a bot with [@BotFather](https://t.me/BotFather), then copy its bot token.
2. Obtain `API_ID` and `API_HASH` from [my.telegram.org](https://my.telegram.org).
3. Use a separate Telegram user account as the assistant, and add it to the group where you will test.

## 2. Generate the assistant session string locally

Do this on your own computer or Termux, **not in a public GitHub workflow**. Never commit a session string.

Install Python 3.12 and project dependencies, then set `API_ID` and `API_HASH` in your local shell. Run:

```bash
python -m pip install -r requirements.txt
python generate_session.py
```

Sign in to the assistant account when prompted. Copy the printed `SESSION_STRING` privately. Anyone who gets this value may be able to access that Telegram account.

## 3. Configure Faable secrets

In Faable, open the app's Environment/Secrets settings and add these variables:

| Variable | Required | Meaning |
|---|---|---|
| `API_ID` | Yes | Telegram API ID from my.telegram.org |
| `API_HASH` | Yes | Telegram API hash |
| `BOT_TOKEN` | Yes | BotFather token |
| `SESSION_STRING` | Yes | Assistant user's session string |
| `PORT` | Provided by host | HTTP health server port; default 8080 locally |
| `MAX_QUEUE` | No | Maximum queued songs; default 20 |
| `MAX_TRACK_SECONDS` | No | Maximum song length in seconds; default 720 |
| `LOG_LEVEL` | No | Default INFO |

Do not put real values in `.env.example`, GitHub files, screenshots, or public chat. If a token/session is exposed, revoke or regenerate it immediately.

## 4. Deploy on Faable

1. Open the Faable dashboard and create an app linked to this repository.
2. Use the managed **Python** buildpack. The repo includes `requirements.txt`, `runtime.txt`, and `faable.json`.
3. Add the four required secrets above.
4. Trigger a deployment and watch the build/runtime logs.
5. Open the app URL. The root endpoint should return JSON status; `/healthz` should return HTTP 200 only after both Telegram clients connect.

Faable's free plan has its own resource and runtime limits. A healthy HTTP endpoint does not guarantee uninterrupted audio playback; test whether the selected plan keeps the process alive for your use case.

## 5. Telegram setup and test

1. Add the controller bot and assistant user to a private test supergroup.
2. Start a voice chat manually in that group.
3. Confirm the assistant user can join/speak and is not restricted.
4. Send commands in the group:

| Command | Action |
|---|---|
| `/start`, `/help` | Show help |
| `/play Daft Punk One More Time` | Search and play a result |
| `/queue` | Show queued songs |
| `/now` | Show current track |
| `/pause` | Pause playback |
| `/resume` | Resume playback |
| `/skip` | Skip current track |
| `/stop` | Stop playback and clear queue |
| `/ping` | Check controller status |

## Troubleshooting

- **Missing environment variables:** check that the four required secrets are present and saved in the correct Faable app.
- **Assistant cannot connect:** regenerate the session string locally and replace `SESSION_STRING`; check API ID/hash.
- **Bot commands are ignored:** check bot token, ensure the bot is in the group, and use `/help@YourBotUsername` if command routing conflicts.
- **Assistant does not join/play:** start the voice chat first, check membership/permissions and inspect Faable logs.
- **Search fails:** yt-dlp may need an update or YouTube may block the hosting IP. Re-deploy after updating dependencies; do not upload account cookies publicly.
- **Build fails around PyTgCalls/ntgcalls:** inspect the build log. If a compatible prebuilt wheel is unavailable for the selected Python/runtime, this free managed buildpack may not be able to install it; Docker builds require a Faable plan that supports Docker.
- **Queue does not auto-advance:** check logs for update events from the installed PyTgCalls version. This behavior needs a live voice-chat test; if the event type differs in the installed release, the callback may need a compatibility adjustment.

## Local run

```bash
python -m pip install -r requirements.txt
export API_ID='...'
export API_HASH='...'
export BOT_TOKEN='...'
export SESSION_STRING='...'
python main.py
```

Then visit `http://127.0.0.1:8080/healthz`.

## License

Personal project scaffold. Check the licenses and terms of the upstream libraries and media services before redistributing or operating publicly.
