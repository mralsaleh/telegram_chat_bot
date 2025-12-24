import os
import asyncio
import json
import base64
import time
import requests
from datetime import datetime
from telethon import TelegramClient, events, Button
from telethon.errors import MessageIdInvalidError
import litellm
from dotenv import load_dotenv

# Load configuration
load_dotenv()


class Config:
    BOT_TOKEN = os.getenv("BOT_TOKEN")
    API_ID = int(os.getenv("API_ID", 0))
    API_HASH = os.getenv("API_HASH")
    MODEL_ID = os.getenv("MODEL_ID", "gemini/gemini-1.5-pro")
    MODEL_API_KEY = os.getenv("MODEL_API_KEY")
    GRADING_API_URL = os.getenv("GRADING_API_URL")
    GRADING_RESULT_URL = os.getenv("GRADING_RESULT_URL")
    GRADING_API_KEY = os.getenv("GRADING_API_KEY")
    STORAGE_PATH = os.getenv("STORAGE_PATH", "./temp_exams")
    JSON_FILE = os.getenv("JSON_FILE", "grading_results.json")


os.makedirs(Config.STORAGE_PATH, exist_ok=True)
if not os.path.exists(Config.JSON_FILE):
    with open(Config.JSON_FILE, 'w', encoding='utf-8') as f:
        json.dump([], f)

json_lock = asyncio.Lock()


# ==========================================
# SESSION MANAGER
# ==========================================

class SessionManager:
    def __init__(self):
        self.users = {}
        self.results_meta = {}
        self.active_tasks = {}
        self.ignored_messages = set()

    def get_user(self, uid):
        if uid not in self.users:
            self.users[uid] = {"state": "START", "data": {}, "phone": "Unknown"}
            print(f"DEBUG: New session for User: {uid}")
        return self.users[uid]

    def reset_user(self, uid):
        if uid in self.users:
            phone = self.users[uid].get("phone", "Unknown")
            self.users[uid] = {"state": "STEP_1_QUESTIONS", "data": {}, "phone": phone}
            print(f"DEBUG: User {uid} reset to STEP_1.")

    def save_result_meta(self, task_id, meta):
        self.results_meta[task_id] = meta


session = SessionManager()


# ==========================================
# UTILITIES
# ==========================================

def repair_json(content: str) -> str:
    return content.replace('```json', '').replace('```', '').strip()


async def save_to_json(entry: dict):
    print(f"DEBUG: Finalizing JSON save for Task ID: {entry.get('task_id')}")
    async with json_lock:
        try:
            with open(Config.JSON_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
            data.append(entry)
            with open(Config.JSON_FILE, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=4, ensure_ascii=False)
        except Exception as e:
            print(f"ERROR [JSON SAVE]: {e}")


async def llm_process(image_data, mime_type, system_message):
    print(f"DEBUG: AI Request - Prompt length: {len(system_message)}")
    try:
        os.environ['GEMINI_API_KEY'] = Config.MODEL_API_KEY
        loop = asyncio.get_event_loop()
        response = await loop.run_in_executor(None, lambda: litellm.completion(
            model=Config.MODEL_ID, messages=[
                {"role": "system", "content": system_message},
                {"role": "user", "content": [
                    {"type": "image_url", "image_url": {"url": f"data:{mime_type};base64,{image_data}"}},
                    {"type": "text", "text": "Return ONLY a valid JSON object."}
                ]}
            ], temperature=0, response_format={"type": "json_object"}
        ))
        return json.loads(repair_json(response.choices[0].message.content))
    except Exception as e:
        print(f"ERROR [AI]: {e}")
        return None


async def poll_grading_task(chat_id, status_msg, task_id, specific_student_b64, teacher_phone, student_temp_path, uid):
    headers = {"x-api-key": Config.GRADING_API_KEY}
    session.active_tasks[uid] = task_id
    print(f"DEBUG: Polling started for Task ID: {task_id}")

    for i in range(150):
        # Stop polling if the message itself was ignored
        if status_msg.id in session.ignored_messages:
            print(f"LOG: Polling stopped for Task {task_id} (Reason: Message ID {status_msg.id} ignored)")
            return

        try:
            loop = asyncio.get_event_loop()
            response = await loop.run_in_executor(None, lambda: requests.get(
                f"{Config.GRADING_RESULT_URL}{task_id}", headers=headers, timeout=15
            ))
            data = response.json()
            status = data.get("status")

            if status == "completed":
                print(f"LOG: Task {task_id} marked COMPLETED by API. Sending response...")
                annotated_b64 = data.get("annotated_image")
                grading = data.get("grading", {})
                grade = grading.get('overall_grade', 'N/A')
                reasoning = grading.get('overall_reasoning', 'Done')

                if annotated_b64:
                    res_path = f"{Config.STORAGE_PATH}/res_{task_id}.jpg"
                    with open(res_path, "wb") as f:
                        f.write(base64.b64decode(annotated_b64))

                    session.save_result_meta(task_id, {
                        "student_b64": specific_student_b64,
                        "ai_graded_b64": annotated_b64,
                        "grade": grade,
                        "feedback": reasoning,
                        "teacher_phone": teacher_phone
                    })

                    buttons = [[Button.inline("Excellent 🌟", f"rate|{task_id}|excellent"),
                                Button.inline("Good 👍", f"rate|{task_id}|good"),
                                Button.inline("Bad 👎", f"rate|{task_id}|bad")]]

                    await bot.send_file(chat_id, res_path, buttons=buttons,
                                        caption=f"✅ **Graded!**\n📊 **Grade:** {grade}/10\n📝 **Feedback:** {reasoning}")
                    try:
                        await status_msg.delete()
                    except:
                        pass

                if uid in session.active_tasks:
                    session.active_tasks.pop(uid)
                return

            elif status == "failed":
                print(f"LOG: Task {task_id} FAILED in API.")
                await status_msg.edit("❌ **Grading Failed.** Server could not process image.")
                return

        except Exception as e:
            print(f"DEBUG: Polling error Task {task_id}: {e}")
        await asyncio.sleep(5)


# ==========================================
# BOT HANDLERS
# ==========================================

bot = TelegramClient('teacher_bot_session', Config.API_ID, Config.API_HASH)


@bot.on(events.NewMessage(pattern='/start'))
async def start_handler(event):
    uid = event.sender_id
    user = session.get_user(uid)
    sender = await event.get_sender()
    if hasattr(sender, 'phone') and sender.phone:
        user["phone"] = f"+{sender.phone}"
        user["state"] = "STEP_1_QUESTIONS"
        await event.respond(f"✅ **Verified:** `{user['phone']}`\n\n**Step 1:** Send **Exam Questions** image.",
                            buttons=Button.clear())
    else:
        user["state"] = "WAIT_CONTACT"
        await event.respond("👋 **Welcome!** Grant phone access to start.",
                            buttons=[[Button.request_phone("🔓 Grant Access", resize=True, single_use=True)]])


@bot.on(events.NewMessage(func=lambda e: e.contact))
async def contact_handler(event):
    uid = event.sender_id
    user = session.get_user(uid)
    if event.contact:
        user["phone"] = event.contact.phone_number
        user["state"] = "STEP_1_QUESTIONS"
        print(f"LOG: User {uid} phone verified via Contact {user["phone"]}.")
        await event.respond(f"✅ **Verified!**\n\n**Step 1:** Please send the **Exam Questions** image.",
                            buttons=Button.clear())


@bot.on(events.CallbackQuery(pattern=rb"ignore_img"))
async def ignore_handler(event):
    uid = event.sender_id
    user = session.get_user(uid)
    msg_id = event.message_id
    session.ignored_messages.add(msg_id)

    # Logging Task ID if it exists in Step 3
    current_task = session.active_tasks.get(uid, "N/A")
    print(f"LOG: IGNORING Action - User: {uid} | State: {user['state']} | TaskID: {current_task} | MsgID: {msg_id}")

    if user["state"] == "STEP_1_QUESTIONS" or (
            user["state"] == "STEP_2_ANSWERS" and "questions_list" not in user["data"]):
        user["state"] = "STEP_1_QUESTIONS"
        user["data"].pop("questions_list", None)
        await event.respond("🗑️ **Questions Ignored.** Send new Questions image.")
    elif user["state"] == "STEP_2_ANSWERS" or (
            user["state"] == "STEP_3_STUDENT" and "model_answer" not in user["data"]):
        user["state"] = "STEP_2_ANSWERS"
        user["data"].pop("model_answer", None)
        await event.respond("🗑️ **Model Answer Ignored.** Send new Answer image.")
    elif user["state"] == "STEP_3_STUDENT":
        session.active_tasks.pop(uid, None)
        await event.respond("🛑 **Processing Cancelled.** Ready for next paper.")

    try:
        await event.delete()
    except:
        pass


@bot.on(events.NewMessage())
async def message_handler(event):
    if event.contact or (event.text and event.text.startswith('/')):
        return
    if not event.photo:
        return

    uid = event.sender_id
    user = session.get_user(uid)
    state = user["state"]
    ignore_btn = [Button.inline("❌ Ignore this image", b"ignore_img")]

    if state == "STEP_1_QUESTIONS":
        status = await event.respond("📥 Reading Questions... ⏳", buttons=ignore_btn)
        path = await event.download_media(file=f"{Config.STORAGE_PATH}/{uid}_q.jpg")
        with open(path, "rb") as f:
            b64 = base64.b64encode(f.read()).decode()

        sys_msg = "Extract every question text exactly. Include MC options. Format: {'q1': 'text', 'q2': 'text'}"
        res = await llm_process(b64, "image/jpeg", sys_msg)

        if status.id not in session.ignored_messages:
            if res:
                user["data"]["questions_list"] = res
                user["state"] = "STEP_2_ANSWERS"
                await status.edit("✅ **Questions saved.**\n\n**Step 2:** Send **Model Answer Image**.", buttons=None)
        else:
            session.ignored_messages.discard(status.id)

    elif state == "STEP_2_ANSWERS":
        status = await event.respond("🧠 Mapping answers... ⏳", buttons=ignore_btn)
        path = await event.download_media(file=f"{Config.STORAGE_PATH}/{uid}_a.jpg")
        with open(path, "rb") as f:
            b64 = base64.b64encode(f.read()).decode()

        q_context = json.dumps(user["data"]["questions_list"])
        sys_msg = f"Questions: {q_context}. Map answers. Output JSON: {{'question_1': 'text', 'answer_1': 'text'}}"
        res = await llm_process(b64, "image/jpeg", sys_msg)

        if status.id not in session.ignored_messages:
            if res:
                user["data"]["model_answer"] = res
                user["state"] = "STEP_3_STUDENT"
                await status.edit("✅ **Answer Key Mapped.** Send student papers.", buttons=None)
        else:
            session.ignored_messages.discard(status.id)

    elif state == "STEP_3_STUDENT":
        ts = int(time.time() * 1000)
        temp_path = f"{Config.STORAGE_PATH}/{uid}_{ts}_std.jpg"
        await event.download_media(file=temp_path)
        status = await event.respond("🚀 Grading... ⏳", buttons=ignore_btn)
        with open(temp_path, "rb") as f:
            std_b64 = base64.b64encode(f.read()).decode()
        try:
            payload = {"student_exam": std_b64, "model_answer": user["data"]["model_answer"]}
            r = requests.post(Config.GRADING_API_URL, json=payload, headers={"X-API-Key": Config.GRADING_API_KEY},
                              timeout=30)
            task_id = r.json().get("task_id")
            if task_id:
                print(f"LOG: Task {task_id} created for User {uid}")
                asyncio.create_task(
                    poll_grading_task(event.chat_id, status, task_id, std_b64, user["phone"], temp_path, uid))
        except Exception as e:
            print(f"ERROR [POST]: {e}")


@bot.on(events.CallbackQuery(pattern=rb"rate\|"))
async def feedback_handler(event):
    data = event.data.decode().split("|")
    task_id, rating = data[1], data[2]
    meta = session.results_meta.get(task_id)
    if meta:
        entry = {
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "telegram_id": event.sender_id, "teacher_phone": meta['teacher_phone'],
            "task_id": task_id, "grade": meta['grade'], "feedback": meta['feedback'],
            "teacher_rating": rating, "student_answer_b64": meta['student_b64'], "ai_graded_b64": meta['ai_graded_b64']
        }
        await save_to_json(entry)
        await event.answer(f"Thank you! Rated as {rating}.", alert=True)
        msg = await event.get_message()
        await event.edit(msg.text + f"\n\n⭐ **Feedback:** {rating.capitalize()}", buttons=None)


if __name__ == '__main__':
    print("🚀 BOT RUNNING")
    bot.start(bot_token=Config.BOT_TOKEN)
    bot.run_until_disconnected()
