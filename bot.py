import json
import logging
import os
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Загрузка структуры квиза
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
        if else_block:
            return else_block

    return None


def build_inline_keyboard(buttons: list, show_back: bool) -> InlineKeyboardMarkup:
    """Создает инлайн-клавиатуру прямо под сообщением."""
    keyboard = []
    # Варианты ответов
    for btn in buttons:
        keyboard.append([InlineKeyboardButton(text=btn, callback_data=f"choice:{btn}")])
    
    # Кнопка НАЗАД (если мы не на самом первом экране)
    if show_back:
        keyboard.append([InlineKeyboardButton(text="⏪ Назад", callback_data="action_back")])
        
    return InlineKeyboardMarkup(keyboard)


async def execute_commands(commands: list, context: ContextTypes.DEFAULT_TYPE, user_id: int):
    for cmd in commands or []:
        if cmd.startswith("inc_variable("):
            inner = cmd[len("inc_variable("):-1]
            parts = [p.strip() for p in inner.split(",")]
            if len(parts) == 2:
                var_name, amount = parts[0], int(parts[1])
                user_data = context.application.user_data.setdefault(user_id, {})
                user_data[var_name] = user_data.get(var_name, 0) + amount


async def render_block_text(block: dict, user_id: int, context: ContextTypes.DEFAULT_TYPE) -> str:
    """Подставляет переменные типа {score} в текст."""
    text = block.get("message", "...")
    user_vars = context.application.user_data.get(user_id, {})
    for key, val in user_vars.items():
        text = text.replace(f"{{{key}}}", str(val))
    return text


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Старт бота по команде /start"""
    user_id = update.effective_user.id
    # Полный сброс истории и очков при новом старте
    context.application.user_data[user_id] = {
        "score": 0,
        "_history": [],
        "_current_block": None
    }

    block = get_start_block()
    if block:
        context.application.user_data[user_id]["_current_block"] = block["block_id"]
        text = await render_block_text(block, user_id, context)
        keyboard = build_inline_keyboard(block.get("buttons", []), show_back=False)
        await update.message.reply_text(text, reply_markup=keyboard)


async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка нажатий на инлайн-кнопки."""
    query = update.callback_query
    await query.answer()

    user_id = update.effective_user.id
    user_data = context.application.user_data.setdefault(user_id, {})
    data = query.data

    # НАЖАЛИ КНОПКУ НАЗАД
    if data == "action_back":
        history = user_data.get("_history", [])
        if history:
            prev_block_id = history.pop()
            prev_block = blocks_by_id.get(prev_block_id)
            if prev_block:
                user_data["_current_block"] = prev_block_id
                text = await render_block_text(prev_block, user_id, context)
                show_back = (prev_block_id != 1001) # На самом первом шаге кнопку назад не показываем
                keyboard = build_inline_keyboard(prev_block.get("buttons", []), show_back=show_back)
                await query.edit_message_text(text, reply_markup=keyboard)
            return
        else:
            return

    # НАЖАЛИ НА ВАРИАНТ ОТВЕТА
    if data.startswith("choice:"):
        choice_text = data[len("choice:"):]
        current_block_id = user_data.get("_current_block")
        current_block = blocks_by_id.get(current_block_id)

        if not current_block:
            await query.edit_message_text("Сессия утеряна. Нажмите /start.")
            return

        next_block = find_next_block(current_block, choice_text)
        if next_block is None:
            score = user_data.get("score", 0)
            await query.edit_message_text(f"🏁 Квиз окончен! Твой результат: {score} очков.\n\nНапиши /start для перезапуска.")
            return

        accumulated_text = ""
        
        # Движок сквозного прохода (чтобы бот не зависал на промежуточных блоках)
        while next_block:
            await execute_commands(next_block.get("commands", []), context, user_id)
            block_message = await render_block_text(next_block, user_id, context)
            
            if accumulated_text:
                accumulated_text += "\n\n" + block_message
            else:
                accumulated_text = block_message

            # Если у блока есть кнопки — это новый вопрос, останавливаемся и показываем его
            if "buttons" in next_block:
                if "_history" not in user_data:
                    user_data["_history"] = []
                
                # Запоминаем старый шаг для кнопки Назад
                if current_block_id and current_block_id != next_block["block_id"]:
                    if not user_data["_history"] or user_data["_history"][-1] != current_block_id:
                        user_data["_history"].append(current_block_id)

                user_data["_current_block"] = next_block["block_id"]
                
                show_back = (next_block["block_id"] != 1001)
                keyboard = build_inline_keyboard(next_block.get("buttons", []), show_back=show_back)
                
                await query.edit_message_text(accumulated_text, reply_markup=keyboard)
                break
            else:
                # Если кнопок нет (это просто сообщение "Верно/Неверно") — летим автоматом к следующему вопросу
                next_block = find_next_block(next_block, "")


def main():
    token = os.getenv("BOT_TOKEN")
    if not token:
        raise ValueError("Нет BOT_TOKEN! Задай переменную окружения BOT_TOKEN.")

    app = Application.builder().token(token).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(handle_callback))

    logger.info("Бот успешно запущен на инлайн-кнопках...")
    app.run_polling()


if __name__ == "__main__":
    main()
