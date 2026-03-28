import asyncio
import html
import re
import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CallbackQueryHandler, ContextTypes, CommandHandler
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
    
    # 핑퐁 횟수가 몇 번째인지 사용자에게도 자랑스럽게 보여줍니다!
    thread_badge = f"[핑퐁 {t_data['count']}회차]" if t_data['count'] > 1 else "[새로운 대화 시작]"

    message_text = (
        f"📧 <b>{thread_badge} 이메일 알림</b>\n\n"
        f"🕒 <b>수신 일시:</b> {escape_for_tg(mail_data.get('date', ''))}\n"
        f"👤 <b>보낸 사람:</b> {escape_for_tg(mail_data.get('sender', ''))}\n"
        f"📝 <b>메일 제목:</b> {escape_for_tg(mail_data.get('subject', ''))}\n"
        f"🗂 <b>분류 결과:</b> {escape_for_tg(ai_result.get('category', ''))}\n\n"
        f"💡 <b>전체 흐름 요약:</b>\n{escape_for_tg(ai_result.get('summary', ''))}"
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
                    [InlineKeyboardButton("💾 마크다운(.md) 문서로 저장하기", callback_data=f"save_{uid}")],
                    [InlineKeyboardButton("🚫 이 보낸 사람 블랙리스트 차단 (스팸 등록)", callback_data=f"block_{uid}")],
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
            
            # 첫 번째 조각이 성공적으로 배달되었으면, 이 새로운 말풍선 번호와 방금 만든 '요약본'을 장부에 저장합니다!
            if i == 0:
                from thread_manager import update_thread_data
                update_thread_data(base_subj, msg_id=sent_msg.message_id, latest_summary=ai_result.get("summary", ""))
            
        logger.info(f"텔레그램 스레드(핑퐁) 알림이 성공적으로 연결되었습니다! (메일번호 {uid})")
    except Exception as e:
        logger.error(f"텔레그램 전력망 장애로 소식 전달에 슬프게도 실패했습니다: {e}")

async def handle_button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    사용자가 텔레그램 대화방에서 [마크다운 문서로 저장하기] 버튼을 띡! 눌렀을 때만 작동하는 '동작 감지기'입니다.
    사용자의 명백한 클릭 명령 없이는 어떤 문서 파일도 제멋대로 생성하지 못하도록 통과 지점을 만든 방어벽입니다.
    """
    query = update.callback_query
    
    # 텔레그램 서버에 "주인님이 버튼 누르셨다! 화면에 모래시계 치워!" 라고 신호를 반환합니다.
    await query.answer()

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
                # 너무 버튼을 신나게 여러 번 눌러 중복 낭비를 일으키지 않게, 해당 버튼을 깔끔하게 지워줍니다.
                await query.edit_message_reply_markup(reply_markup=None) 
                # 성공했다는 기쁜 소식을 메시지로 전달합니다.
                await context.bot.send_message(
                    chat_id=query.message.chat_id,
                    text=f"✅ 문서를 내 컴퓨터의 전용 방어 금고에 안전하게 저장했습니다!\n📁 저장 경로(위치): {filepath}"
                )
            else:
                await context.bot.send_message(
                    chat_id=query.message.chat_id,
                    text="❌ 죄송합니다. 보고서를 쓰다가 잉크가 터지는 작은 소동(오류)이 생겼습니다."
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
                # 차단이 성공하면 앗! 차단이 완료되었다는 안내와 함께 기존 버튼 판을 아예 엎어버립니다. (중복 방지)
                await query.edit_message_reply_markup(reply_markup=None) 
                
                await context.bot.send_message(
                    chat_id=query.message.chat_id,
                    text=f"🚨 탕탕! 블랙리스트 확정!\n이제 [{result_msg}] 놈이 보내는 모든 이메일은 가차 없이 파이썬 문지기가 찢어버릴 것입니다!!"
                )
            else:
                await context.bot.send_message(
                    chat_id=query.message.chat_id,
                    text=f"⚠️ 이미 지옥(블랙리스트)에 간 녀석이거나 주소를 잡을 수 없습니다: {result_msg}"
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
                    text="✅ 기특한 제미나이(AI)가 이 메일의 패턴을 머릿속에 완벽히 암기했습니다!\n다음에 이와 비슷한 내용 혹은 형식의 메일이 오면 사용자님을 귀찮게 하지 않고 스스로 알아서 [스킵]하겠습니다. 🧠✨"
                )
            else:
                await context.bot.send_message(
                    chat_id=query.message.chat_id,
                    text=f"⚠️ {msg}"
                )
        else:
            await context.bot.send_message(
                chat_id=query.message.chat_id,
                text="⚠️ 해당 메일 내용이 이미 옛날 것이라 파이썬이 내용을 까먹었습니다."
            )


async def command_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if str(update.message.chat_id) != ALLOWED_CHAT_ID: return
    await update.message.reply_text("✅ 🤖 비서 봇이 정상적으로 살아있으며, 열심히 메일을 감시하고 있습니다!")

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

    # 빈 내용 방어
    if not memo_content:
        await update.message.reply_text("🤔 사장님! 내용을 명령어 뒤에 띄어쓰고 같이 적어주세요!\n👉 예시: /note 🐛버그: 텔레그램 버튼 안 눌림")
        return

    try:
        # 3. 파이썬 시계로 현재 시각 도장 생성
        now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
        
        # 4. 파일 제일 밑(바닥)에 한 줄씩 이어붙이기(Append) 모드인 'a' 로 엽니다. 
        # (절대 'w'로 열면 안 됩니다. 기존 내용이 통째로 엎어(날아감)집니다!)
        with open(IDEA_NOTE_FILE, "a", encoding="utf-8") as f:
            f.write(f"\n- **[{now_str}]** {memo_content}")
            
        logger.info(f"원격 메모가 성공적으로 노트에 추가되었습니다: {memo_content}")
        # 5. 등록 성공 콜백 알림 전송 (안심 효과)
        await update.message.reply_text(f"📝 훌륭한 메모입니다! 데스크톱 장부(`아이디어노트.md`) 최하단에 안전하게 영구 누적 기록해 두었습니다!\n\n[기록된 내용]\n{memo_content}")
        
    except Exception as e:
        logger.error(f"메모 기록 중 치명적인 에러 발생: {e}")
        await update.message.reply_text("🚨 앗! 하드디스크에 메모를 찍으려고 했는데 오류가 발생했습니다. 나중에 다시 시도해 주세요.")

def setup_telegram_handlers(application: Application):
    # 명령을 대기하는 두뇌 회로(수신기)에 '/status', '/note' 옵션을 박아 넣습니다.
    application.add_handler(CommandHandler("status", command_status))
    application.add_handler(CommandHandler("note", handle_memo_command))
    
    # 인라인 버튼(문서 저장 등) 콜백 처리를 위한 리스너입니다.
    application.add_handler(CallbackQueryHandler(handle_button_callback))
    
    logger.info("텔레그램 제어 수신기('/status', '/메모' 등)와 버튼 수신기가 메인 서버에 장착 완료되었습니다.")
