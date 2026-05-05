import logging
import json
import random
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes, CallbackQueryHandler, MessageHandler, filters
from telegram.constants import PollType, ChatType
import ai_core
import os
import asyncio

USER_FILE = "users.json"
user_lock = asyncio.Lock()

# Configuration
TOKEN = "tt"

# Setup logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

async def add_user(user_id: int):
    async with user_lock:
        try:
            # Create file if not exists
            if not os.path.exists(USER_FILE):
                with open(USER_FILE, "w") as f:
                    json.dump([], f)

            # Read existing users
            with open(USER_FILE, "r") as f:
                try:
                    users = json.load(f)
                except json.JSONDecodeError:
                    users = []

            # Add if not exists
            if user_id not in users:
                users.append(user_id)

                # Write back safely
                with open(USER_FILE, "w") as f:
                    json.dump(users, f, indent=2)

                logger.info(f"[USER] Added new user: {user_id}")

        except Exception as e:
            logger.error(f"[USER] Error adding user {user_id}: {e}")
            

def load_questions():
    """Loads questions from the JSON file."""
    try:
        with open("questions.json", "r") as f:
            return json.load(f)
    except Exception as e:
        logging.error(f"Error loading questions: {e}")
        return []

def find_best_question(topic, questions):
    """Simple probabilistic search using keyword intersection."""
    topic_words = set(topic.lower().split())
    best_match = None
    highest_score = 0

    for q in questions:
        # Check keywords in question and explanation
        content = (q['question'] + " " + q.get('explanation', '')).lower()
        score = sum(1 for word in topic_words if word in content)
        
        if score > highest_score:
            highest_score = score
            best_match = q
            
    return best_match if highest_score > 0 else random.choice(questions)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles /start with interactive buttons."""
    user_id = update.effective_user.id

    # ADD THIS LINE
    await add_user(user_id)
    keyboard = [
        [InlineKeyboardButton("✨ Random Quiz", callback_data="quiz_random"),
         InlineKeyboardButton("📚 About", callback_data="about_bot")],
        [InlineKeyboardButton("🤖 Enter AI Tutor", callback_data="ai:enter")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    text = (
        "💎 <b>Crypto Quiz Ultra</b>\n\n"
        "Welcome to the ultimate blockchain learning hub! 🚀\n\n"
        "🧠 <b>Test Your Knowledge:</b> Take random quizzes or use <code>/quiz [topic]</code> to challenge yourself.\n"
        "🤖 <b>AI Tutor:</b> Enter AI Mode to chat with our intelligent crypto assistant for real-time market data and personalized lessons.\n\n"
        "<i>What would you like to explore today?</i>"
    )
    
    await update.message.reply_text(text, reply_markup=reply_markup, parse_mode='HTML')

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles button clicks."""
    query = update.callback_query
    await query.answer()

    if query.data == "quiz_random":
        await query.edit_message_text("🔄 Finding a question for you...", parse_mode='Markdown')
        # We call the poll sender directly
        await send_quiz_logic(update, context)
    
    elif query.data == "about_bot":
        keyboard = [[InlineKeyboardButton("⬅️ Back", callback_data="back_start")]]
        about_text = (
            "💎 <b>About CryptoQuiz Pro</b>\n\n"
            "This bot is a comprehensive professional tool designed to help you master cryptocurrency concepts and track real-time market data.\n\n"
            "🔹 <b>Interactive Polls:</b> Deploy challenging quizzes in your groups to test your friends.\n"
            "🔹 <b>Live AI Tutor:</b> Chat with a specialized AI to learn complex topics step-by-step or check live token prices.\n\n"
            "<i>Elevate your crypto journey with us!</i> 🚀"
        )
        await query.edit_message_text(
            about_text,
            reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML"
        )
        
    elif query.data == "back_start":
        keyboard = [
            [InlineKeyboardButton("🚀 Random Quiz", callback_data="quiz_random"),
             InlineKeyboardButton("📚 About", callback_data="about_bot")],
            [InlineKeyboardButton("🤖 Enter AI Tutor", callback_data="ai:enter")]
        ]
        text = (
            "💎 <b>CryptoQuiz Pro</b>\n\n"
            "Welcome to the ultimate blockchain learning hub! 🚀\n\n"
            "🧠 <b>Test Your Knowledge:</b> Take random quizzes or use <code>/quiz [topic]</code> to challenge yourself.\n"
            "🤖 <b>AI Tutor:</b> Enter AI Mode to chat with our intelligent crypto assistant for real-time market data and personalized lessons.\n\n"
            "<i>What would you like to explore today?</i>"
        )
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='HTML')
        
    elif query.data == "ai:enter":
        ai_core.toggle_ai_mode(update.effective_user.id, True)
        text = (
            "🤖 <b>AI Tutor Activated!</b>\n\n"
            "I'm your personal Crypto Tutor. You can ask me anything about blockchain, tokens, or live market prices.\n\n"
            "<i>Type <code>/exit</code> at any time to return to the main menu.</i>"
        )
        await query.edit_message_text(text, parse_mode='HTML')

async def exit_ai_mode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if ai_core.is_ai_mode(user_id):
        ai_core.toggle_ai_mode(user_id, False)
        await update.message.reply_text("👋 You have left the AI session. Type /start to open the main menu.")
    else:
        await update.message.reply_text("You are not currently in an AI session. Type /start to open the main menu.")

async def handle_ai_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not ai_core.is_ai_mode(user_id):
        return
        
    text = update.message.text
    chat_id = update.effective_chat.id
    
    # 1. Native Stream UI Draft
    draft_id = random.randint(10000, 99999)
    
    async def set_draft_status(status_text):
        try:
            await context.bot.do_api_request("sendMessageDraft", {
                "chat_id": chat_id,
                "draft_id": draft_id,
                "text": f"🧠 {status_text}"
            })
        except Exception as e:
            logger.warning(f"Draft request failed: {e}")

    await set_draft_status("Thinking...")
        
    # Process AI
    result = await ai_core.process_ai_message(user_id, text, update_status=set_draft_status)
    reply_text = result.get("reply", "No response generated.")
    
    # 2. Promotion
    try:
        await context.bot.send_message(
            chat_id=chat_id,
            text=reply_text,
            api_kwargs={"draft_id": draft_id},
            parse_mode="HTML"
        )
    except Exception as e:
        logger.error(f"Error promoting draft: {e}")
        await context.bot.send_message(chat_id=chat_id, text=reply_text, parse_mode="HTML")

async def quiz_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles /quiz [topic] command."""
    questions = load_questions()
    if not questions:
        await update.message.reply_text("⚠️ Database is empty.")
        return

    if context.args:
        topic = " ".join(context.args)
        quiz_data = find_best_question(topic, questions)
    else:
        quiz_data = random.choice(questions)

    await send_poll_to_chat(update, context, quiz_data)

async def send_quiz_logic(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Helper for button-triggered quizzes."""
    questions = load_questions()
    if questions:
        quiz_data = random.choice(questions)
        await send_poll_to_chat(update, context, quiz_data)

async def send_poll_to_chat(update: Update, context: ContextTypes.DEFAULT_TYPE, quiz_data):
    """The central logic to send the actual poll."""
    chat = update.effective_chat
    
    # Determine anonymity: Group polls must be public (not anonymous)
    # is_anonymous = False means users can see who voted what
    is_group = chat.type in [ChatType.GROUP, ChatType.SUPERGROUP]
    
    await context.bot.send_poll(
        chat_id=chat.id,
        question=quiz_data["question"],
        options=quiz_data["options"],
        type=PollType.QUIZ,
        correct_option_id=quiz_data["correct_option_id"],
        explanation=quiz_data.get("explanation", "Study more!"),
        is_anonymous=not is_group if is_group else False # Public in groups
    )

async def post_init(application):
    # This runs right after the bot starts its asyncio event loop
    ai_core.start_worker()

if __name__ == '__main__':
    application = ApplicationBuilder().token(TOKEN).post_init(post_init).build()
    
    # Handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("quiz", quiz_command))
    application.add_handler(CommandHandler("poll", quiz_command)) # Alias
    application.add_handler(CommandHandler("exit", exit_ai_mode))
    application.add_handler(CallbackQueryHandler(button_handler))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_ai_message))

    print("Bot is live with Topic Search and Buttons...")
    application.run_polling()
