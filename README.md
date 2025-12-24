# telegram_chat_bot

This README.md is tailored to your specific implementation, highlighting the unique Multi-Device Phone-Centric Sync and the AI-Grading workflow.

🎓 AI Teacher's Grading Bot
An intelligent Telegram bot designed for teachers to automate the grading process using Gemini 1.5 Pro and a custom Grading API. This bot allows teachers to upload exam questions, model answers, and student papers to receive instant, annotated feedback.

🌟 Key Features
Cross-Device Synchronization: The session is tied to the Phone Number, not the Telegram User ID. Switch between your phone, tablet, and desktop seamlessly.

Intelligent Extraction: Uses Gemini 2.5 Pro to extract structured question text and options from handwritten or printed images.

Automated Grading: Integrates with a specialized Grading API to provide annotated result images and score calculation.

Concurrency Control: A global "Phone Lock" prevents duplicate AI requests even if multiple devices send images simultaneously.

Feedback Loop: Teachers can rate AI grading accuracy, with all data (including base64 images) saved to a local JSON archive.

🛠️ Technical Stack
Language: Python 3.10+

Library: Telethon (Telegram MTProto)

AI Orchestration: LiteLLM (Gemini 1.5 Pro)

Async Processing: asyncio for non-blocking API polling and concurrency management.

🚀 Setup Instructions
1. Prerequisites
A Telegram API ID and API HASH (get them from my.telegram.org).

A Bot Token from @BotFather.

A Google Gemini API Key.

2. Environment Variables
Create a .env file in the root directory:

Code snippet

BOT_TOKEN=your_bot_token
API_ID=your_api_id
API_HASH=your_api_hash
MODEL_ID=gemini/gemini-2.5-pro
MODEL_API_KEY=your_gemini_key
GRADING_API_URL=https://your-api.com/grade
GRADING_RESULT_URL=https://your-api.com/result/
GRADING_API_KEY=your_grading_api_key
JSON_FILE=grading_results.json
STORAGE_PATH=./temp_exams
3. Installation
Bash

pip install telethon litellm requests python-dotenv
python main.py
📖 User Workflow
Verification: Send /start and share your contact. This binds your device to your phone number.

Step 1 (Questions): Upload an image of the exam questions. The AI will extract the text.

Step 2 (Answers): Upload the model answer (the key). The AI maps these to the questions.

Step 3 (Grading): Upload student papers. The bot will poll the API and return a graded, annotated image.

Rating: Use the inline buttons (Excellent/Good/Bad) to save the final result to the database.

📊 Data Logs & Storage
The bot maintains a grading_results.json file. Each entry includes:

Teacher Phone: The verified primary ID.

Task ID: Reference for the Grading API.

Images: Original student paper and AI-annotated result (Base64).

Metrics: Grade (out of 10), AI reasoning, and teacher feedback rating.

🛡️ Safety & Concurrency
Phone Locking: If a teacher uploads multiple photos rapidly, the bot processes the first and logs: [BLOCK] Dropping duplicate request.

Error Recovery: The try...finally block ensures that if an API fails, the user's phone is unlocked so they can try again without restarting the bot.
