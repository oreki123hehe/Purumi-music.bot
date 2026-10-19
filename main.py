"""
Purumi Music Bot — Telegram voice-chat music bot.
Bot account handles commands; assistant user account joins the voice chat.
"""
import asyncio
import json
import logging
import os
import subprocess
import threading
import time
from collections import defaultdict, deque
from typing import Any

from flask import Flask, jsonify
from pyrogram import Client, filters, idle
from pyrogram.errors import RPCError
from pytgcalls import PyTgCalls
from pytgcalls.types import MediaStream

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
log = logging.getLogger("purumi")

API_ID = os.getenv("API_ID", "").strip()
API_HASH = os.getenv("API_HASH", "").strip()
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
SESSION_STRING = os.getenv("SESSION_STRING", "").strip()
PORT = int(os.getenv("PORT", "8080"))
MAX_QUEUE = int(os.getenv("MAX_QUEUE", "20"))
MAX_SEARCH_SECONDS = int(os.getenv("MAX_TRACK_SECONDS", "720"))
COOKIE_FILE = os.getenv("YOUTUBE_COOKIE_FILE", "").strip()

web = Flask(__name__)
started_at = time.time()
runtime_status: dict[str, Any] = {
    "status": "starting",
    "bot": False,
    "assistant": False,
    "voice_chats": {},
    "last_error": None,
}

# Each chat gets its own queue. Queue items are metadata plus a temporary stream URL.
queues: dict[int, deque] = defaultdict(deque)
current_tracks: dict[int, dict | None] = defaultdict(lambda: None)
queue_locks: dict[int, asyncio.Lock] = {}
bot_client: Client | None = None
calls: PyTgCalls | None = None
bot_loop: asyncio.AbstractEventLoop | None = None


@web.get("/")
def home():
    return jsonify({
        "name": "Purumi Music Bot",
        "status": runtime_status["status"],
        "bot_connected": runtime_status["bot"],
        "assistant_connected": runtime_status["assistant"],
        "uptime_seconds": int(time.time() - started_at),
        "commands": ["/play", "/queue", "/now", "/pause", "/resume", "/skip", "/stop", "/help"],
    })


@web.get("/healthz")
def healthz():
    healthy = runtime_status["status"] == "running" and runtime_status["bot"] and runtime_status["assistant"]
    return jsonify({"ok": healthy, **runtime_status}), (200 if healthy else 503)


def start_http_server():
    # Faable routes requests to the PORT environment variable.
    web.run(host="0.0.0.0", port=PORT, debug=False, use_reloader=False)


def require_config():
    missing = [name for name, value in (
        ("API_ID", API_ID), ("API_HASH", API_HASH),
        ("BOT_TOKEN", BOT_TOKEN), ("SESSION_STRING", SESSION_STRING),
    ) if not value]
    if missing:
        raise RuntimeError("Missing environment variables: " + ", ".join(missing))


async def search_youtube(query: str) -> dict:
    """Search one public YouTube result and return its direct audio URL."""
    if not query or not query.strip():
        raise ValueError("Masukkan judul lagu setelah perintah /play.")
    query = query.strip()
    if len(query) > 180:
        raise ValueError("Judul terlalu panjang (maksimal 180 karakter).")

    cmd = [
        "yt-dlp", "--no-warnings", "--no-playlist",
        "--dump-single-json", "--format", "bestaudio/best",
        "--default-search", "ytsearch1",
    ]
    if COOKIE_FILE and os.path.isfile(COOKIE_FILE):
        cmd.extend(["--cookies", COOKIE_FILE])
    cmd.append(query)

    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        detail = stderr.decode("utf-8", "replace")[-700:]
        log.warning("yt-dlp search failed: %s", detail)
        raise RuntimeError("Pencarian lagu gagal. Coba judul lain atau periksa log hosting.")
    try:
        data = json.loads(stdout.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise RuntimeError("Hasil pencarian tidak bisa dibaca.") from exc

    duration = int(data.get("duration") or 0)
    if duration and duration > MAX_SEARCH_SECONDS:
        raise ValueError(f"Lagu terlalu panjang. Batas saat ini {MAX_SEARCH_SECONDS // 60} menit.")
    url = data.get("url")
    if not url:
        # Some extractors return formats rather than a top-level URL.
        formats = data.get("formats") or []
        candidates = [f for f in formats if f.get("url") and f.get("acodec") not in (None, "none")]
        if candidates:
            url = candidates[-1]["url"]
    if not url:
        raise RuntimeError("URL audio tidak ditemukan untuk hasil tersebut.")

    return {
        "title": data.get("title") or query,
        "url": url,
        "duration": duration,
        "webpage_url": data.get("webpage_url", ""),
        "requested": query,
    }


def fmt_duration(seconds: int | None) -> str:
    if not seconds:
        return "durasi tidak diketahui"
    return f"{seconds // 60}:{seconds % 60:02d}"


async def send_text(message, text: str):
    try:
        await message.reply_text(text, disable_web_page_preview=True)
    except Exception:
        log.exception("Could not send Telegram reply")


async def play_track(chat_id: int, track: dict):
    if calls is None:
        raise RuntimeError("Voice-call engine belum siap.")
    # PyTgCalls streams a direct HTTPS audio URL through FFmpeg/ntgcalls.
    await calls.play(chat_id, MediaStream(track["url"]))
    current_tracks[chat_id] = track
    runtime_status["voice_chats"][str(chat_id)] = "playing"
    log.info("Playing %s in %s", track["title"], chat_id)


async def play_next(chat_id: int, message=None):
    lock = queue_locks.setdefault(chat_id, asyncio.Lock())
    async with lock:
        while queues[chat_id]:
            track = queues[chat_id].popleft()
            try:
                await play_track(chat_id, track)
                if message:
                    await send_text(message, f"▶️ Memutar: **{track['title']}** ({fmt_duration(track['duration'])})")
                return
            except Exception as exc:
                log.exception("Failed to start stream")
                runtime_status["last_error"] = str(exc)
                if message:
                    await send_text(message, f"Gagal memutar **{track['title']}**. Mencoba lagu berikutnya…")
        current_tracks[chat_id] = None
        runtime_status["voice_chats"][str(chat_id)] = "idle"
        if message:
            await send_text(message, "Antrean habis. Tambahkan lagu dengan /play judul lagu.")


async def enqueue_query(message, query: str):
    chat_id = message.chat.id
    if len(queues[chat_id]) >= MAX_QUEUE:
        await send_text(message, f"Antrean penuh (maksimal {MAX_QUEUE} lagu).")
        return
    await send_text(message, f"🔎 Mencari: **{query}**")
    try:
        track = await search_youtube(query)
    except (ValueError, RuntimeError) as exc:
        await send_text(message, f"⚠️ {exc}")
        return
    track["requested_by"] = message.from_user.first_name if message.from_user else "unknown"

    if current_tracks[chat_id] is None and not queues[chat_id]:
        try:
            await play_track(chat_id, track)
            await send_text(message, f"▶️ Memutar: **{track['title']}** ({fmt_duration(track['duration'])})")
        except Exception as exc:
            log.exception("Could not start first track")
            runtime_status["last_error"] = str(exc)
            await send_text(message, "Tidak bisa memulai audio. Pastikan assistant sudah menjadi anggota grup dan voice chat sedang aktif, lalu coba /play lagi.")
    else:
        queues[chat_id].append(track)
        await send_text(message, f"➕ Ditambahkan ke antrean #{len(queues[chat_id])}: **{track['title']}**")


def command_filters():
    return filters.group & filters.command


async def bot_main():
    global bot_client, calls, bot_loop
    require_config()
    bot_loop = asyncio.get_running_loop()

    bot_client = Client(
        name="purumi_controller",
        api_id=int(API_ID),
        api_hash=API_HASH,
        bot_token=BOT_TOKEN,
        in_memory=True,
    )
    assistant = Client(
        name="purumi_assistant",
        api_id=int(API_ID),
        api_hash=API_HASH,
        session_string=SESSION_STRING,
        in_memory=True,
    )

    # Controller command handlers
    @bot_client.on_message(filters.command("start") & filters.group)
    async def start_cmd(_, message):
        await send_text(message, "🎵 **Purumi Music Bot aktif!**\nGunakan /play judul lagu untuk memutar musik di voice chat.")

    @bot_client.on_message(filters.command("help") & filters.group)
    async def help_cmd(_, message):
        await send_text(message, (
            "**Purumi Music Bot — Perintah**\n"
            "/play judul lagu — cari dan putar lagu\n"
            "/queue — lihat antrean\n"
            "/now — lagu yang sedang diputar\n"
            "/pause — jeda audio\n"
            "/resume — lanjutkan audio\n"
            "/skip — lewati lagu\n"
            "/stop — hentikan audio dan kosongkan antrean\n"
            "/ping — periksa status bot\n\n"
            "Assistant harus sudah masuk ke grup dan voice chat harus aktif."
        ))

    @bot_client.on_message(filters.command("play") & filters.group)
    async def play_cmd(_, message):
        query = " ".join(message.command[1:]).strip()
        if not query:
            await send_text(message, "Format: /play judul lagu")
            return
        await enqueue_query(message, query)

    @bot_client.on_message(filters.command("queue") & filters.group)
    async def queue_cmd(_, message):
        chat_id = message.chat.id
        items = list(queues[chat_id])
        current = current_tracks[chat_id]
        lines = [f"🎶 **Sedang diputar:** {current['title']}" if current else "🎶 Tidak ada lagu yang sedang diputar."]
        if items:
            lines.append("\n**Antrean:**")
            lines.extend(f"{i}. {item['title']} ({fmt_duration(item['duration'])})" for i, item in enumerate(items, 1))
        else:
            lines.append("\nAntrean kosong.")
        await send_text(message, "\n".join(lines))

    @bot_client.on_message(filters.command("now") & filters.group)
    async def now_cmd(_, message):
        track = current_tracks[message.chat.id]
        if not track:
            await send_text(message, "Belum ada lagu yang diputar.")
        else:
            await send_text(message, f"🎧 **Sekarang:** {track['title']} ({fmt_duration(track['duration'])})")

    @bot_client.on_message(filters.command("pause") & filters.group)
    async def pause_cmd(_, message):
        try:
            await calls.pause(message.chat.id)
            runtime_status["voice_chats"][str(message.chat.id)] = "paused"
            await send_text(message, "⏸️ Audio dijeda.")
        except Exception as exc:
            log.warning("Pause failed: %s", exc)
            await send_text(message, "Tidak bisa menjeda. Pastikan ada lagu yang sedang diputar.")

    @bot_client.on_message(filters.command("resume") & filters.group)
    async def resume_cmd(_, message):
        try:
            await calls.resume(message.chat.id)
            runtime_status["voice_chats"][str(message.chat.id)] = "playing"
            await send_text(message, "▶️ Audio dilanjutkan.")
        except Exception as exc:
            log.warning("Resume failed: %s", exc)
            await send_text(message, "Tidak bisa melanjutkan. Pastikan audio sedang dijeda.")

    @bot_client.on_message(filters.command("skip") & filters.group)
    async def skip_cmd(_, message):
        chat_id = message.chat.id
        try:
            await calls.leave_call(chat_id)
        except Exception:
            pass
        current_tracks[chat_id] = None
        await send_text(message, "⏭️ Melewati lagu…")
        await play_next(chat_id, message)

    @bot_client.on_message(filters.command("stop") & filters.group)
    async def stop_cmd(_, message):
        chat_id = message.chat.id
        queues[chat_id].clear()
        current_tracks[chat_id] = None
        try:
            await calls.leave_call(chat_id)
        except Exception as exc:
            log.info("leave_call on stop: %s", exc)
        runtime_status["voice_chats"][str(chat_id)] = "stopped"
        await send_text(message, "⏹️ Audio dihentikan dan antrean dikosongkan.")

    @bot_client.on_message(filters.command("ping") & filters.group)
    async def ping_cmd(_, message):
        await send_text(message, f"🏓 Purumi aktif. Controller: {'online' if runtime_status['bot'] else 'offline'}; assistant: {'online' if runtime_status['assistant'] else 'offline'}.")

    try:
        await bot_client.start()
        runtime_status["bot"] = True
        await assistant.start()
        runtime_status["assistant"] = True
        calls = PyTgCalls(assistant)
        await calls.start()
        runtime_status["status"] = "running"
        runtime_status["last_error"] = None
        log.info("Purumi is ready. Controller and assistant connected.")

        # Register stream-ended callback before waiting for the process to stop.
        @calls.on_update()
        async def on_call_update(_, update):
            if type(update).__name__ == "StreamEnded":
                chat_id = getattr(update, "chat_id", None)
                if chat_id is not None:
                    current_tracks[chat_id] = None
                    await play_next(chat_id)

        await idle()
    except Exception as exc:
        runtime_status["status"] = "error"
        runtime_status["last_error"] = str(exc)
        log.exception("Bot runtime failed")
        raise
    finally:
        runtime_status["status"] = "stopping"
        if calls:
            try:
                await calls.stop()
            except Exception:
                log.exception("Error stopping PyTgCalls")
        for client in (assistant, bot_client):
            try:
                if client and client.is_connected:
                    await client.stop()
            except Exception:
                log.exception("Error stopping Telegram client")


def main():
    threading.Thread(target=start_http_server, name="http-health", daemon=True).start()
    asyncio.run(bot_main())


if __name__ == "__main__":
    main()
