import asyncio
import json
import logging
from datetime import datetime
from pathlib import Path

from maxapi import Bot, Dispatcher, F
from maxapi.context import MemoryContext
from maxapi.context.state_machine import State, StatesGroup
from maxapi.types import (
    BotStarted,
    CallbackButton,
    Command,
    CommandStart,
    MessageCallback,
    MessageCreated,
    OpenAppButton,
)
from maxapi.utils.inline_keyboard import InlineKeyboardBuilder

from config import ADMIN_ID, MAX_BOT_TOKEN
from transport import patch_transport

patch_transport()

logging.basicConfig(level=logging.INFO)

bot = Bot(MAX_BOT_TOKEN)
dp = Dispatcher()

DATA_FILE = Path(__file__).resolve().parent / "data" / "requests.json"

TEXT_MENU = "🏫 Образовательный квартал 1409\n\nВыберите действие:"
TEXT_REQUEST = (
    "📝 Заявка\n\n"
    "Вы можете оставить заявку. Быстрое реагирование проблемы.\n"
    "Например: сломанный стул, разбитое окно, отсутствие воды\n\n"
    "📎 Обязательно прикрепите фотографию и оставьте комментарий."
)
TEXT_PROPOSAL = (
    "💡 Предложение\n\n"
    "Вы можете сформировать предложение по улучшению среды "
    "образовательного квартала 1409.\n\n"
    "📎 Обязательно прикрепите фотографию и оставьте комментарий."
)


class Flow(StatesGroup):
    wait_request = State()
    wait_proposal = State()


def load_data() -> dict:
    if DATA_FILE.exists():
        try:
            return json.loads(DATA_FILE.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass
    return {"requests": [], "next_id": 1}


def save_data(data: dict) -> None:
    DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
    DATA_FILE.write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def menu_markup(bot=None):
    builder = InlineKeyboardBuilder()
    builder.row(CallbackButton(text="📝 Заявка", payload="menu_request"))
    builder.row(CallbackButton(text="💡 Предложение", payload="menu_proposal"))
    builder.row(CallbackButton(text="📋 Мои заявки", payload="menu_my"))
    builder.row(CallbackButton(text="🗄 Архив", payload="menu_archive"))
    if bot is not None and bot.me is not None:
        builder.row(
            OpenAppButton(
                text="🖥 Панель обращений",
                web_app=bot.me.username,
                contact_id=bot.me.user_id,
            )
        )
    return builder.as_markup()


def flow_markup():
    builder = InlineKeyboardBuilder()
    builder.row(CallbackButton(text="🏠 Главное меню", payload="menu_main"))
    return builder.as_markup()


async def send_menu(bot, chat_id=None, user_id=None, text=TEXT_MENU):
    await bot.send_message(
        chat_id=chat_id,
        user_id=user_id,
        text=text,
        attachments=[menu_markup(bot)],
    )


@dp.bot_started()
async def on_bot_started(event: BotStarted):
    await send_menu(event.bot, chat_id=event.chat_id)


@dp.message_created(CommandStart())
async def on_command_start(event: MessageCreated, context: MemoryContext):
    await context.set_state(None)
    await event.message.answer(TEXT_MENU, attachments=[menu_markup(event.bot)])


@dp.message_created(Command("id"))
async def on_show_id(event: MessageCreated):
    await event.message.answer(f"Ваш ID: {event.message.sender.user_id}")


@dp.message_callback(F.callback.payload == "menu_request")
async def on_menu_request(event: MessageCallback, context: MemoryContext):
    await event.message.answer(TEXT_REQUEST, attachments=[flow_markup()])
    await context.set_state(Flow.wait_request)


@dp.message_callback(F.callback.payload == "menu_proposal")
async def on_menu_proposal(event: MessageCallback, context: MemoryContext):
    await event.message.answer(TEXT_PROPOSAL, attachments=[flow_markup()])
    await context.set_state(Flow.wait_proposal)


@dp.message_callback(F.callback.payload == "menu_my")
async def on_menu_my(event: MessageCallback):
    await show_user_requests(event, status="active", title="📋 Мои заявки")


@dp.message_callback(F.callback.payload == "menu_archive")
async def on_menu_archive(event: MessageCallback):
    await show_user_requests(event, status="archived", title="🗄 Архив")


@dp.message_callback(F.callback.payload == "menu_main")
async def on_menu_main(event: MessageCallback):
    await event.message.answer(TEXT_MENU, attachments=[menu_markup(event.bot)])


async def show_user_requests(event: MessageCallback, status: str, title: str):
    records = [
        r
        for r in load_data()["requests"]
        if r["user_id"] == event.message.sender.user_id and r["status"] == status
    ]
    if not records:
        await event.message.answer(f"{title}\n\nЗдесь пока пусто.", attachments=[menu_markup(event.bot)])
        return

    lines = [title, ""]
    for r in records:
        icon = "📝" if r["kind"] == "заявка" else "💡"
        lines.append(f"{icon} #{r['id']}: {r['kind'].capitalize()}")
        lines.append(f"Комментарий: {r['comment']}")
        lines.append(f"Дата: {r['created_at']}")
        lines.append("")
    lines.append("")
    await event.message.answer("\n".join(lines).strip(), attachments=[menu_markup(event.bot)])


@dp.message_created(Flow.wait_request)
async def on_wait_request(event: MessageCreated, context: MemoryContext):
    await accept_flow_message(event, context, "заявка")


@dp.message_created(Flow.wait_proposal)
async def on_wait_proposal(event: MessageCreated, context: MemoryContext):
    await accept_flow_message(event, context, "предложение")


async def accept_flow_message(event: MessageCreated, context: MemoryContext, kind: str):
    photo = None
    for attachment in event.message.body.attachments or []:
        if getattr(attachment, "type", None) == "image":
            photo = attachment
            break

    comment = (event.message.body.text or "").strip()

    if photo is None:
        await event.message.answer("📎 Обязательно прикрепите фотографию к сообщению.")
        return

    if not comment:
        await event.message.answer("💬 Напишите комментарий вместе с фотографией.")
        return

    payload = getattr(photo, "payload", None)
    data = load_data()
    request_id = data["next_id"]
    data["requests"].append(
        {
            "id": request_id,
            "user_id": event.message.sender.user_id,
            "user": event.message.sender.full_name,
            "kind": kind,
            "comment": comment,
            "photo_url": getattr(payload, "url", None),
            "photo_token": getattr(payload, "token", None),
            "created_at": datetime.now().strftime("%d.%m.%Y %H:%M"),
            "status": "active",
        }
    )
    data["next_id"] += 1
    save_data(data)

    await context.set_state(None)

    word = "заявка" if kind == "заявка" else "предложение"
    await event.message.answer(
        f"✅ #{request_id}: {word.capitalize()} принята!\n\n"
        f"Мы быстро отреагируем на вашу {word}."
    )
    await send_menu(
        event.bot,
        chat_id=event.message.recipient.chat_id,
        user_id=event.message.recipient.user_id,
    )


@dp.message_created(Command("all"))
async def on_admin_all(event: MessageCreated):
    if event.message.sender.user_id != ADMIN_ID:
        return
    records = [r for r in load_data()["requests"] if r["status"] == "active"]
    if not records:
        await event.message.answer("Активных обращений нет.")
        return

    lines = ["Все активные обращения:", ""]
    for r in records:
        lines.append(f"#{r['id']} · {r['kind']} · {r['user']}")
        lines.append(f"Комментарий: {r['comment']}")
        lines.append(f"Дата: {r['created_at']}")
        lines.append("")
    await event.message.answer("\n".join(lines).strip())


@dp.message_created(Command("archive"))
async def on_admin_archive(event: MessageCreated):
    await admin_set_status(event, "archived", "архивирована")


@dp.message_created(Command("restore"))
async def on_admin_restore(event: MessageCreated):
    await admin_set_status(event, "active", "возвращена в активные")


async def admin_set_status(event: MessageCreated, status: str, done_word: str):
    if event.message.sender.user_id != ADMIN_ID:
        return

    parts = (event.message.body.text or "").split()
    if len(parts) != 2:
        await event.message.answer("Использование: /archive <номер> или /restore <номер>")
        return

    try:
        request_id = int(parts[1])
    except ValueError:
        await event.message.answer("Нужно указать номер заявки числом.")
        return

    data = load_data()
    for record in data["requests"]:
        if record["id"] == request_id:
            record["status"] = status
            save_data(data)
            await event.message.answer(f"Заявка №{request_id} {done_word}.")
            return

    await event.message.answer(f"Заявка №{request_id} не найдена.")


@dp.message_created(F.message.body.attachments)
async def on_any_attachment(event: MessageCreated):
    await event.message.answer(TEXT_MENU, attachments=[menu_markup(event.bot)])


@dp.message_created(F.message.body.text)
async def on_any_text(event: MessageCreated):
    await event.message.answer(TEXT_MENU, attachments=[menu_markup(event.bot)])


async def main():
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())