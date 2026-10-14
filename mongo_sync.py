"""Keeps the bot's data safe in MongoDB.

The bot works on a local SQLite file (fast). Every ~30 seconds, if anything changed,
a compressed snapshot of the whole database is saved into MongoDB. When the bot starts
on a fresh server (empty disk) it restores the latest snapshot automatically.
"""
import asyncio
import logging
import os
import sqlite3
import time
import zlib

from config import DB_PATH, MONGO_DB, MONGO_URL

CHUNK = 6 * 1024 * 1024
_state = {"mtime": 0, "last_ok": 0, "err": "", "busy": False}
_client = None


def enabled():
    return bool(MONGO_URL)


def _col():
    global _client
    if _client is None:
        from motor.motor_asyncio import AsyncIOMotorClient
        _client = AsyncIOMotorClient(MONGO_URL, serverSelectionTimeoutMS=8000)
    return _client[MONGO_DB]["db_snapshot"]


def _snapshot() -> bytes:
    tmp = DB_PATH + ".snap"
    src = sqlite3.connect(DB_PATH, timeout=20)
    try:
        dst = sqlite3.connect(tmp)
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()
    try:
        with open(tmp, "rb") as f:
            data = f.read()
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass
    return zlib.compress(data, 6)


async def push_now() -> str:
    if not enabled():
        return "Mongo backup is off (MONGO_URL is not set)."
    if not os.path.exists(DB_PATH):
        return "No database file yet."
    if _state["busy"]:
        return "A backup is already running."
    _state["busy"] = True
    try:
        mt = os.stat(DB_PATH).st_mtime_ns
        blob = await asyncio.to_thread(_snapshot)
        col = _col()
        ver = int(time.time() * 1000)
        n = 0
        for i in range(0, len(blob), CHUNK):
            await col.insert_one({"_id": f"{ver}:{n}", "ver": ver, "n": n, "data": blob[i:i + CHUNK]})
            n += 1
        await col.replace_one(
            {"_id": "meta"},
            {"_id": "meta", "ver": ver, "chunks": n, "size": len(blob), "ts": int(time.time())},
            upsert=True,
        )
        await col.delete_many({"_id": {"$ne": "meta"}, "ver": {"$ne": ver}})
        _state.update(mtime=mt, last_ok=int(time.time()), err="")
        return f"Backed up to MongoDB ({len(blob) // 1024} KB)."
    except Exception as e:
        _state["err"] = str(e)[:160]
        logging.exception("mongo push failed")
        return "Mongo backup failed: " + _state["err"]
    finally:
        _state["busy"] = False


async def restore_if_needed():
    """On a fresh/empty disk, load the latest snapshot from MongoDB before the bot starts."""
    if not enabled():
        return
    need = True
    if os.path.exists(DB_PATH) and os.path.getsize(DB_PATH) > 0:
        try:
            c = sqlite3.connect(DB_PATH)
            need = c.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0
            c.close()
        except Exception:
            need = True
    if not need:
        return
    try:
        col = _col()
        meta = await col.find_one({"_id": "meta"})
        if not meta:
            logging.info("Mongo: no snapshot yet, starting fresh")
            return
        parts = []
        for i in range(meta["chunks"]):
            d = await col.find_one({"_id": f"{meta['ver']}:{i}"})
            if not d:
                logging.error("Mongo: snapshot chunk %s missing, restore skipped", i)
                return
            parts.append(bytes(d["data"]))
        data = zlib.decompress(b"".join(parts))
        folder = os.path.dirname(os.path.abspath(DB_PATH))
        os.makedirs(folder, exist_ok=True)
        tmp = DB_PATH + ".restore"
        with open(tmp, "wb") as f:
            f.write(data)
        os.replace(tmp, DB_PATH)
        logging.info("Mongo: database restored (%s KB)", len(data) // 1024)
    except Exception:
        logging.exception("Mongo restore failed")


async def _loop():
    await asyncio.sleep(20)
    while True:
        try:
            if os.path.exists(DB_PATH) and os.stat(DB_PATH).st_mtime_ns != _state["mtime"]:
                await push_now()
        except Exception:
            logging.exception("mongo loop")
        await asyncio.sleep(30)


def start():
    if enabled():
        asyncio.create_task(_loop())
    else:
        logging.warning("MONGO_URL not set: data lives only on this server's disk")


def status_text():
    if not enabled():
        return "off (set MONGO_URL)"
    if _state["err"]:
        return "error: " + _state["err"]
    if not _state["last_ok"]:
        return "on, first backup pending"
    return "on, last backup " + time.strftime("%d %b %H:%M", time.localtime(_state["last_ok"]))
