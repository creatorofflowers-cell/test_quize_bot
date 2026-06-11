import json
import logging
import os
from telegram import Update, ReplyKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

with open("blocks.json", "r", encoding="utf-8") as f:
    raw_blocks = json.load(f)

blocks_by_id = {b["block_id"]: b for b in raw_blocks}

children_by_parent = {}
for b in raw_blocks:
    pid = b.get("parent_block_id")
    if pid is not None:
        children_by_parent.setdefault(pid, []).append(b)


def get_start_block():
    for b in raw_blocks:
        if b.get("condition") == "/start":
            return b
    return None


def find_next_block(current_block: dict, user_text: str):
    block_id = current_block["block_id"]

    if current_block.get("next_block_id"):
        return blocks_by_id.get(current_block["next_block_id"])

    children = children_by_parent.get(block_id, [])
    if children:
        else_block = None
        for child in children:
            cond = child.get("condition", "")
            if cond == "else":
                else_block = child
            elif cond.lower() == user_text.lower():
                return child
        return else_block

    ids = list(blocks_by_id.keys())
    try:
        idx = ids.index(block_id)
        for next_id in ids[idx + 1:]:
            candidate = blocks_by_id[next_id]
            if candidate.get("condition") == "auto_transition":
                return candidate
    except ValueError:
        pass

    return None


def build_keyboard(buttons: list):
    if not buttons:
        return ReplyKeyboardRemove()
    rows = [[btn] for btn in buttons]
    return ReplyKeyboardMarkup(rows, resize_keyboard=True, one_time_keyboard=True)


async def execute_commands(commands: list, context: ContextTypes.DEFAULT_TYPE):
    for cmd in commands or []:
        if cmd.startswith("inc_variable("):
            inner = cmd[len("inc_variable("):-1]
            parts = [p.strip() for p in inner.split(",")]
            if len(parts) == 2:
                var_name, amount = parts[0], int(parts[1])
                context.user_data[var_name] = context.user_data.get(var_name, 0) + amount
                logger.info(f"{var_name} = {context.user_data[var_name]}")


async def send_block(block: dict, update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = block.get("message", "...")
    buttons = block.get("buttons", [])

    for key, val in context.user_data.items():
        if not key.startswith("_"):
            text = text.replace(f"{{{key}}}", str(val))

    keyboard = build_keyboard(buttons)
    await update.effective_message.reply_text(text, reply_markup=keyboard)
    context.user_data["_current_block"] = block["block_id"]


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()

    block = get_start_block()
    if block:
        await send_block(block, update, context)
    else:
        await update.message.reply_text("Стартовый блок не найден.")


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_text = update.message.text.strip()
    current_block_id = context.user_data.get("_current_block")

    if current_block_id is None:
        await update.message.reply_text("Напиши /start чтобы начать квиз.")
        return

    current_block = blocks_by_id.get(current_block_id)
    if not current_block:
        await update.message.reply_text("Что-то пошло не так. Напиши /start.")
        return

    next_block = find_next_block(current_block, user_text)

    if next_block is None:
        score = context.user_data.get("score", 0)
        await update.message.reply_text(
            f"🏁 Квиз окончен! Твой результат: {score} очков.\n\nНапиши /start чтобы начать заново.",
            reply_markup=ReplyKeyboardRemove()
        )
        return

    await execute_commands(next_block.get("commands", []), context)
    await send_block(next_block, update, context)


def main():
    token = os.getenv("BOT_TOKEN")
    if not token:
        raise ValueError("Нет BOT_TOKEN! Задай переменную окружения BOT_TOKEN.")

    app = Application.builder().token(token).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    logger.info("Бот запущен...")
    app.run_polling()


if __name__ == "__main__":
    main()
