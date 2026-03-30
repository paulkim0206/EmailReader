import asyncio
import html
import re
import datetime
import sys
import os
import subprocess
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CallbackQueryHandler, ContextTypes, CommandHandler, MessageHandler, filters
from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, logger, IDEA_NOTE_FILE
from local_storage import create_and_save_report

# 전 세계의 모든 사용자 중, 오직 '나(등록된 소유자)'에게만 알림을 보내고 명령을 받기 위한 검증용 정보입니다.
# 만약 누군가 내 비서 봇에 몰래 말을 걸어도 ID가 다르면 가차 없이 무시합니다. (보안 철저)
ALLOWED_CHAT_ID = str(TELEGRAM_CHAT_ID)

# 이메일 데이터와 AI가 고생해서 분석한 요약을 컴퓨터 기억장치에 임시로 '잠시' 넣어두는 상자입니다.
# 나중에 유저가 텔레그램 버튼으로 "이거 저장해줘!" 라고 할 때 여기서 꺼내다 씁니다.
# (컴퓨터를 끄면 상자가 비워지는 임시 보관소라 용량 걱정은 전혀 없습니다.)
temp_mail_cache = {}

def escape_for_tg(text):
    """
    마크다운 대신 훨씬 튼튼한 HTML 방식을 쓰기 위해, 꺾쇠(<, >) 등만 살짝 보호합니다!
    이렇게 하면 AI가 쓴 내용이 절대 안 깨지고, 줄바꿈과 기호가 그대로 스마트폰에 예쁘게 나옵니다.
    """
    if not text:
        return ""
    return html.escape(str(text))

async def send_skip_alert(application, mail_data: dict, ai_result: dict):
    """
    V2.6: AI가 '스킵'으로 분류한 메일에 대해 사유와 함께 알림을 보냅니다.
    사용자가 직접 판단할 수 있도록 [그래도 요약해 줘!] 버튼을 제공합니다.
    """
    uid = mail_data.get('uid', '번호없음')
    
    # 나중에 강제 요약 시 꺼내 쓸 수 있도록 임시 저장소에 넣어둡니다.
    temp_mail_cache[uid] = {
        "mail": mail_data,
        "ai": ai_result
    }
    
    msg = (
        f"⏭️ <b>[메일 요약 스킵 알림]</b>\n\n"
        f"📝 <b>제목:</b> {escape_for_tg(mail_data.get('subject', ''))}\n"
        f"👤 <b>보낸 사람:</b> {escape_for_tg(mail_data.get('sender', ''))}\n"
        f"💡 <b>스킵 사유:</b> {escape_for_tg(ai_result.get('skip_reason', '내용 없음'))}\n\n"
        f"<i>AI가 중요하지 않다고 판단했으나, 혹시 보고 싶으시면 아래 버튼을 누르세요.</i>"
    )
    
    keyboard = [[InlineKeyboardButton("📝 그래도 요약해 줘! ✨", callback_data=f"force_summary_{uid}")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await application.bot.send_message(
        chat_id=ALLOWED_CHAT_ID,
        text=msg,
        parse_mode="HTML",
        reply_markup=reply_markup
    )

async def send_email_alert(application: Application, mail_data: dict, ai_result: dict, t_data: dict, base_subj: str):
    """
    새로운 이메일이 오고 AI가 분석을 마쳤을 때 텔레그램으로 배달합니다.
    이때 이전 대화 말풍선을 찾아서 시각적으로 답장(스레드) 형태로 완벽하게 연결합니다.
    """
    uid = mail_data.get('uid', '알수없는번호')
    
    temp_mail_cache[uid] = {
        "mail": mail_data,
        "ai": ai_result
    }
    
    # V1.11.0: 핑퐁 여부는 AI가 판단한 is_thread로 확인합니다.
    is_thread = ai_result.get('is_thread', False)
    thread_index = ai_result.get('thread_index', 1)

    message_text = (
        f"📧 <b>이메일 알림</b>\n\n"
        f"🕒 <b>수신 일시:</b> {escape_for_tg(mail_data.get('date', ''))}\n"
        f"👤 <b>보낸 사람:</b> {escape_for_tg(mail_data.get('sender', ''))}\n"
        f"📝 <b>메일 제목:</b> {escape_for_tg(mail_data.get('subject', ''))}\n\n"
        f"💡 <b>요약:</b>\n{escape_for_tg(ai_result.get('summary', ''))}"
    )

    # 텔레그램 메신저는 욕심을 부려 한 번에 너무 많은 글씨(4096자)를 쑤셔 넣으면
    # 배탈이 나서 에러를 내며 멈춥니다! 
    # 그래서 이 엄청난 양의 보고서를 안전한 크기(약 4000자 블록 단위)로 
    # 가위로 예쁘게 잘라서 연속으로 보내주는 고급 배달 스킬을 사용합니다.
    max_length = 4000
    message_chunks = [message_text[i:i + max_length] for i in range(0, len(message_text), max_length)]

    try:
        # 가위로 조각낸 텍스트 상자 꾸러미들을 순서대로 차곡차곡 배달합니다.
        for i, chunk in enumerate(message_chunks):
            reply_markup = None
            
            # 여러 개의 쪼개진 편지가 왔을 때, 마지막 장 맨 아랫부분 바닥에만 버튼을 달아줍니다.
            if i == len(message_chunks) - 1:
                # 사용자가 버튼을 누르면 "save_<고유번호>" 또는 "block_<고유번호>" 란 암호 신호를 튕깁니다!
                keyboard = [
                    [InlineKeyboardButton("💾 HTML 리포트 받기 📥", callback_data=f"save_{uid}")],
                    [InlineKeyboardButton("🔇 이 발송자, 앞으로 요약 알림 받지 않기", callback_data=f"block_{uid}")],
                    [InlineKeyboardButton("👎 이런 류의 메일 내용 요약 제외 (AI 학습)", callback_data=f"learn_{uid}")]
                ]
                reply_markup = InlineKeyboardMarkup(keyboard)

            sent_msg = await application.bot.send_message(
                chat_id=ALLOWED_CHAT_ID,
                text=chunk,
                parse_mode="HTML",
                reply_to_message_id=t_data.get("msg_id") if i == 0 else None,
                reply_markup=reply_markup
            )
            
            # 첫 번째 조각이 배달되면 텔레그램 말풍선 ID를 t_data에 저장해 둡니다.
            # (main.py에서 save_thread_entry 호출 시 msg_id를 업데이트합니다)
            if i == 0:
                t_data["msg_id"] = sent_msg.message_id
            
        logger.info(f"텔레그램 알림 전송 완료! (메일번호 {uid})")
    except Exception as e:
        logger.error(f"텔레그램 전력망 장애로 소식 전달에 슬프게도 실패했습니다: {e}")

async def handle_button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    사용자가 텔레그램 대화방에서 [마크다운 문서로 저장하기] 버튼을 띡! 눌렀을 때만 작동하는 '동작 감지기'입니다.
    사용자의 명백한 클릭 명령 없이는 어떤 문서 파일도 제멋대로 생성하지 못하도록 통과 지점을 만든 방어벽입니다.
    """
    query = update.callback_query

    # 텔레그램 서버에 "주인님이 버튼 누르셨다! 화면에 모래시계 치워!" 라고 신호를 반환합니다.
    # [V2.1] 클라우드 환경에서 응답이 늦어도 이후 처리가 중단되지 않도록 try/except로 방어합니다.
    try:
        await query.answer()
    except Exception:
        pass  # Timeout이 나도 아래 실제 처리 로직은 계속 진행합니다.

    # 버튼 뒤에 숨겨두었던 암호문 (예: 'save_10번편지')을 가져옵니다.
    data = query.data
    
    # 보안 통과 검사: 암호문에 맞게 분기 처리합니다.
    if data.startswith("save_"):
        uid = data.split("_")[1]
        
        # 아까 준비해 둔 '임시 상자'에서 이 번호의 메일을 끄집어냅니다.
        cache_data = temp_mail_cache.get(uid)
        
        if cache_data:
            # 드디어 우리가 앞서 만든 4단계 모듈 '마크다운 자동 생성기'를 가동합니다!
            success, filepath = create_and_save_report(cache_data["mail"], cache_data["ai"])
            
            if success:
                # [V2.5] 텔레그램으로 파일을 직접 전송합니다! (클라우드 환경 대응)
                try:
                    with open(filepath, 'rb') as document:
                        await context.bot.send_document(
                            chat_id=query.message.chat_id,
                            document=document,
                            filename=os.path.basename(filepath),
                            caption="✅ 요청하신 HTML 분석 보고서 배달 완료! 📥"
                        )
                    # 너무 버튼을 신나게 여러 번 눌러 중복 낭비를 일으키지 않게, 해당 버튼을 깔끔하게 지워줍니다.
                    await query.edit_message_reply_markup(reply_markup=None)
                except Exception as e:
                    logger.error(f"파일 전송 중 오류 발생: {e}")
                    await context.bot.send_message(
                        chat_id=query.message.chat_id,
                        text=f"❌ 파일 전송 중 오류가 발생했습니다: {e}"
                    )
            else:
                await context.bot.send_message(
                    chat_id=query.message.chat_id,
                    text="❌ 죄송합니다. 보고서를 작성하다가 작은 소동(오류)이 생겼습니다."
                )
        else:
            await context.bot.send_message(
                chat_id=query.message.chat_id,
                text="⚠️ 해당 메일 내용이 컴퓨터의 단기 기억 용량에서 이미 지워졌습니다. (오래된 메일이거나 재부팅됨)"
            )
            
    # [새로운 분기 기능] 사용자가 블랙리스트 버튼을 눌렀을 때!!
    elif data.startswith("block_"):
        uid = data.split("_")[1]
        cache_data = temp_mail_cache.get(uid)

        if cache_data:
            from blacklist_manager import add_to_blacklist
            sender = cache_data["mail"].get('sender', '')
            success, result_msg = add_to_blacklist(sender)

            if success:
                await query.edit_message_reply_markup(reply_markup=None)
                await context.bot.send_message(
                    chat_id=query.message.chat_id,
                    text=f"✅ 등록 완료.\n앞으로 [{result_msg}] 님의 메일은 텔레그램 요약 알림을 보내지 않고 조용히 패스하겠습니다.\n(원본 이메일은 이메일함에 정상적으로 보관되어 있습니다.)"
                )
            else:
                await context.bot.send_message(
                    chat_id=query.message.chat_id,
                    text=f"⚠️ 이미 등록된 발송자이거나 주소를 확인할 수 없습니다: {result_msg}"
                )
        else:
            await context.bot.send_message(
                chat_id=query.message.chat_id,
                text="⚠️ 해당 메일 내용이 이미 옛날 것이라 차단할 주소록을 분실했습니다."
            )
            
    # [새로운 분기 3] 사용자가 AI 학습(👎) 단추를 눌렀을 때!!
    elif data.startswith("learn_"):
        uid = data.split("_")[1]
        cache_data = temp_mail_cache.get(uid)
        
        if cache_data:
            from feedback_manager import add_learning_preference
            subject = cache_data["mail"].get("subject", "제목 없음")
            summary = cache_data["ai"].get("summary", "데이터 없음")
            
            success, msg = add_learning_preference(subject, summary)
            
            if success:
                # 사용자가 또 광클릭 못하게 기존의 모든 메뉴판(버튼들)을 싹 날려줍니다.
                await query.edit_message_reply_markup(reply_markup=None)
                await context.bot.send_message(
                    chat_id=query.message.chat_id,
                    text=(
                        f"🧠 요약 제외 학습 완료!\n\n"
                        f"📝 등록된 제목 패턴: [{escape_for_tg(subject)}]\n\n"
                        f"앞으로 이와 비슷한 내용의 메일이 오면 사용자님을 귀찮게 하지 않고 "
                        f"스스로 조용히 [스킵]하겠습니다. ✨"
                    )
                )
            else:
                await context.bot.send_message(
                    chat_id=query.message.chat_id,
                    text=f"ℹ️ 이미 학습된 패턴입니다! 중복 등록은 생략했습니다.\n(제목: [{escape_for_tg(subject)}])"
                )
        else:
            await context.bot.send_message(
                chat_id=query.message.chat_id,
                text="⚠️ 해당 메일 내용이 이미 옛날 것이라 파이썬이 내용을 까먹었습니다."
            )

    # [새로운 분기 4] 부장님의 준엄한 명령: "그래도 요약해!"
    elif data.startswith("force_summary_"):
        uid = data.split("_")[2]
        cache_data = temp_mail_cache.get(uid)
        
        if cache_data:
            logger.info(f"강제 요약 요청 접수 (UID: {uid})")
            # 1. 화면에 "공사 중..." 표시
            await query.edit_message_text(
                text=f"{query.message.text}\n\n⏳ <b>부장님 명령 접수! 강제로 다시 분석 중입니다...</b>",
                parse_mode="HTML"
            )
            
            # 2. 강제 요약 수행 (순환 참조 방지를 위해 로컬 임포트)
            from ai_processor import process_email_with_ai
            from thread_manager import format_threads_for_prompt
            
            mail_data = cache_data["mail"]
            history = format_threads_for_prompt()
            
            # force_summarize=True 옵션을 주어 모든 엔진을 풀가동합니다.
            new_ai_result = await asyncio.to_thread(process_email_with_ai, mail_data, history, force_summarize=True)
            
            # 3. 분석 완료 시 알림 전송 (t_data는 새로 생성)
            await send_email_alert(context.application, mail_data, new_ai_result, {}, mail_data.get('subject'))
            
            # 4. 기존 스킵 버튼은 지워줍니다.
            await query.edit_message_reply_markup(reply_markup=None)
        else:
            await context.bot.send_message(
                chat_id=query.message.chat_id,
                text="⚠️ 임시 보관소에서 메일 데이터를 찾을 수 없습니다. (재부팅됨)"
            )


async def command_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if str(update.message.chat_id) != ALLOWED_CHAT_ID: return
    await update.message.reply_text("✅ 🤖 비서 봇이 정상적으로 살아있으며, 열심히 메일을 감시하고 있습니다!")

async def handle_help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    [V3.7 기계식 매뉴얼 뱉기] 
    부장님이 '/help'나 '/명령어'를 치면 제미나이(AI)는 쳐다도 안 보고 파이썬 서버가 즉각 매뉴얼 파일 텍스트를 기계적으로 출력합니다.
    """
    if str(update.message.chat_id) != ALLOWED_CHAT_ID: return
    
    import os
    try:
        # 단일 소스 원칙에 따라 telegram_commands.txt 파일을 그대로 읽어서 뱉습니다.
        prompt_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "prompts", "telegram_commands.txt")
        with open(prompt_path, "r", encoding="utf-8") as f:
            manual_text = f.read()
        await update.message.reply_text(f"🤖 [기계식 봇 매뉴얼 출력]\n\n{manual_text}")
    except Exception as e:
        await update.message.reply_text("🚨 매뉴얼 파일을 찾을 수 없습니다.")

async def handle_export_notes(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    [V4.2] 부장님의 명령('/수첩')에 의해 1번부터 끝번까지의 전체 메모 장부(JSON 파싱 텍스트)를 배달합니다.
    데이터가 길 수 있으므로 깔끔하게 파일(Document) 형태로 전송합니다.
    """
    if str(update.message.chat_id) != ALLOWED_CHAT_ID: return
    
    from memo_manager import get_all_memos
    from config import USER_NOTES_FILE
    import tempfile
    
    all_notes_text = get_all_memos()
    
    # 텔레그램 한도 초과 방지 및 가독성을 위해 텍스트 파일로 뽑아서 발송합니다.
    try:
        with tempfile.NamedTemporaryFile(mode='w+', delete=False, suffix='.txt', encoding='utf-8') as tmp:
            tmp.write("====== [부장님의 전체 공용 수첩(JSON) 다운로드 원본] ======\n\n")
            tmp.write(all_notes_text)
            tmp_path = tmp.name
            
        with open(tmp_path, 'rb') as f:
            await context.bot.send_document(
                chat_id=update.message.chat_id,
                document=f,
                filename=f"수첩_전체백업_{datetime.datetime.now().strftime('%Y%m%d')}.txt",
                caption="🗄️ 부장님! [1번]부터 끝번까지 기록된 수첩 전체 원본(DB)을 배달해 드립니다!"
            )
        import os
        os.unlink(tmp_path)
    except Exception as e:
        await update.message.reply_text(f"🚨 수첩 파일 배달 중 오류: {e}")

async def handle_memo_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    사용자가 '/note' 또는 '/메모' 명령어로 보낸 알맹이 텍스트만 빼내어 아이디어노트.md 파일 하단에 영구 누적 기록(Append)합니다.
    """
    # 1. 철통 보안 통과 검사 (사용자 본인, 즉 주인님만 쓸 수 있게 차단)
    chat_id = str(update.message.chat_id)
    if chat_id != ALLOWED_CHAT_ID:
        return

    text = update.message.text
    # 2. 명령어 껍데기('/note ')를 정규식 가위로 강제로 잘라냅니다.
    memo_content = re.sub(r'^/note\s*', '', text, flags=re.IGNORECASE).strip()

    if not memo_content:
        await update.message.reply_text("🤔 부장님! 내용을 명령어 뒤에 띄어쓰고 같이 적어주세요!\n👉 예시: /note 🐛버그: 텔레그램 버튼 안 눌림")
        return

    try:
        # [V4.0] 새로운 공용 수첩(JSON) 관리자를 불러와 메모를 넘깁니다.
        from memo_manager import save_memo
        
        success = save_memo(memo_content)
        
        if success:
            logger.info(f"원격 메모가 성공적으로 노트에 추가되었습니다: {memo_content}")
            # 5. 등록 성공 콜백 알림 전송 (안심 효과)
            await update.message.reply_text(f"📝 훌륭한 메모입니다! [공용 수첩(user_notes.json)]에 안전하게 영구 기록해 두었습니다!\n\n[기록된 내용]\n{memo_content}")
        else:
            await update.message.reply_text("🚨 앗! 수첩(JSON)에 메모를 적다가 펜이 부러졌습니다. 서버 상태를 확인해 주세요.")
        
    except Exception as e:
        logger.error(f"메모 기록 중 치명적인 에러 발생: {e}")
        await update.message.reply_text("🚨 앗! 하드디스크에 메모를 찍으려고 했는데 오류가 발생했습니다. 나중에 다시 시도해 주세요.")

async def handle_update_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    [V2.0 클라우드용 핵심 무기] 
    사용자가 IDE에서 코드를 고치고 깃허브에 올린 뒤 텔레그램에 '/update'라고 치면,
    클라우드에 있는 봇이 스스로 깃허브에서 새 코드를 다운받고 자기 자신을 재부팅(소생)시킵니다!
    """
    chat_id = str(update.message.chat_id)
    if chat_id != ALLOWED_CHAT_ID:
        return

    await update.message.reply_text("🔄 [시스템 업데이트] 깃허브에서 새로운 똑똑해진 뇌(코드)를 다운로드합니다...")
    
    try:
        # 1. 깃허브에서 최신 코드 강제 당겨오기 (Pull)
        result = await asyncio.to_thread(
            subprocess.run,
            ['git', 'pull'],
            capture_output=True,
            text=True
        )
        
        if result.returncode == 0:
            await update.message.reply_text(f"✅ 다운로드 완벽 성공!\n\n[처리 결과]\n{result.stdout}\n\n🤖 3초 뒤, 봇이 스스로 전원을 껐다 켜서(재부팅) 새로운 패치를 장착합니다. 잠시 후 뵙겠습니다!")
            # 2. 메세지를 무사히 보내고, 3초 뒤에 자기 자신을 파이썬 명령어로 100% 껐다 켭니다(Restart).
            await asyncio.sleep(3)
            # os.execl 은 현재 실행 중인 파이썬 프로세스를 '새 파이썬 프로세스'로 갈아 끼워버리는 완벽한 재부팅 기술입니다.
            os.execl(sys.executable, sys.executable, *sys.argv)
        else:
            await update.message.reply_text(f"❌ 다운로드 실패! 코드가 꼬였을 수 있습니다.\n\n[에러 내용]\n{result.stderr}")
            
    except Exception as e:
        logger.error(f"업데이트 중 알 수 없는 오류 발생: {e}")
        await update.message.reply_text(f"🚨 업데이트 중 오류가 발생했습니다: {e}")

async def handle_normal_chat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    [V3.0 대화형 비서 기능]
    사용자가 명령어가 아닌 일반 대화(예: "안녕?", "오늘 날씨 어때?")를 입력했을 때 작동합니다.
    제미나이 AI가 비서의 자아로 답장합니다.
    """
    if str(update.message.chat_id) != ALLOWED_CHAT_ID:
        return

    user_text = update.message.text
    
    # [V5.0] 장기 기억을 위해 부장님의 말씀을 즉시 장부에 기록합니다.
    from chat_manager import save_chat_log
    save_chat_log(role='user', content=user_text)
    
    # [V3.2] 사용자가 이전 메시지에 답장(Reply)을 한 경우 그 텍스트를 파악합니다.
    replied_text = None
    if update.message.reply_to_message and update.message.reply_to_message.text:
        replied_text = update.message.reply_to_message.text
    
    # 텔레그램 화면 상단에 "봇이 타이핑 중..." (Typing action)을 띄워 생동감을 줍니다.
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action='typing')
    
    try:
        # 비서 AI 뇌(로직)를 불러옵니다.
        from ai_processor import chat_with_secretary
        
        # AI가 생각해서 답변을 만들어옵니다. (기다리는 동안 봇이 멈추지 않게 비동기로 처리)
        ai_reply = await asyncio.to_thread(chat_with_secretary, user_text, replied_text)
        
        # [V3.2] AI의 답변에서 오답 노트 태그([[LEARN]]...[[/LEARN]])를 파싱(추출)합니다.
        import re
        learn_match = re.search(r'\[\[LEARN\]\](.*?)\[\[/LEARN\]\]', ai_reply, re.DOTALL)
        
        if learn_match:
            rule_text = learn_match.group(1).strip()
            
            # 추출된 규칙을 피드백 매니저를 통해 오답 노트 장부에 영구 저장합니다.
            from feedback_manager import add_correction
            add_correction(rule_text)
            
            # 태그가 포함된 원본 답변에서 해당 태그 부분만 깔끔하게 지워냅니다.
            ai_reply = re.sub(r'\[\[LEARN\]\].*?\[\[/LEARN\]\]', '', ai_reply, flags=re.DOTALL).strip()
            ai_reply += f"\n\n*(✅ 비서가 방금 지적하신 내용을 오답 노트 장부에 영구 기록하여 학습했습니다!)*"

        # [V4.0 코어 기능 2/2] AI가 스스로 부장님의 메모 지시를 눈치채고 던진 수첩 기록 태그 가로채기!
        memo_match = re.search(r'\[\[SAVE_MEMO\]\](.*?)\[\[/SAVE_MEMO\]\]', ai_reply, re.DOTALL)
        if memo_match:
            memo_text = memo_match.group(1).strip()
            from memo_manager import save_memo
            save_memo(memo_text)
            ai_reply = re.sub(r'\[\[SAVE_MEMO\]\].*?\[\[/SAVE_MEMO\]\]', '', ai_reply, flags=re.DOTALL).strip()

        # [V4.2] 삭제 명령 (DELETE_MEMO) 가로채기
        del_match = re.search(r'\[\[DELETE_MEMO\]\](.*?)\[\[/DELETE_MEMO\]\]', ai_reply, re.DOTALL)
        if del_match:
            try:
                target_id = int(del_match.group(1).strip())
                from memo_manager import delete_memo
                delete_memo(target_id)
            except ValueError: pass
            ai_reply = re.sub(r'\[\[DELETE_MEMO\]\].*?\[\[/DELETE_MEMO\]\]', '', ai_reply, flags=re.DOTALL).strip()

        # [V4.2] 수정 명령 (UPDATE_MEMO) 가로채기
        upd_match = re.search(r'\[\[UPDATE_MEMO\]\](.*?)\[\[/UPDATE_MEMO\]\]', ai_reply, re.DOTALL)
        if upd_match:
            payload = upd_match.group(1).strip()
            if "|" in payload:
                try:
                    parts = payload.split('|', 1)
                    target_id = int(parts[0].strip())
                    new_content = parts[1].strip()
                    from memo_manager import update_memo
                    update_memo(target_id, new_content)
                except ValueError: pass
            ai_reply = re.sub(r'\[\[UPDATE_MEMO\]\].*?\[\[/UPDATE_MEMO\]\]', '', ai_reply, flags=re.DOTALL).strip()
            # 이미 비서가 자연어로 "기억하겠습니다!" 라고 뱉었으므로, 별도의 시스템 메시지는 추가하지 않습니다.

        # [V5.0] 장기 기억을 위해 피아니의 답변도 장부에 기록합니다.
        from chat_manager import save_chat_log
        save_chat_log(role='assistant', content=ai_reply)

        # 만들어진 최종 답변을 텔레그램으로 보냅니다.
        await update.message.reply_text(ai_reply)
        
    except Exception as e:
        logger.error(f"대화 처리 중 오류: {e}")
        await update.message.reply_text("🚨 앗, 부장님! 방금 머리가 좀 아파서(서버 오류) 말씀을 제대로 못 들었습니다. 다시 말씀해 주시겠어요?")

def setup_telegram_handlers(application: Application):
    # 명령을 대기하는 두뇌 회로(수신기)에 '/status', '/note', '/update' 옵션을 박아 넣습니다.
    application.add_handler(CommandHandler("status", command_status))
    application.add_handler(CommandHandler("note", handle_memo_command))
    application.add_handler(CommandHandler("update", handle_update_command))
    application.add_handler(CommandHandler("help", handle_help_command))
    application.add_handler(CommandHandler("notelist", handle_export_notes))
    
    # 인라인 버튼(문서 저장 등) 콜백 처리를 위한 리스너입니다.
    application.add_handler(CallbackQueryHandler(handle_button_callback))
    
    # [V3.0] 일반적인 텍스트 대화(명령어 제외)를 감지하는 수신기입니다.
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_normal_chat))
    
    logger.info("텔레그램 제어 수신기('/status', '/메모' 등)와 일상 대화 수신기가 메인 서버에 장착 완료되었습니다.")
