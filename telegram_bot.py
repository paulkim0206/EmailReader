import asyncio
import io
import html
import re
import datetime
import sys
import os
import subprocess
import json
import pytz
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove
from telegram.ext import Application, CallbackQueryHandler, ContextTypes, CommandHandler, MessageHandler, filters
from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, logger, TIMEZONE_FILE, USER_TIMEZONE

# 전 세계의 모든 사용자 중, 오직 '나(등록된 소유자)'에게만 알림을 보내고 명령을 받기 위한 검증용 정보입니다.
# 만약 누군가 내 비서 봇에 몰래 말을 걸어도 ID가 다르면 가차 없이 무시합니다. (보안 철저)
ALLOWED_CHAT_ID = str(TELEGRAM_CHAT_ID)

# 이메일 데이터와 AI가 고생해서 분석한 요약을 컴퓨터 기억장치에 임시로 '잠시' 넣어두는 상자입니다.
# 나중에 유저가 텔레그램 버튼으로 "이거 저장해줘!" 라고 할 때 여기서 꺼내다 씁니다.
# (컴퓨터를 끄면 상자가 비워지는 임시 보관소라 용량 걱정은 전혀 없습니다.)
temp_mail_cache = {}

def clear_temp_cache():
    """
    [V12.13] 임시 저장소(메일 원본 캐시)를 싹 비우는 대청소 함수입니다.
    메모리 점유율을 낮추기 위해 정해진 시간(월/목 03시)에 호출됩니다.
    """
    global temp_mail_cache
    cache_count = len(temp_mail_cache)
    temp_mail_cache.clear()
    logger.info(f"🧹 임시 저장소 대청소 완료! ({cache_count}건의 데이터를 정리했습니다.)")

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
    
    # [V12.12] 요약본은 장부에 있으므로 원본 본문만 캐시합니다.
    temp_mail_cache[uid] = {
        "mail": mail_data
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
                # [V11.10] 원본 버튼 삭제로 공간이 넓어져 '요약제외'로 명칭 복구
                keyboard = [[
                    InlineKeyboardButton("📌 보고서", callback_data=f"rpt_{uid}"),
                    InlineKeyboardButton("👎 요약제외", callback_data=f"learn_{uid}")
                ]]
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

async def send_skip_alert(application: Application, mail_data: dict, ai_result: dict):
    """
    [V6.0] 부장님이 가르친 패턴에 의해 메일이 스킵되었을 때 알림을 보냅니다.
    스킵 사유를 명시하고, 혹시라도 요약을 원하실 경우를 위해 [그래도 요약해줘] 버튼을 같이 드립니다.
    """
    uid = mail_data.get('uid', '알수없는번호')
    skip_reason = ai_result.get('skip_reason', '사용자 지정 패턴과 일치함')
    
    # [V12.12] 메모리 절약을 위해 제목 등은 캐시하지 않고, 강제 요약 시에만 활용할 메일 데이터만 보관합니다.
    temp_mail_cache[uid] = {
        "mail": mail_data
    }

    message_text = (
        f"🔇 <b>메일 스킵 알림 (학습된 패턴)</b>\n\n"
        f"🕒 <b>수신:</b> {escape_for_tg(mail_data.get('date', ''))}\n"
        f"👤 <b>발신:</b> {escape_for_tg(mail_data.get('sender', ''))}\n"
        f"📝 <b>제목:</b> {escape_for_tg(mail_data.get('subject', ''))}\n"
        f"🚫 <b>사유:</b> {escape_for_tg(skip_reason)}\n\n"
        f"위 메일은 부장님이 가르쳐주신 패턴과 유사하여 요약을 생략했습니다."
    )

    keyboard = [
        [InlineKeyboardButton("⚡ 그래도 요약해줘!", callback_data=f"force_summary_{uid}")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    try:
        await application.bot.send_message(
            chat_id=ALLOWED_CHAT_ID,
            text=message_text,
            parse_mode="HTML",
            reply_markup=reply_markup
        )
        logger.info(f"스킵 알림 전송 완료 (UID: {uid})")
    except Exception as e:
        logger.error(f"스킵 알림 전송 실패: {e}")

async def send_failure_alert(application: Application, mail_data: dict):
    """
    [V12.8] 실시간 및 배경 재시도까지 모두 실패했을 때 부장님께 정중하게 최종 보고합니다.
    (부장님 지시에 따라 .eml 원본 파일 전송 기능은 폐지되었습니다.)
    """
    subject = mail_data.get('subject', '제목없음')
    
    message_text = (
        f"🚨 <b>최종 업무 보고: AI 요약 분석 불가</b>\n\n"
        f"부장님, AI 서버의 일시적인 응답 지연으로 인해 해당 메일의 요약본을 생성하는 데 최종적으로 실패했습니다.\n\n"
        f"📝 <b>메일 제목:</b> {escape_for_tg(subject)}\n"
        f"👤 <b>보낸 사람:</b> {escape_for_tg(mail_data.get('sender', ''))}\n\n"
        f"🚨 <b>최종 안내:</b> 번거로우시겠지만 메일함에서 직접 원문을 확인해 주시면 감사하겠습니다. 😭"
    )
    
    try:
        await application.bot.send_message(
            chat_id=ALLOWED_CHAT_ID,
            text=message_text,
            parse_mode="HTML"
        )
        logger.info(f"최종 실패 알림 전송 완료 (UID: {mail_data.get('uid')})")
    except Exception as e:
        logger.error(f"최종 실패 알림 전송 실패: {e}")

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
    
    # [분기 3] 보안 통과 검사: 암호문에 맞게 분기 처리합니다.
    if data.startswith("save_"):
        # [V11.10] 부장님의 지시로 원본 저장 기능은 폐지되었습니다. 
        # (혹시라도 이전 메시지의 버튼을 누를 경우를 대비하여 안내를 보냅니다.)
        await query.answer(text="⚠️ 해당 기능(메일 원본 저장)은 부장님 지시로 폐지되었습니다.", show_alert=True)
        return

    # [새로운 분기 1] 부장님의 "이건 보고서에 넣어!" 명령 (핀 버튼)
    elif data.startswith("rpt_"):
        uid = data.split("_")[1]
        from thread_manager import find_entry_by_uid, mark_as_report_target
        
        # 1. 먼저 장부에서 정보를 찾습니다 (V12.12 핵심: 업데이트 후에도 작동!)
        info = find_entry_by_uid(uid)
        
        if info:
            thread_key = info["thread_key"]
            thread_index = info["thread_index"]
            
            if mark_as_report_target(thread_key, thread_index, status=True):
                try:
                    await query.answer(text="✅ 해당 업무가 내일 아침 일일보고서 대상으로 등록되었습니다! 📋", show_alert=True)
                    await query.edit_message_reply_markup(reply_markup=None)
                except Exception: pass
            else:
                await query.answer(text="❌ 장부 기록 중 문제가 생겼습니다. 나중에 다시 시도해 주세요.", show_alert=True)
        else:
            await query.answer(text="⚠️ 너무 오래된 메일이거나 장부에서 찾을 수 없습니다. (30일 경과 등)", show_alert=True)
        return

    # [새로운 분기 3] 사용자가 AI 학습(👎) 단추를 눌렀을 때!!
    elif data.startswith("learn_"):
        uid = data.split("_")[1]
        from thread_manager import find_entry_by_uid
        from feedback_manager import add_learning_preference
        
        # 1. 장부 또는 캐시에서 제목과 내용을 확보합니다.
        info = find_entry_by_uid(uid)
        subject = None
        summary = None
        
        if info:
            subject = info["thread_key"]
            summary = info["summary"]
        else:
            # 장부에도 없으면(스킵된 메일 등) 캐시를 확인합니다.
            cache_data = temp_mail_cache.get(uid)
            if cache_data:
                subject = cache_data["mail"].get("subject")
                summary = "데이터 없음(스킵됨)"

        if subject:
            success, msg = add_learning_preference(subject, summary or "데이터 없음")
            if success:
                await query.edit_message_reply_markup(reply_markup=None)
                await context.bot.send_message(
                    chat_id=query.message.chat_id,
                    text=f"🧠 요약 제외 학습 완료!\n\n📝 등록된 제목 패턴: [{escape_for_tg(subject)}]\n\n앞으로 이와 비슷한 메일은 스킵하겠습니다. ✨"
                )
            else:
                await context.bot.send_message(chat_id=query.message.chat_id, text=f"ℹ️ 이미 학습된 패턴입니다.")
        else:
            await context.bot.send_message(chat_id=query.message.chat_id, text="⚠️ 정보를 찾을 수 없습니다.")
        return

    # [새로운 분기 4] 부장님의 준엄한 명령: "그래도 요약해!"
    elif data.startswith("force_summary_"):
        uid = data.split("_")[2]
        
        # 1. 텍스트부터 먼저 표시 (부장님 안심용)
        try:
            safe_current_text = escape_for_tg(query.message.text)
            await query.edit_message_text(
                text=f"{safe_current_text}\n\n⏳ <b>부장님 명령 접수! 서버에서 데이터를 다시 가져와 분석 중입니다...</b>",
                parse_mode="HTML"
            )
        except Exception: pass

        # 2. 메일 데이터 확보 (캐시 우선 -> 없으면 서버 실시간 패치)
        mail_data = None
        cache_data = temp_mail_cache.get(uid)
        if cache_data:
            mail_data = cache_data.get("mail")
        else:
            # [V12.12] 업데이트 후 캐시가 비었을 때 서버에서 직접 가져옵니다!
            from mail_parser import fetch_parsed_mail
            mail_data = await asyncio.to_thread(fetch_parsed_mail, uid)

        if mail_data:
            from ai_processor import process_email_with_ai
            from thread_manager import format_threads_for_prompt
            history = format_threads_for_prompt()
            
            new_ai_result = await asyncio.to_thread(process_email_with_ai, mail_data, history, force_summarize=True)
            
            # 분석 완료 시 알림 및 장부 기록
            from thread_manager import save_thread_entry
            await send_email_alert(context.application, mail_data, new_ai_result, {}, mail_data.get('subject'))
            save_thread_entry(new_ai_result.get('thread_key'), new_ai_result.get('thread_index'), new_ai_result.get('summary'), None, uid)
            
            try: await query.edit_message_reply_markup(reply_markup=None)
            except Exception: pass
        else:
            await context.bot.send_message(chat_id=query.message.chat_id, text="🚨 메일 서버에서 데이터를 가져오는 데 실패했습니다.")
        return

    # [V7.0 새로운 분기] 사용자가 시간대(tz_) 수동 버튼을 눌렀을 때!!
    elif data.startswith("tz_"):
        new_tz = data.replace("tz_", "")
        
        # 1. 설정 파일에 저장
        try:
            with open(TIMEZONE_FILE, "w", encoding="utf-8") as f:
                json.dump({"timezone": new_tz}, f, ensure_ascii=False, indent=4)
            
            # 2. 현재 실행 중인 프로그램 설정 즉시 업데이트
            import config
            config.USER_TIMEZONE = new_tz
            
            import pytz
            now = datetime.datetime.now(pytz.timezone(new_tz))
            
            await query.edit_message_reply_markup(reply_markup=None)
            await context.bot.send_message(
                chat_id=query.message.chat_id,
                text=f"✅ 시계 동기화 완료!\n이제 피아니의 시계가 <b>[{new_tz}]</b> 기준으로 흐릅니다.\n📅 현재 시간: {now.strftime('%H:%M:%S')}",
                parse_mode="HTML"
            )
        except Exception as e:
            logger.error(f"타임존 저장 중 오류: {e}")
            await context.bot.send_message(chat_id=query.message.chat_id, text="🚨 시계 설정 저장 중 오류가 발생했습니다.")

async def handle_location_update(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    [V7.0] 부장님이 보내주신 GPS 위도/경도를 보고 AI가 전 세계 어디인지 맞추는 스마트 로직입니다.
    """
    if str(update.message.chat_id) != ALLOWED_CHAT_ID: return
    if not update.message.location: return

    lat = update.message.location.latitude
    lon = update.message.location.longitude
    
    await update.message.reply_text("📍 위치 정보 수신 완료! 부장님이 지금 세계 어느 나라에 계시는지 분석 중입니다... 🧐", reply_markup=ReplyKeyboardRemove())
    
    try:
        from ai_processor import chat_with_secretary
        # 제미나이(AI)에게 좌표 해석을 정교하게 부탁합니다. (JSON 형식으로 유도)
        prompt = (
            f"부장님이 현재 위도 {lat}, 경도 {lon} 위치에 계십니다. "
            f"이 좌표가 속한 '국가명'과 'IANA 타임존 이름(예: Asia/Seoul, Europe/Paris)'을 정확히 알려주세요. "
            f"반드시 아래 JSON 형식으로만 짧게 답하세요:\n"
            '{"country": "국가명", "timezone": "타임존이름"}'
        )
        
        ai_response = await asyncio.to_thread(chat_with_secretary, prompt)
        
        # AI 결과 파싱
        import json as pyjson
        import re
        match = re.search(r'\{.*\}', ai_response, re.DOTALL)
        if match:
            geo_info = pyjson.loads(match.group())
            country = geo_info.get("country", "알 수 없는 나라")
            new_tz = geo_info.get("timezone", "UTC")
            
            # 1. 파일 저장
            with open(TIMEZONE_FILE, "w", encoding="utf-8") as f:
                pyjson.dump({"timezone": new_tz, "country": country}, f, ensure_ascii=False, indent=4)
            
            # 2. 즉시 반영
            import config
            config.USER_TIMEZONE = new_tz
            
            import pytz
            now = datetime.datetime.now(pytz.timezone(new_tz))
            
            await update.message.reply_text(
                f"🌍 <b>위치 인식 성공!</b>\n\n"
                f"부장님은 지금 <b>[{country}]</b>에 계시는군요!\n"
                f"피아니의 시계를 <b>[{new_tz}]</b> 시간대로 맞췄습니다.\n"
                f"📅 현재 현지 시간: {now.strftime('%Y-%m-%d %H:%M:%S')}",
                parse_mode="HTML"
            )
        else:
            await update.message.reply_text("🚨 좌표 분석에 실패했습니다. 수동 버튼으로 지역을 선택해 주세요!")
            
    except Exception as e:
        logger.error(f"위치 기반 타임존 분석 중 오류: {e}")
        await update.message.reply_text("🚨 GPS 정보를 처리하는 도중 오류가 발생했습니다.")


async def command_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if str(update.message.chat_id) != ALLOWED_CHAT_ID: return
    
    # [V12.16] 부장님을 위한 '하단 고정형 스마트 메뉴' 설계 및 장착
    keyboard = [['❓ 도움말', '📝 메모보기', '🔄 업데이트']]
    # resize_keyboard=True 로 하면 버튼 크기가 화면에 맞게 아주 콤팩트하고 예쁘게 조절됩니다.
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    
    await update.message.reply_text(
        "✅ 🤖 <b>비서 피아니가 정상적으로 살아있으며, 부장님의 모든 명령을 대기 중입니다!</b>\n\n"
        "하단의 버튼을 누르시면 도움말을 보거나 메모 현황을 즉시 확인하실 수 있습니다. 👇",
        reply_markup=reply_markup,
        parse_mode="HTML"
    )

async def handle_time_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    [V7.0] 부장님의 시계를 세계 어디서든 동기화하는 명령어입니다.
    수동으로 선택하거나, 핸드폰의 위치(GPS) 정보를 쏘아 자동으로 맞출 수 있습니다.
    """
    if str(update.message.chat_id) != ALLOWED_CHAT_ID: return
    
    from config import USER_TIMEZONE
    import pytz
    
    try:
        tz = pytz.timezone(USER_TIMEZONE)
        now = datetime.datetime.now(tz)
    except Exception:
        now = datetime.datetime.now()
        
    msg = (
        f"⏰ <b>피아니 통합 시계 관리 시스템</b>\n\n"
        f"현재 부장님의 시계는 <b>[{USER_TIMEZONE}]</b> 기준입니다.\n"
        f"📅 <b>현지 시간:</b> {now.strftime('%Y-%m-%d %H:%M:%S')}\n\n"
        f"아래 버튼을 눌러 피아니의 시계를 바꾸실 수 있습니다. 👇"
    )
    
    # 1. 수동 선택 (인라인 버튼)
    inline_keyboard = [
        [
            InlineKeyboardButton("🇻🇳 베트남 (Ho Chi Minh)", callback_data="tz_Asia/Ho_Chi_Minh"),
            InlineKeyboardButton("🇰🇷 한국 (Seoul)", callback_data="tz_Asia/Seoul")
        ]
    ]
    inline_markup = InlineKeyboardMarkup(inline_keyboard)
    
    # 2. 위치 자동 인식 (리플라이 키보드 - 위치 전송 요청)
    # 한 번만 쓰고 사라지게(one_time_keyboard) 설정합니다.
    location_keyboard = [[KeyboardButton("📍 현재 내 위치 전송 (GPS)", request_location=True)]]
    location_markup = ReplyKeyboardMarkup(location_keyboard, resize_keyboard=True, one_time_keyboard=True)
    
    await update.message.reply_text(msg, parse_mode="HTML", reply_markup=location_markup)
    await update.message.reply_text("수동으로 선택하시려면 아래 버튼을 눌러주세요:", reply_markup=inline_markup)

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
            stdout = result.stdout.strip()
            
            # [V12.16] 지능형 조건부 업데이트: 이미 최신 상태라면 재부팅 없이 즉시 보고 종료
            if "Already up to date" in stdout:
                await update.message.reply_text(
                    "✅ <b>이미 최신 상태입니다!</b>\n\n현재 피아니의 뇌(코드)는 가장 똑똑한 최신 버전입니다. 별도의 재부팅 없이 그대로 업무를 계속하겠습니다.\n\n🫡 부장님의 소중한 시간을 아꼈습니다!",
                    parse_mode="HTML"
                )
                return

            # 새로운 패치가 있는 경우에만 아래의 '재부팅' 과정을 진행합니다.
            rev_result = await asyncio.to_thread(
                subprocess.run,
                ['git', 'rev-parse', '--short', 'HEAD'],
                capture_output=True,
                text=True
            )
            short_id = rev_result.stdout.strip() if rev_result.returncode == 0 else "N/A"

            await update.message.reply_text(
                f"✅ <b>업데이트 성공!</b>\n"
                f"새로운 패치가 성공적으로 장착되었습니다.\n\n"
                f"📌 <b>버전 정보:</b> <code>{short_id}</code>\n"
                f"🤖 3초 뒤, 피아니가 스스로 재부팅하여 새로운 패치를 적용합니다. 잠시 후 뵙겠습니다!",
                parse_mode="HTML"
            )
            # 2. 메세지를 무사히 보내고, 3초 뒤에 자기 자신을 파이썬 명령어로 100% 껐다 켭니다(Restart).
            await asyncio.sleep(3)
            # os.execl 은 현재 실행 중인 파이썬 프로세스를 '새 파이썬 프로세스'로 갈아 끼워버리는 완벽한 재부팅 기술입니다.
            os.execl(sys.executable, sys.executable, *sys.argv)
        else:
            await update.message.reply_text(f"❌ 다운로드 실패! 코드가 꼬였을 수 있습니다.\n\n[에러 내용]\n{result.stderr}")
            
    except Exception as e:
        logger.error(f"업데이트 중 알 수 없는 오류 발생: {e}")
        await update.message.reply_text(f"🚨 업데이트 중 오류가 발생했습니다: {e}")

async def _process_ai_tags(ai_reply: str, update: Update, context: ContextTypes.DEFAULT_TYPE, replied_text: str = None) -> str:
    """
    [V12.16] AI 답변 속에 숨겨진 명령 태그와 '오답 원문'을 결합하여 처리합니다.
    """
    # 1. 오답 노트 학습 태그 (다중 처리 지원)
    learn_matches = list(re.finditer(r'\[\[LEARN\]\](.*?)\[\[/LEARN\]\]', ai_reply, re.DOTALL))
    if learn_matches:
        from feedback_manager import add_correction
        for match in learn_matches:
            rule_text = match.group(1).strip()
            # [V12.16] 이제 '오답 원문(replied_text)'을 함께 넘겨서 세트로 박제합니다!
            add_correction(rule_text, replied_text)
        ai_reply = re.sub(r'\[\[LEARN\]\].*?\[\[/LEARN\]\]', '', ai_reply, flags=re.DOTALL).strip()
        ai_reply += f"\n\n*(✅ 비서가 방금 지적하신 {len(learn_matches)}건의 내용을 [오답 사례 세트]로 학습했습니다!)*"

    # 2. 메모(수첩) 저장 태그 (다중 처리 지원)
    memo_matches = list(re.finditer(r'\[\[SAVE_MEMO\]\](.*?)\[\[/SAVE_MEMO\]\]', ai_reply, re.DOTALL))
    for match in memo_matches:
        from memo_manager import save_memo
        save_memo(match.group(1).strip())
    if memo_matches:
        ai_reply = re.sub(r'\[\[SAVE_MEMO\]\].*?\[\[/SAVE_MEMO\]\]', '', ai_reply, flags=re.DOTALL).strip()

    # 3. 메모 삭제 태그 (다중 처리 지원 - 부장님 버그 리포트 해결)
    del_matches = list(re.finditer(r'\[\[DELETE_MEMO\]\](.*?)\[\[/DELETE_MEMO\]\]', ai_reply, re.DOTALL))
    for match in del_matches:
        try:
            target_id = int(match.group(1).strip())
            from memo_manager import delete_memo
            delete_memo(target_id)
        except ValueError: pass
    if del_matches:
        ai_reply = re.sub(r'\[\[DELETE_MEMO\]\].*?\[\[/DELETE_MEMO\]\]', '', ai_reply, flags=re.DOTALL).strip()

    # 4. 메모 업데이트 태그 (다중 처리 지원)
    upd_matches = list(re.finditer(r'\[\[UPDATE_MEMO\]\](.*?)\[\[/UPDATE_MEMO\]\]', ai_reply, re.DOTALL))
    for match in upd_matches:
        payload = match.group(1).strip()
        if "|" in payload:
            try:
                parts = payload.split('|', 1)
                target_id = int(parts[0].strip())
                new_content = parts[1].strip()
                from memo_manager import update_memo
                update_memo(target_id, new_content)
            except ValueError: pass
    if upd_matches:
        ai_reply = re.sub(r'\[\[UPDATE_MEMO\]\].*?\[\[/UPDATE_MEMO\]\]', '', ai_reply, flags=re.DOTALL).strip()

    # 5. 온디맨드 보고서 생성 태그 (이 항목은 보통 1개씩 처리되지만 일관성을 유지)
    daily_report_match = re.search(r"\[\[GENERATE_DAILY_REPORT\]\]\s*(.*?)\s*\[\[/GENERATE_DAILY_REPORT\]\]", ai_reply)
    if daily_report_match:
        from report_manager import update_daily_report
        target_date = daily_report_match.group(1).strip()
        logger.info(f"온디맨드 일일 보고서 생성 시작: {target_date}")
        report_data = await asyncio.to_thread(update_daily_report, target_date)
        
        if report_data:
            summary_msg = f"✅ <b>{target_date} 일일 업무 보고서 생성을 완료했습니다!</b>\n\n"
            
            # [V11.8] 신규 고객사 중심 구조 반영 (가시성 극대화)
            client_reports = report_data.get("client_reports", [])
            if client_reports:
                for report in client_reports:
                    summaries = [s for s in report.get("summaries", []) if s.strip()]
                    if not summaries: continue
                    
                    summary_msg += f"🏢 <b>{escape_for_tg(report.get('client', '기타'))}</b>\n"
                    for item in summaries:
                        summary_msg += f"- {escape_for_tg(item)}\n"
                    summary_msg += "\n" # 고객사 간 여백
            else:
                # 구형 데이터 호환
                for topic in report_data.get("topics", []):
                    items = [i for i in topic.get("items", []) if i.strip()]
                    if not items: continue
                    
                    summary_msg += f"📌 <b>{topic.get('category', '분류')}</b>\n"
                    for item in items:
                        summary_msg += f"- {escape_for_tg(item)}\n"
                    summary_msg += "\n"
            
            await update.message.reply_text(summary_msg, parse_mode="HTML")
        else:
            await update.message.reply_text(f"⚠️ {target_date}의 데이터가 없어 보고서를 생성하지 못했습니다.")
        
        ai_reply = re.sub(r"\[\[GENERATE_DAILY_REPORT\]\].*?\[\[/GENERATE_DAILY_REPORT\]\]", "", ai_reply, flags=re.DOTALL).strip()

    # 6. 온디맨드 주간 보고서 생성 태그
    if "[[GENERATE_WEEKLY_REPORT]]" in ai_reply:
        from report_manager import generate_weekly_summary
        logger.info("온디맨드 주간 보고서 생성 시작")
        report_data = await asyncio.to_thread(generate_weekly_summary)
        
        if report_data:
            summary_msg = f"✅ <b>금주 주간 업무 보고서 작성을 완료했습니다!</b>\n\n"
            summary_msg += f"📊 <b>주간 전술적 분석:</b>\n{escape_for_tg(report_data.get('주간 전술적 분석', '분석 완료'))}\n\n"
            
            achievements = report_data.get("key_achievements", [])
            if achievements:
                summary_msg += "🏆 <b>이번 주 핵심 추진 성과:</b>\n"
                for item in achievements:
                    summary_msg += f"- {escape_for_tg(item)}\n"
                summary_msg += "\n"
            

            summary_msg += f"\n부장님의 전략적 의사결정을 돕기 위해 최선을 다해 분석했습니다. 수고하셨습니다! 👍"
            await update.message.reply_text(summary_msg, parse_mode="HTML")
        else:
            await update.message.reply_text("⚠️ 주간 보고서 생성을 위한 데이터가 부족합니다.")
        ai_reply = re.sub(r"\[\[GENERATE_WEEKLY_REPORT\]\].*?\[\[/GENERATE_WEEKLY_REPORT\]\]", "", ai_reply, flags=re.DOTALL).strip()

    return ai_reply

async def handle_normal_chat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    [V11.5] 명령 태그 처리기 분리로 슬림해진 대화형 비서 기능
    """
    if str(update.message.chat_id) != ALLOWED_CHAT_ID: return

    user_text = update.message.text
    
    # [V12.0] 하단 고정 메뉴 감지
    if user_text == "❓ 도움말":
        # 도움말은 기계적인 나열이므로 0.1초 만에 파이썬이 즉각 응답합니다.
        await handle_help_command(update, context)
        return
    
    elif user_text == "🔄 업데이트":
        # 업데이트 버튼 클릭 시 즉시 시스템 패치 핸들러를 호출합니다.
        await handle_update_command(update, context)
        return

    # [중요] '📝 메모보기' 버튼은 별도로 가로채지 않습니다. 
    # 부장님의 지시대로 피아니(AI)가 직접 뇌를 써서 미완료 업무만 브리핑하도록 대화 흐름을 유지합니다.

    # 1. 사용자 말씀 기록
    try:
        from chat_manager import save_chat_log
        save_chat_log(role='user', content=user_text)
    except Exception: pass
    
    # 답장(Reply) 맥락 파악
    replied_text = None
    if update.message.reply_to_message and update.message.reply_to_message.text:
        replied_text = update.message.reply_to_message.text
    
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action='typing')
    
    try:
        # 2. AI 답변 생성
        from ai_processor import chat_with_secretary
        ai_reply = await asyncio.to_thread(chat_with_secretary, user_text, replied_text)
        
        # 3. [V12.16] AI 답변 내의 명령 태그들을 처리할 때 '오답 맥락(replied_text)'을 함께 태웁니다.
        ai_reply = await _process_ai_tags(ai_reply, update, context, replied_text)

        # 4. 피아니의 답변 기록
        try:
            from chat_manager import save_chat_log
            save_chat_log(role='assistant', content=ai_reply)
        except Exception: pass

        # 5. 최종 답변 전송
        await update.message.reply_text(ai_reply)
        
    except Exception as e:
        logger.error(f"대화 처리 중 오류: {e}")
        await update.message.reply_text("🚨 앗, 부장님! 방금 머리가 좀 아파서 말씀을 제대로 못 들었습니다. 다시 말씀해 주시겠어요?")

def setup_telegram_handlers(application: Application):
    # 명령을 대기하는 두뇌 회로(수신기)에 '/status', '/note', '/update' 옵션을 박아 넣습니다.
    application.add_handler(CommandHandler("status", command_status))
    application.add_handler(CommandHandler("note", handle_memo_command))
    application.add_handler(CommandHandler("update", handle_update_command))
    application.add_handler(CommandHandler("notelist", handle_export_notes))
    
    # [V7.0] 시간 시계 관리 명령어 (메뉴 호출)
    application.add_handler(CommandHandler("time", handle_time_command))
    application.add_handler(CommandHandler("timeupdate", handle_time_command))

    # [V7.0] 스마트폰 GPS 위치 공유 수신기
    application.add_handler(MessageHandler(filters.LOCATION, handle_location_update))
    
    # 인라인 버튼(문서 저장 등) 콜백 처리를 위한 리스너입니다.
    application.add_handler(CallbackQueryHandler(handle_button_callback))
    
    # [V3.0] 일반적인 텍스트 대화(명령어 제외)를 감지하는 수신기입니다.
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_normal_chat))
    
    logger.info("텔레그램 제어 수신기('/status', '/메모' 등)와 일상 대화 수신기가 메인 서버에 장착 완료되었습니다.")
