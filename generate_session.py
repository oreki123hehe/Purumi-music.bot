"""Generate the assistant account session string locally. Never run this on a public host."""
import asyncio
import os
from kurigram import Client


async def main():
    api_id = os.getenv("API_ID", "").strip()
    api_hash = os.getenv("API_HASH", "").strip()
    if not api_id or not api_hash:
        print("Set API_ID and API_HASH first.")
        raise SystemExit(2)

    client = Client(
        "purumi_session_setup",
        api_id=int(api_id),
        api_hash=api_hash,
        in_memory=True,
    )
    await client.start()
    try:
        session = await client.export_session_string()
        print("\nSESSION_STRING (keep private; do not commit it):\n")
        print(session)
        print("\nCopy it directly into Faable's SESSION_STRING secret.")
    finally:
        await client.stop()


if __name__ == "__main__":
    asyncio.run(main())
