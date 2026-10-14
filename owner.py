import asyncio
import html
import io
import json
import time

from aiogram import Bot, F, Router
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import BufferedInputFile, CallbackQuery, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

import db
import mongo_sync
import ui
import premium
from config import OWNER_ID, PLANS

router = Router()
esc = ui.esc


class Broadcast(StatesGroup):
    text = State()


async def staff(obj):
    u = await db.get_user(obj.from_user.id)
    return u if u and u["role"] in ("owner", "admin") else None


def kb_back(extra=None):
    kb = InlineKeyboardBuilder()
    for t, d in (extra or []):
        kb.button(text=t, callback_data=d)
    kb.button(text="⬅️ Owner Panel", callback_data="owner_panel")
    kb.button(text="🏠 Back to Menu", callback_data="menu")
    kb.adjust(1)
    return kb.as_markup()


@router.callback_query(F.data == "owner_panel")
async def panel(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    if not await staff(cb):
        return await cb.answer("Access denied.", show_alert=True)
    kb = InlineKeyboardBuilder()
    for t, d in (("📊 Stats", "os"), ("💳 Pending Payments", "pp"), ("🎁 All Contests", "all_contests"),
                 ("📜 Paid Votes Log", "blog"), ("💎 Plans", "opl"), ("📢 Broadcast", "bc"),
                 ("💾 Backup Users", "bku"), ("♻️ Restore Users", "rku"), ("☁️ Sync Now", "msync"),
                 ("ℹ️ Commands", "ohelp")):
        kb.button(text=t, callback_data=d)
    kb.button(text="🏠 Back to Menu", callback_data="menu")
    kb.adjust(2, 2, 2, 2, 2, 1)
    await ui.edit(cb, "🛠 <b>Owner Panel</b>", reply_markup=kb.as_markup())
    await cb.answer()


@router.callback_query(F.data == "os")
async def stats(cb: CallbackQuery):
    if not await staff(cb):
        return await cb.answer("Access denied.", show_alert=True)
    g = db.count_rows
    rev = (await db.fetchone("SELECT COALESCE(SUM(amount),0) AS s FROM payments WHERE status='approved'"))["s"]
    bonus = (await db.fetchone("SELECT COALESCE(SUM(amount),0) AS s FROM bonus_votes"))["s"]
    text = (
        "📊 <b>Stats</b>\n\n"
        f"Users: {await g('users')}\n"
        f"Premium users: {await g('users', 'WHERE plan!=\'free\'')}\n"
        f"Banned: {await g('users', 'WHERE banned=1')}\n"
        f"Channels: {await g('channels')}\n"
        f"Participants: {await g('participants')}\n"
        f"Contests: {await g('contests')} (active {await g('contests', 'WHERE status=\'active\'')})\n"
        f"Real votes: {await g('votes')}\n"
        f"Paid votes (net): {bonus}\n"
        f"Pending payments: {await g('payments', 'WHERE status=\'pending\'')}\n"
        f"Revenue (approved): ₹{rev}\n"
        f"☁️ Mongo backup: {esc(mongo_sync.status_text())}"
    )
    await ui.edit(cb, text, reply_markup=kb_back())
    await cb.answer()


@router.callback_query(F.data == "pp")
async def pending(cb: CallbackQuery, bot: Bot):
    if not await staff(cb):
        return await cb.answer("Access denied.", show_alert=True)
    rows = await db.fetchall("SELECT id FROM payments WHERE status='pending' ORDER BY id LIMIT 15")
    if not rows:
        await ui.edit(cb, "No pending payments.", reply_markup=kb_back())
        return await cb.answer()
    await cb.answer(f"Sending {len(rows)} pending payments")
    for r in rows:
        await premium.send_review(bot, cb.from_user.id, r["id"])


@router.callback_query(F.data == "all_contests")
async def all_contests(cb: CallbackQuery):
    if not await staff(cb):
        return await cb.answer("Access denied.", show_alert=True)
    rows = await db.fetchall("SELECT id,title,status FROM contests ORDER BY id DESC LIMIT 20")
    kb = InlineKeyboardBuilder()
    for r in rows:
        kb.button(text=f"{'🟢' if r['status'] == 'active' else '⚫'} #{r['id']} {ui.raw((r['title'] or '')[:24])}", callback_data=f"mc:{r['id']}")
    kb.button(text="⬅️ Owner Panel", callback_data="owner_panel")
    kb.adjust(1)
    await ui.edit(cb, "🎁 <b>All Contests</b>" if rows else "No contests yet.", reply_markup=kb.as_markup())
    await cb.answer()


@router.callback_query(F.data == "blog")
async def bonus_log(cb: CallbackQuery):
    if not await staff(cb):
        return await cb.answer("Access denied.", show_alert=True)
    rows = await db.fetchall(
        """SELECT b.*, p.first_name AS cname, t.title AS ctitle FROM bonus_votes b
           LEFT JOIN participants p ON p.contest_id=b.contest_id AND p.user_id=b.target_id
           LEFT JOIN contests t ON t.id=b.contest_id ORDER BY b.id DESC LIMIT 20""")
    lines = ["📜 <b>Paid Votes Log</b> (last 20)\n"]
    for r in rows:
        when = time.strftime("%d %b %H:%M", time.localtime(r["created_at"]))
        lines.append(f"{when} | {esc(str(r['ctitle'] or '#' + str(r['contest_id']))[:15])} → {esc(str(r['cname']))}: {r['amount']:+d} (by {r['given_by']})")
    if not rows:
        lines.append("No entries yet.")
    await ui.edit(cb, "\n".join(lines), reply_markup=kb_back())
    await cb.answer()


@router.callback_query(F.data == "ohelp")
async def ohelp(cb: CallbackQuery):
    if not await staff(cb):
        return await cb.answer("Access denied.", show_alert=True)
    await ui.edit(cb, 
        "ℹ️ <b>Commands</b>\n\n"
        "/ban USER_ID\n/unban USER_ID\n"
        "/addadmin USER_ID (owner only)\n/removeadmin USER_ID (owner only)\n"
        "/grant USER_ID PLAN DAYS — e.g. /grant 12345 pro 30 (owner only)\n\n"
        "Paid votes: All Contests → pick a contest → 👥 Participants → pick a participant",
        reply_markup=kb_back())
    await cb.answer()


# ---- broadcast
@router.callback_query(F.data == "bc")
async def bc_start(cb: CallbackQuery, state: FSMContext):
    if not await staff(cb):
        return await cb.answer("Access denied.", show_alert=True)
    await state.set_state(Broadcast.text)
    await ui.edit(cb, "Send the message you want to broadcast to all users:", reply_markup=kb_back())
    await cb.answer()


@router.message(StateFilter(Broadcast.text))
async def bc_send(msg: Message, state: FSMContext, bot: Bot):
    if not await staff(msg):
        return
    await state.clear()
    users = await db.fetchall("SELECT user_id FROM users WHERE banned=0")
    ok = 0
    for u in users:
        try:
            await msg.copy_to(u["user_id"])
            ok += 1
        except Exception:
            pass
        await asyncio.sleep(0.05)
    await msg.answer(f"📢 Sent to {ok}/{len(users)} users.", reply_markup=kb_back())


# ---- commands
def _arg_id(msg: Message):
    parts = (msg.text or "").split()
    if len(parts) < 2 or not parts[1].lstrip("-").isdigit():
        return None
    return int(parts[1])


@router.message(Command("ban"))
async def ban(msg: Message):
    if not await staff(msg):
        return
    uid = _arg_id(msg)
    if uid is None or uid == OWNER_ID:
        return await msg.answer("Usage: /ban USER_ID")
    await db.execute("UPDATE users SET banned=1 WHERE user_id=?", (uid,))
    await msg.answer(f"🚫 {uid} has been banned.")


@router.message(Command("unban"))
async def unban(msg: Message):
    if not await staff(msg):
        return
    uid = _arg_id(msg)
    if uid is None:
        return await msg.answer("Usage: /unban USER_ID")
    await db.execute("UPDATE users SET banned=0 WHERE user_id=?", (uid,))
    await msg.answer(f"✅ {uid} has been unbanned.")


@router.message(Command("addadmin"))
async def addadmin(msg: Message):
    if msg.from_user.id != OWNER_ID:
        return
    uid = _arg_id(msg)
    if uid is None or not await db.get_user(uid):
        return await msg.answer("Usage: /addadmin USER_ID (the user must have started the bot)")
    await db.execute("UPDATE users SET role='admin' WHERE user_id=?", (uid,))
    await msg.answer(f"✅ {uid} is now an admin.")


@router.message(Command("removeadmin"))
async def removeadmin(msg: Message):
    if msg.from_user.id != OWNER_ID:
        return
    uid = _arg_id(msg)
    if uid is None or uid == OWNER_ID:
        return await msg.answer("Usage: /removeadmin USER_ID")
    await db.execute("UPDATE users SET role='user' WHERE user_id=? AND role='admin'", (uid,))
    await msg.answer(f"✅ {uid} is no longer an admin.")


@router.message(Command("grant"))
async def grant(msg: Message):
    if msg.from_user.id != OWNER_ID:
        return
    p = (msg.text or "").split()
    if len(p) != 4 or not p[1].isdigit() or p[2] not in PLANS or p[2] == "free" or not p[3].isdigit():
        return await msg.answer("Usage: /grant USER_ID basic|pro|ultimate DAYS")
    uid = int(p[1])
    if not await db.get_user(uid):
        return await msg.answer("User not found (they haven't started the bot).")
    await db.activate_plan(uid, p[2], int(p[3]))
    await msg.answer(f"✅ {uid} now has the {p[2]} plan for {p[3]} days.")


# ====================================================================== Plans control (owner only)
class PlanEdit(StatesGroup):
    value = State()


class RestoreUsers(StatesGroup):
    file = State()


PLAN_ORDER = ("free", "basic", "pro", "ultimate")
FIELD_LABEL = {
    "price": "💰 Price (₹)", "days": "📅 Days", "giveaways": "🎁 Giveaways",
    "channels": "🔗 Channels", "bonus": "🗳 Paid votes quota",
}


def _lim(n):
    return "Unlimited" if n >= 9999 else str(n)


async def owner_only(obj):
    u = await db.get_user(obj.from_user.id)
    if u and u["role"] == "owner":
        return u
    if hasattr(obj, "data"):
        await obj.answer("Only the owner can do this.", show_alert=True)
    return None


def plan_text(key):
    p = PLANS[key]
    if key == "free":
        return (f"💎 <b>{p['name']} plan</b>\n\n🎁 Giveaways: {_lim(p['giveaways'])}\n"
                f"🔗 Channels: {_lim(p['channels'])}")
    return (f"💎 <b>{p['name']} plan</b>\n\n💰 Price: ₹{p['price']}\n📅 Days: {p['days']}\n"
            f"🎁 Giveaways: {_lim(p['giveaways'])}\n🔗 Channels: {_lim(p['channels'])}\n"
            f"🗳 Paid votes quota: {p['bonus']}")


@router.callback_query(F.data == "opl")
async def plans_list(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    if not await owner_only(cb):
        return
    kb = InlineKeyboardBuilder()
    lines = ["💎 <b>Plans Control</b>\n"]
    for k in PLAN_ORDER:
        p = PLANS[k]
        if k == "free":
            lines.append(f"<b>{p['name']}</b> — Giveaways {_lim(p['giveaways'])}, Channels {_lim(p['channels'])}\n")
        else:
            lines.append(f"<b>{p['name']}</b> — ₹{p['price']} for {p['days']} day(s)\n"
                         f"Giveaways {_lim(p['giveaways'])}, Channels {_lim(p['channels'])}, Paid votes {p['bonus']}\n")
        kb.button(text=f"✏️ {p['name']}", callback_data=f"opx:{k}")
    kb.button(text="⬅️ Owner Panel", callback_data="owner_panel")
    kb.adjust(2, 2, 1)
    await ui.edit(cb, "\n".join(lines), reply_markup=kb.as_markup())
    await cb.answer()


@router.callback_query(F.data.startswith("opx:"))
async def plan_view(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    if not await owner_only(cb):
        return
    key = cb.data.split(":")[1]
    if key not in PLANS:
        return await cb.answer("Plan not found.", show_alert=True)
    fields = ("giveaways", "channels") if key == "free" else ("price", "days", "giveaways", "channels", "bonus")
    kb = InlineKeyboardBuilder()
    for f in fields:
        kb.button(text=FIELD_LABEL[f], callback_data=f"ope:{key}:{f}")
    kb.button(text="♾ Unlimited giveaways", callback_data=f"opu:{key}:giveaways")
    kb.button(text="♾ Unlimited channels", callback_data=f"opu:{key}:channels")
    kb.button(text="⬅️ Plans", callback_data="opl")
    kb.adjust(2, 2, 1, 1, 1, 1)
    await ui.edit(cb, plan_text(key) + "\n\nTap a field to change it.", reply_markup=kb.as_markup())
    await cb.answer()


@router.callback_query(F.data.startswith("opu:"))
async def plan_unlimited(cb: CallbackQuery):
    if not await owner_only(cb):
        return
    _, key, field = cb.data.split(":")
    if key not in PLANS or field not in ("giveaways", "channels"):
        return await cb.answer("Invalid.", show_alert=True)
    await db.save_plan(key, field, 9999)
    await cb.answer("Set to unlimited ✅")
    kb = InlineKeyboardBuilder()
    kb.button(text="⬅️ Back", callback_data=f"opx:{key}")
    await ui.edit(cb, plan_text(key), reply_markup=kb.as_markup())


@router.callback_query(F.data.startswith("ope:"))
async def plan_edit(cb: CallbackQuery, state: FSMContext):
    if not await owner_only(cb):
        return
    _, key, field = cb.data.split(":")
    if key not in PLANS or field not in db.PLAN_FIELDS:
        return await cb.answer("Invalid.", show_alert=True)
    await state.set_state(PlanEdit.value)
    await state.update_data(key=key, field=field)
    hint = "\nSend 0 for unlimited." if field in ("giveaways", "channels") else ""
    kb = InlineKeyboardBuilder()
    kb.button(text="⬅️ Cancel", callback_data=f"opx:{key}")
    await ui.edit(
        cb, f"{FIELD_LABEL[field]} of <b>{PLANS[key]['name']}</b> is now: <b>{_lim(PLANS[key][field]) if field in ('giveaways', 'channels') else PLANS[key][field]}</b>\n\n"
            f"Send the new number.{hint}", reply_markup=kb.as_markup())
    await cb.answer()


@router.message(StateFilter(PlanEdit.value))
async def plan_set(msg: Message, state: FSMContext):
    if not await owner_only(msg):
        return
    d = await state.get_data()
    key, field = d["key"], d["field"]
    try:
        val = int((msg.text or "").strip())
    except ValueError:
        return await msg.answer("❌ Send a number only.")
    if field in ("giveaways", "channels"):
        val = 9999 if val <= 0 or val >= 9999 else val
    elif field == "price":
        if not 1 <= val <= 100000:
            return await msg.answer("❌ Price must be between 1 and 100000.")
    elif field == "days":
        if not 1 <= val <= 3650:
            return await msg.answer("❌ Days must be between 1 and 3650.")
    elif field == "bonus":
        if not 0 <= val <= 10_000_000:
            return await msg.answer("❌ Paid votes quota must be between 0 and 10000000.")
    await db.save_plan(key, field, val)
    await state.clear()
    kb = InlineKeyboardBuilder()
    kb.button(text="⬅️ Back", callback_data=f"opx:{key}")
    await msg.answer("✅ Saved.\n\n" + plan_text(key), reply_markup=kb.as_markup())


# ====================================================================== Backup / Restore users
def parse_backup(data):
    """-> (full_rows, new_only_rows, skipped). Accepts our backup file, a plain list of ids, or a list of dicts."""
    items = data.get("users") if isinstance(data, dict) else data
    if not isinstance(items, list):
        raise ValueError("no user list found")
    full, new_only, skipped = [], [], 0
    now = int(time.time())
    for it in items:
        if isinstance(it, (int, str)) and str(it).lstrip("-").isdigit():
            it = {"user_id": int(it)}
        if not isinstance(it, dict):
            skipped += 1
            continue
        try:
            uid = int(it.get("user_id") or it.get("id") or it.get("_id"))
        except (TypeError, ValueError):
            skipped += 1
            continue
        if uid <= 0:
            skipped += 1
            continue
        role = it.get("role", "user")
        role = "owner" if uid == OWNER_ID else (role if role in ("user", "admin") else "user")
        plan = it.get("plan", "free")
        plan = plan if plan in PLANS else "free"
        try:
            expiry = int(it.get("plan_expiry") or 0)
            quota = int(it.get("bonus_quota") or 0)
            created = int(it.get("created_at") or now)
        except (TypeError, ValueError):
            skipped += 1
            continue
        row = (uid, it.get("username"), it.get("full_name") or it.get("first_name") or "", role, plan,
               expiry, quota, 1 if it.get("banned") else 0, created)
        if "plan" in it or "role" in it:
            full.append(row)
        else:
            new_only.append(row)
    return full, new_only, skipped


@router.callback_query(F.data == "bku")
async def backup_users(cb: CallbackQuery, bot: Bot):
    if not await staff(cb):
        return await cb.answer("Access denied.", show_alert=True)
    rows = await db.fetchall(
        "SELECT user_id,username,full_name,role,plan,plan_expiry,bonus_quota,banned,created_at FROM users ORDER BY user_id")
    blob = json.dumps({"format": "voteprox-users", "version": 1, "exported_at": int(time.time()),
                       "count": len(rows), "users": rows}, ensure_ascii=False).encode("utf-8")
    name = time.strftime("users_backup_%Y%m%d_%H%M.json")
    await cb.answer("Preparing your backup…")
    await bot.send_document(cb.from_user.id, BufferedInputFile(blob, name),
                            caption=f"💾 Users backup — {len(rows)} users\n\nKeep this file safe. Use ♻️ Restore Users to load it into this or another bot.")


@router.callback_query(F.data == "rku")
async def restore_start(cb: CallbackQuery, state: FSMContext):
    if not await owner_only(cb):
        return
    await state.set_state(RestoreUsers.file)
    kb = InlineKeyboardBuilder()
    kb.button(text="⬅️ Cancel", callback_data="owner_panel")
    await ui.edit(
        cb, "♻️ <b>Restore Users</b>\n\nSend the backup file (.json) now.\n\n"
            "• Works with the file from 💾 Backup Users (this bot or another Vote Pro X bot)\n"
            "• Also accepts a simple list of user IDs, e.g. [123, 456]\n"
            "• Existing users with the same ID are updated; the owner role is never changed.",
        reply_markup=kb.as_markup())
    await cb.answer()


@router.message(StateFilter(RestoreUsers.file), F.document)
async def restore_file(msg: Message, state: FSMContext, bot: Bot):
    if not await owner_only(msg):
        return
    doc = msg.document
    if doc.file_size and doc.file_size > 15 * 1024 * 1024:
        return await msg.answer("❌ File is too large (max 15 MB).")
    buf = io.BytesIO()
    await bot.download(doc, destination=buf)
    try:
        full, new_only, skipped = parse_backup(json.loads(buf.getvalue().decode("utf-8-sig")))
    except Exception as e:
        return await msg.answer(f"❌ This is not a valid backup file: {esc(str(e)[:100])}")
    existing = {r["user_id"] for r in await db.fetchall("SELECT user_id FROM users")}
    added = sum(1 for r in full + new_only if r[0] not in existing)
    updated = sum(1 for r in full if r[0] in existing)
    if full:
        await db.executemany(
            "INSERT INTO users(user_id,username,full_name,role,plan,plan_expiry,bonus_quota,banned,created_at) "
            "VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(user_id) DO UPDATE SET username=excluded.username, "
            "full_name=excluded.full_name, role=excluded.role, plan=excluded.plan, plan_expiry=excluded.plan_expiry, "
            "bonus_quota=excluded.bonus_quota, banned=excluded.banned", full)
    if new_only:
        await db.executemany(
            "INSERT OR IGNORE INTO users(user_id,username,full_name,role,plan,plan_expiry,bonus_quota,banned,created_at) "
            "VALUES(?,?,?,?,?,?,?,?,?)", new_only)
    await state.clear()
    await msg.answer(f"✅ Restore finished\n\nAdded: {added}\nUpdated: {updated}\nSkipped: {skipped}", reply_markup=kb_back())


@router.message(StateFilter(RestoreUsers.file))
async def restore_need_file(msg: Message):
    await msg.answer("❌ Please send the backup as a file (document).")


@router.callback_query(F.data == "msync")
async def sync_now(cb: CallbackQuery):
    if not await staff(cb):
        return await cb.answer("Access denied.", show_alert=True)
    await cb.answer("Backing up…")
    result = await mongo_sync.push_now()
    await ui.edit(cb, "☁️ " + esc(result), reply_markup=kb_back())
