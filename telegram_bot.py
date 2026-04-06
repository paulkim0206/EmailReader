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

# [V17.0] 베트남 뉴스 RSS 전용 알림 및 요약 핸들러 (URL 매핑용)
RSS_URL_MAP = {}

async def send_rss_alert(application, item):
    """베트남 뉴스 속보 알림을 전송합니다."""
    import hashlib
    from config import TELEGRAM_CHAT_ID
    
    url = item.get('link', '')
    url_hash = hashlib.md5(url.encode()).hexdigest()[:10]
    RSS_URL_MAP[url_hash] = url
    
    from ai_processor import translate_news_title
    
    title_vi = item.get('title', '제목 없음')
    # [V17.3] 제목 실시간 한국어 번역 실행
    title_ko = await asyncio.to_thread(translate_news_title, title_vi)
    
    msg = (
        f"🇻🇳 <b>[베트남 뉴스 속보]</b>\n\n"
        f"🇰🇷 <b>{escape_for_tg(title_ko)}</b>\n"
        f"🇻🇳 <i>{escape_for_tg(title_vi)}</i>\n\n"
        f"<i>({item.get('pub_date', '')})</i>"
    )
    
    # [📰 한글 요약] 버튼 부착
    keyboard = [[InlineKeyboardButton("📰 한글 요약 보기", callback_data=f"rss_sum_{url_hash}")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    try:
        await application.bot.send_message(
            chat_id=str(TELEGRAM_CHAT_ID),
            text=msg,
            parse_mode="HTML",
            reply_markup=reply_markup
        )
        logger.info(f"뉴스 속보 알림 전송 완료: {title}")
    except Exception as e:
        logger.error(f"뉴스 알림 전송 실패: {e}")

async def show_memo_interface(update: Update, context: ContextTypes.DEFAULT_TYPE, is_edit: bool = False, query=None, view_mode="main"):
    """[V16.0] 파이썬으로 수첩 내용을 빠르게 불러와 인라인 버튼 형태로 구성"""
    from memo_manager import get_active_memos_list
    memos = get_active_memos_list()
    
    keyboard = []
    if not memos:
        msg = "📝 <b>현재 활성화된 메모가 없습니다! 수첩이 아주 깨끗합니다 ✨</b>"
    else:
        msg = "📝 <b>[부장님의 미완료 수첩 목록]</b>\n\n"
        for idx, memo in enumerate(memos, 1):
            memo_id = memo.get('id', '?')
            safe_content = escape_for_tg(memo['content'])
            msg += f"🔸 <b>{idx}. (ID: {memo_id}번)</b>\n{safe_content}\n\n"
            
    if view_mode == "main":
        if memos:
            # [V16.9] 부장님 지시: [추가], [수정], [완료] 순서로 심플하게 배치
            keyboard.append([
                InlineKeyboardButton("➕ 추가", callback_data="memo_add"),
                InlineKeyboardButton("✏️ 수정", callback_data="memo_edit_menu"),
                InlineKeyboardButton("✅ 완료", callback_data="memo_del_menu")
            ])
        else:
            keyboard.append([InlineKeyboardButton("➕ 추가", callback_data="memo_add")])
            
    elif view_mode == "delete_menu":
        msg += "\n🗑 <b>완료 처리할(지울) 번호를 선택하세요.</b>"
        row = []
        for memo in memos:
            memo_id = memo.get('id', '?')
            row.append(InlineKeyboardButton(f"[{memo_id}]", callback_data=f"memo_del_{memo_id}"))
            if len(row) >= 5:
                keyboard.append(row)
                row = []
        if row:
            keyboard.append(row)
        keyboard.append([InlineKeyboardButton("🔙 뒤로 가기", callback_data="memo_main")])

    elif view_mode == "edit_menu":
        msg += "\n✏️ <b>수정할 번호를 선택하세요.</b>"
        row = []
        for memo in memos:
            memo_id = memo.get('id', '?')
            row.append(InlineKeyboardButton(f"[{memo_id}]", callback_data=f"memo_edit_{memo_id}"))
            if len(row) >= 5:
                keyboard.append(row)
                row = []
        if row:
            keyboard.append(row)
        keyboard.append([InlineKeyboardButton("🔙 뒤로 가기", callback_data="memo_main")])
            
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    if is_edit and query:
        try:
            await query.edit_message_text(text=msg, parse_mode="HTML", reply_markup=reply_markup)
        except Exception:
            pass
    else:
        await update.message.reply_text(text=msg, parse_mode="HTML", reply_markup=reply_markup)

async def handle_button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    사용자가 텔레그램 대화방에서 [마크다운 문서로 저장하기] 버튼을 띡! 눌렀을 때만 작동하는 '동작 감지기'입니다.
    사용자의 명백한 클릭 명령 없이는 어떤 문서 파일도 제멋대로 생성하지 못하도록 통과 지점을 만든 방어벽입니다.
    """
    query = update.callback_query

    # 버튼 뒤에 숨겨두었던 암호문 (예: 'save_10번편지')을 가져옵니다.

    # 버튼 뒤에 숨겨두었던 암호문 (예: 'save_10번편지')을 가져옵니다.
    data = query.data
    
    # [분기 3] 보안 통과 검사: 암호문에 맞게 분기 처리합니다.
    if data.startswith("save_"):
        # [V11.10] 부장님의 지시로 원본 저장 기능은 폐지되었습니다. 
        # (혹시라도 이전 메시지의 버튼을 누를 경우를 대비하여 안내를 보냅니다.)
        await query.answer(text="⚠️ 해당 기능(메일 원본 저장)은 부장님 지시로 폐지되었습니다.", show_alert=True)
        return

    # [V17.0] 베트남 뉴스 기사 요약 처리
    elif data.startswith("rss_sum_"):
        url_hash = data.replace("rss_sum_", "")
        url = RSS_URL_MAP.get(url_hash)
        
        if not url:
            await query.answer("⚠️ 세션이 만료되어 기사 링크를 찾을 수 없거나 이미 분석되었습니다.", show_alert=True)
            return
            
        await query.answer("⏳ 베트남 뉴스를 분석하여 한국어로 요약 중입니다...")
        
        # '요약 중...' 상태를 부장님께 알림
        original_text = query.message.text
        # [V12.16] 텔레그램 메시지 편집 (상태 업뎃)
        try:
            await query.edit_message_text(
                f"{original_text}\n\n"
                f"━━━━━━━━━━━━━━━\n"
                f"🔄 <b>피아니가 기사를 정독하고 있습니다...</b> (10~20초 소요)",
                parse_mode="HTML"
            )
        except Exception: pass
        
        try:
            from ai_processor import summarize_news_article
            # AI 요약 실행 (실제 웹 스크래핑 포함)
            summary = await asyncio.to_thread(summarize_news_article, url)
            
            # 요약 결과로 메시지 업데이트
            await query.edit_message_text(
                f"{original_text}\n\n"
                f"━━━━━━━━━━━━━━━\n"
                f"📝 <b>한글 요약 보고</b>\n\n"
                f"{summary}\n\n"
                f"🔗 <a href='{url}'>[기사 원문 보기]</a>",
                parse_mode="HTML",
                disable_web_page_preview=True
            )
        except Exception as e:
            logger.error(f"뉴스 요약 처리 중 오류: {e}")
            await context.bot.send_message(
                chat_id=query.message.chat_id,
                text=f"❌ <b>뉴스 요약 실패:</b> 원문을 가져와 분석하는 중 오류가 발생했습니다.\n{url}"
            )
        return

    # [V16.9] 수첩(노트) 인라인 인터페이스 핸들러
    elif data.startswith("memo_"):
        if data == "memo_main":
            await show_memo_interface(update, context, is_edit=True, query=query, view_mode="main")
        elif data == "memo_del_menu":
            await show_memo_interface(update, context, is_edit=True, query=query, view_mode="delete_menu")
        elif data == "memo_edit_menu":
            await show_memo_interface(update, context, is_edit=True, query=query, view_mode="edit_menu")
        
        elif data.startswith("memo_del_"):
            memo_id = int(data.split("_")[2])
            from memo_manager import delete_memo
            if delete_memo(memo_id):
                await query.answer(text=f"✅ {memo_id}번 노트를 완료 처리했습니다!", show_alert=False)
                await show_memo_interface(update, context, is_edit=True, query=query, view_mode="delete_menu")
            else:
                await query.answer(text="🚨 완료 처리 실패!", show_alert=True)
                
        elif data.startswith("memo_edit_"):
            memo_id = int(data.split("_")[2])
            from telegram import ForceReply
            # [부장님 취향저격] 상단 말풍선 알림 발송
            await query.answer(text=f"✏️ {memo_id}번 노트 수정을 시작합니다.")
            await context.bot.send_message(
                chat_id=query.message.chat_id,
                text=f"📝 <b>[{memo_id}번 수첩 수정]</b>\n수정할 새로운 내용을 아래에 적어주세요. (이 메시지에 답장)",
                parse_mode="HTML",
                reply_markup=ForceReply(selective=True)
            )
            
        elif data == "memo_add":
            from telegram import ForceReply
            await query.answer()
            await context.bot.send_message(
                chat_id=query.message.chat_id,
                text="📝 새로 추가할 노트 내용을 아래에 적어주세요. (이 메시지에 답장)",
                reply_markup=ForceReply(selective=True)
            )
        return

    # [새로운 분기 1] 부장님의 "이건 보고서에 넣어!" 명령 (핀 버튼)
    elif data.startswith("rpt_"):
        uid = data.split("_")[1]
        
        # [V12.20] 체감 속도 최적화: 클릭 즉시 버튼을 가려 부장님께 즉각적인 반응을 보여줍니다.
        try: await query.edit_message_reply_markup(reply_markup=None)
        except Exception: pass

        from thread_manager import toggle_report_pin_by_uid
        
        # [V15.0 Flat DB] 단 한 줄로 초고속 핀셋 토글 처리
        if toggle_report_pin_by_uid(uid, status=True):
            try:
                await query.answer(text="✅ 일일보고서 대상으로 등록 완료! 📋", show_alert=False)
            except Exception: pass
        else:
            await query.answer()
            await query.message.reply_text("⚠️ 장부에서 찾을 수 없습니다. (서버 초기화 등)", parse_mode="HTML")
        return

    # [새로운 분기 3] 사용자가 AI 학습(👎) 단추를 눌렀을 때!!
    elif data.startswith("learn_"):
        uid = data.split("_")[1]

        # [V12.20] 체감 속도 최적화: 클릭 즉시 버튼을 가려 부장님께 즉각적인 반응을 보여줍니다.
        try: await query.edit_message_reply_markup(reply_markup=None)
        except Exception: pass

        from thread_manager import find_entry_by_uid
        from feedback_manager import add_learning_preference
        from ai_processor import extract_skip_rule_ai
        
        # 0. [V12.19] 즉시 응답: 텔레그램 버튼의 '글썽임(로딩)'을 0.1초 만에 멈추게 합니다.
        await query.answer(text="⏳ 부장님의 의도를 분석하여 학습 중입니다... 잠시만 기다려 주세요! 🫡")

        # 1. 장부 또는 캐시에서 제목, 본문, 요약을 확보합니다.
        info = find_entry_by_uid(uid)
        subject = None
        summary = None
        body = ""
        
        cache_data = temp_mail_cache.get(uid)
        if cache_data:
            subject = cache_data["mail"].get("subject")
            body = cache_data["mail"].get("body", "")
            
        if info:
            subject = subject or info.get("thread_key")
            summary = info.get("summary")
        
        if not body and uid:
            # 캐시에 없으면 서버에서 가져옵니다 (V12.12 실시간 복구)
            from mail_parser import fetch_parsed_mail
            mail_data = await asyncio.to_thread(fetch_parsed_mail, uid)
            if mail_data:
                subject = subject or mail_data.get("subject")
                body = mail_data.get("body", "")

        if subject:
            # [V12.19] AI를 사용하여 왜 제외했는지 '의도(Rule)'를 추출합니다.
            reason = await asyncio.to_thread(extract_skip_rule_ai, subject, body)
            
            success, msg = add_learning_preference(subject, summary or "요약 없음", reason)
            
            if success:
                await context.bot.send_message(
                    chat_id=query.message.chat_id,
                    text=(
                        f"🧠 <b>요약 제외 및 규칙 학습 완료!</b>\n\n"
                        f"📝 <b>파악된 스킵 규칙:</b>\n<code>{escape_for_tg(reason)}</code>\n\n"
                        f"앞으로 유사한 성격의 메일은 지능적으로 스킵하겠습니다. ✨"
                    ),
                    parse_mode="HTML"
                )
            else:
                await context.bot.send_message(chat_id=query.message.chat_id, text=f"ℹ️ {msg}")
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
            # [V12.16] 재분석 시작됨을 알림
            await query.answer(text="⏳ 재분석을 시작합니다...")
            from ai_processor import process_email_with_ai
            
            # [V14.0/V15.0 Stateless] 더 이상 과거 장부(history)를 억지로 주입하지 않습니다.
            new_ai_result = await asyncio.to_thread(process_email_with_ai, mail_data, [], force_summarize=True)
            
            # 분석 완료 시 알림 및 장부 기록
            from thread_manager import save_summary_entry
            await send_email_alert(context.application, mail_data, new_ai_result, {}, mail_data.get('subject', ''))
            
            # [V15.0 Flat DB] 오래된 부장님 버튼 버그(save_thread_entry) 해결 완료!
            save_summary_entry(uid, mail_data.get('subject', ''), new_ai_result.get('summary', ''), None, new_ai_result.get('client_name'))
            
            try: await query.edit_message_reply_markup(reply_markup=None)
            except Exception: pass
        else:
            await query.answer(text="🚨 메일 데이터를 가져오지 못했습니다.")
            await context.bot.send_message(chat_id=query.message.chat_id, text="🚨 메일 서버에서 데이터를 가져오는 데 실패했습니다.")
        return

    # [V7.0 새로운 분기] 사용자가 시간대(tz_) 관련 버튼을 눌렀을 때!!
    elif data.startswith("tz_"):
        # [V12.16] GPS 시작 버튼 클릭 시: 하단 키보드에 위치 전송 버튼을 '띡!' 하고 띄워줍니다.
        if data == "tz_gps_start":
            location_keyboard = [[KeyboardButton("📍 현재 내 위치 전송 (GPS)", request_location=True)]]
            location_markup = ReplyKeyboardMarkup(location_keyboard, resize_keyboard=True, one_time_keyboard=True)
            
            # [중요] query.answer는 여기서 단 한 번만 호출해야 충돌이 없습니다.
            await query.answer(text="📍 하단 키보드에 생성된 [위치 전송] 버튼을 눌러주세요!", show_alert=True)
            # [V12.16] 버튼 클릭 후 즉시 상단 인라인 메뉴를 제거합니다.
            await query.edit_message_reply_markup(reply_markup=None)
            
            await context.bot.send_message(
                chat_id=query.message.chat_id,
                text="부장님, 방금 하단 타이핑 영역에 <b>[📍 위치 전송]</b> 버튼을 활성화했습니다. 해당 버튼을 누르시면 자동으로 분석을 시작합니다. 🫡",
                parse_mode="HTML",
                reply_markup=location_markup
            )
            return

        # 일반적인 국가 수동 선택 처리
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
            
            # 국가 선택 시에도 답변을 한 번만 호출
            await query.answer(text="✅ 시계 설정이 완료되었습니다.")
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
        
        ai_response = await asyncio.to_thread(chat_with_secretary, prompt, None, False)
        
        # [V12.16] 긴급 방어: AI 응답이 없거나(None) 문자열이 아닐 경우를 대비합니다.
        if not ai_response or not isinstance(ai_response, str):
            logger.error(f"GPS 분석 실패: AI가 유효하지 않은 응답을 보냈습니다. (응답값: {ai_response})")
            await update.message.reply_text("🚨 AI가 좌표를 해석하지 못했습니다. 잠시 후 다시 시도해 주세요.")
            return

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
    keyboard = [['❓ 도움말', '📝 노트보기', '🔄 업데이트']]
    # resize_keyboard=True 로 크기 최적화, is_persistent=True로 항상 고정 유지
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True, is_persistent=True)
    
    await update.message.reply_text(
        "✅ 🤖 <b>비서 피아니가 정상적으로 살아있으며, 부장님의 모든 명령을 대기 중입니다!</b>\n\n"
        "하단의 버튼을 누르시면 도움말을 보거나 노트 현황을 즉시 확인하실 수 있습니다. 👇",
        reply_markup=reply_markup,
        parse_mode="HTML"
    )

async def handle_time_info_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    [V16.4] 부장님의 요청으로 분리된 명령어. 순수하게 현재 맞춰진 시간만 문자로 알려줍니다.
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
        f"⏰ <b>현재 시각 안내</b>\n\n"
        f"현재 설정된 지역 <b>[{USER_TIMEZONE}]</b> 기준으로\n"
        f"📅 <b>{now.strftime('%Y년 %m월 %d일 (%a)')}</b>\n"
        f"⏱ <b>{now.strftime('%p %I시 %M분 %S초').replace('AM', '오전').replace('PM', '오후')}</b>\n\n"
        f"*(시간 설정을 변경하시려면 /timeupdate 명령어를 이용해 주세요)*"
    )
    await update.message.reply_text(msg, parse_mode="HTML")

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
        f"현재 부장님의 시계 기준: <b>[{USER_TIMEZONE}]</b>\n"
        f"📅 <b>현지 시각:</b> {now.strftime('%A, %H:%M:%S')}\n\n"
        f"원하시는 시간대를 선택하거나 GPS로 자동 맞춤을 시작하십시오. 👇"
    )
    
    # [V12.16] 부장님의 정석: 행 1(국가 선택), 행 2(GPS 자동 인식) 통합 배치
    inline_keyboard = [
        [
            InlineKeyboardButton("🇻🇳 베트남 (Ho Chi Minh)", callback_data="tz_Asia/Ho_Chi_Minh"),
            InlineKeyboardButton("🇰🇷 한국 (Seoul)", callback_data="tz_Asia/Seoul")
        ],
        [
            InlineKeyboardButton("📍 GPS로 자동 시계 맞춤 시작", callback_data="tz_gps_start")
        ]
    ]
    inline_markup = InlineKeyboardMarkup(inline_keyboard)
    
    await update.message.reply_text(msg, parse_mode="HTML", reply_markup=inline_markup)

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

async def handle_export_backup_notes(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    [V12.29] 부장님의 명령('/notebackup')에 의해 백업된 전체 메모 장부를 배달합니다.
    데이터가 길 수 있으므로 깔끔하게 파일(Document) 형태로 전송합니다.
    """
    if str(update.message.chat_id) != ALLOWED_CHAT_ID: return
    
    from memo_manager import get_backup_memos_text
    import tempfile
    
    backup_text = get_backup_memos_text()
    
    try:
        with tempfile.NamedTemporaryFile(mode='w+', delete=False, suffix='.txt', encoding='utf-8') as tmp:
            tmp.write(backup_text)
            tmp_path = tmp.name
            
        with open(tmp_path, 'rb') as f:
            await context.bot.send_document(
                chat_id=update.message.chat_id,
                document=f,
                filename=f"수첩_전체백업_{datetime.datetime.now().strftime('%Y%m%d')}.txt",
                caption="🗄️ 부장님! 과거에 완료되어 백업 보관소로 이사 간 전체 메모 원본을 배달해 드립니다!"
            )
        import os
        os.unlink(tmp_path)
    except Exception as e:
        await update.message.reply_text(f"🚨 백업 수첩 파일 배달 중 오류: {e}")

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
        from telegram import ForceReply
        await update.message.reply_text(
            "📝 새로 추가할 메모 내용을 하단에 적어주세요. (이 메시지에 대한 답장 형태)",
            reply_markup=ForceReply(selective=True)
        )
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
        await update.message.reply_text(f"🚨 앗! 하드디스크에 메모를 찍으려고 했는데 오류가 발생했습니다.\n[원인]: {e}")

async def handle_memo_del_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    [V16.1] /notedel [숫자] 명령어로 바로 특정 메모를 지우는 기능
    """
    chat_id = str(update.message.chat_id)
    if chat_id != ALLOWED_CHAT_ID:
        return

    text = update.message.text
    match = re.search(r'^/notedel\s+(\d+)', text, flags=re.IGNORECASE)
    
    if not match:
        await update.message.reply_text("🤔 부장님! 완료할(지울) 메모의 번호(ID)를 같이 적어주세요!\n👉 예시: /notedel 3")
        return

    memo_id = int(match.group(1))
    from memo_manager import delete_memo
    if delete_memo(memo_id):
        await update.message.reply_text(f"✅ 수첩에서 [ID: {memo_id}번] 메모를 지웠습니다(완료 처리)!")
    else:
        await update.message.reply_text(f"🚨 앗! {memo_id}번 메모를 찾을 수 없습니다. (이미 존재하지 않음)")

async def handle_notelist_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """[V16.2] /notelist 명령어로 노트(메모) 인라인 메뉴를 바로 호출합니다."""
    if str(update.message.chat_id) != ALLOWED_CHAT_ID: return
    await show_memo_interface(update, context)

async def handle_restart_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    [V12.20] 사용자 요청에 의한 즉시 재부팅 명령어입니다.
    업데이트(git pull) 없이 현재 프로세스만 새로고침합니다.
    """
    chat_id = str(update.message.chat_id)
    if chat_id != ALLOWED_CHAT_ID:
        return

    await update.message.reply_text("🔄 <b>[시스템 재시작]</b> 부장님의 명령에 따라 피아니를 즉시 재부팅합니다. 3초만 기다려 주세요! 🫡", parse_mode="HTML")
    
    # [V12.20] 부장님이 직접 명령하신 것이므로 별도의 업데이트 없이 즉시 재부팅합니다.
    await asyncio.sleep(3)
    # os.execl 은 현재 실행 중인 파이썬 프로세스를 '새 파이썬 프로세스'로 갈아 끼워버리는 완벽한 재부팅 기술입니다.
    os.execl(sys.executable, sys.executable, *sys.argv)

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
                f"📌 <b>버전 정보:</b> <code>{short_id}</code>\n\n"
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
            week_label = report_data.get("week_label", "")
            client_summary = report_data.get("client_summary", {})
            total_items = report_data.get("total_items", 0)

            # 메시지 통합 전송 (텔레그램 4096자 제한 대응)
            current_msg = f"<b>주간 업무 종합 ({week_label})</b>\n고객 {len(client_summary)}사 / 총 {total_items}건\n\n"

            for client, items in sorted(client_summary.items()):
                client_block = f"<b>{escape_for_tg(client)}</b>\n"
                for item in items:
                    client_block += f"- {escape_for_tg(item)}\n"
                client_block += "\n"
                
                # 다음 고객 내용을 합쳤을 때 4000자가 넘는다면, 지금까지 모은 텍스트를 먼저 전송
                if len(current_msg) + len(client_block) > 4000:
                    if current_msg.strip():
                        await update.message.reply_text(current_msg, parse_mode="HTML")
                    current_msg = ""
                    
                    # 고객 한 곳의 내용만으로도 4000자가 넘는 초과 예외 상황 방어
                    if len(client_block) > 4000:
                        client_block = client_block[:4000] + "\n...(생략)\n\n"
                
                current_msg += client_block
                
            # 남은 텍스트가 있으면 마지막으로 전송
            if current_msg.strip():
                await update.message.reply_text(current_msg, parse_mode="HTML")
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
    
    # [V16.9] ForceReply 기반 노트 추가/수정 기능 (답장 감지)
    if update.message.reply_to_message:
        replied_msg = update.message.reply_to_message.text
        
        # 1. 노트 추가 처리
        if "새로 추가할 노트 내용" in replied_msg:
            from memo_manager import save_memo
            memo_content = user_text.strip()
            if save_memo(memo_content):
                await update.message.reply_text(f"✅ 성공적으로 새 노트가 수첩에 등록되었습니다!\n\n내용: {memo_content}")
                await show_memo_interface(update, context, view_mode="main") # 리스트 갱신 출력
            else:
                await update.message.reply_text("🚨 노트 저장 중 오류가 발생했습니다.")
            return
            
        # 2. 노트 수정 처리
        elif "수첩 수정" in replied_msg:
            # ID 추출 ( [12번 수첩 수정] 형태에서 12를 뽑아냄 )
            match = re.search(r'\[(\d+)번 수첩 수정\]', replied_msg)
            if match:
                memo_id = int(match.group(1))
                from memo_manager import update_memo
                new_content = user_text.strip()
                if update_memo(memo_id, new_content):
                    await update.message.reply_text(f"✅ {memo_id}번 노트가 성공적으로 수정되었습니다!\n\n변경내용: {new_content}")
                    await show_memo_interface(update, context, view_mode="main")
                else:
                    await update.message.reply_text(f"🚨 {memo_id}번 노트 수정에 실패했습니다.")
            return

    # [V12.0] 하단 고정 메뉴 감지
    if user_text == "❓ 도움말":
        # 도움말은 기계적인 나열이므로 0.1초 만에 파이썬이 즉각 응답합니다.
        await handle_help_command(update, context)
        return
    
    elif user_text == "🔄 업데이트":
        # 업데이트 버튼 클릭 시 즉시 시스템 패치 핸들러를 호출합니다.
        await handle_update_command(update, context)
        return

    # [V16.0] '📝 노트보기' 버튼 인라인 인터페이스로 가로채기
    elif user_text == "📝 노트보기":
        await show_memo_interface(update, context)
        return

    # [대수술] 이전의 '무조건적인 사용자 기록(save_chat_log)' 로직을 삭제했습니다.
    # 의도 파악이 끝난 후 GENERAL_CHAT 일때 한꺼번에 기록합니다.
    
    # 답장(Reply) 맥락 파악
    replied_text = None
    if update.message.reply_to_message and update.message.reply_to_message.text:
        replied_text = update.message.reply_to_message.text
    
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action='typing')
    
    try:
        # 2. AI 답변 생성 전 의도 파악 (Routing)
        from ai_processor import route_intent, chat_with_secretary
        
        # [혁신] 부장님의 말씀 의도를 먼저 파악합니다.
        intent = await asyncio.to_thread(route_intent, user_text)
        
        # [토큰 다이어트 기준 적용]
        use_memos = False
        use_history = True
        
        if intent in ["REPORT_WORK", "MAIL_WORK"]:
            use_history = False # 보고/메일 작업에도 일상 대화나 수첩이 불필요합니다.
        else: # GENERAL_CHAT
            use_history = True
            
        logger.info(f"선택된 모드: {intent} (기억력 적용: {use_history})")

        ai_reply = await asyncio.to_thread(
            chat_with_secretary, 
            user_message=user_text, 
            replied_text=replied_text, 
            include_history=use_history,
            intent=intent
        )

        
        # 3. [V12.16] AI 답변 내의 명령 태그들을 처리할 때 '오답 맥락(replied_text)'을 함께 태웁니다.
        ai_reply = await _process_ai_tags(ai_reply, update, context, replied_text)

        # 4. [신규 핵심 로직] 오직 GENERAL_CHAT일 때만 히스토리에 기록!!
        if intent == "GENERAL_CHAT":
            try:
                from chat_manager import save_chat_log
                # [버그 방지] 오류 메시지는 장부에 기록하지 않음. 기록되면 봇이 오류 문구를 학습/반복하는 문제가 생김.
                is_error_reply = ai_reply.startswith("🚨") or "안개가 낀 것처럼" in ai_reply or "머릿속에 기억들이 꼬여" in ai_reply or "머리가 좀 아파서" in ai_reply
                if not is_error_reply:
                    save_chat_log(role='user', content=user_text)
                    save_chat_log(role='assistant', content=ai_reply)
                    logger.info("✅ 순수 대화(GENERAL_CHAT)로 분류되어 장부에 영구 기록되었습니다.")
                else:
                    logger.warning("⚠️ 오류 메시지가 감지되어 장부 기록을 건너뜁니다. (오염 방지)")
            except Exception as e:
                logger.error(f"장부 저장 실패: {e}")
        else:
            logger.info(f"🚫 작업 모드({intent})로 분류되어 대화 이력 장부에 기록하지 않고 휘발시킵니다.")

        # 5. 최종 답변 전송
        await update.message.reply_text(ai_reply)
        
    except Exception as e:
        logger.error(f"대화 처리 중 오류: {e}")
        await update.message.reply_text("🚨 앗, 부장님! 방금 머리가 좀 아파서 말씀을 제대로 못 들었습니다. 다시 말씀해 주시겠어요?")


async def handle_memory_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    [V13.1] /memory : 대화 장부 현황 조회 + 초기화(삭제) 기능
    """
    if str(update.message.chat_id) != ALLOWED_CHAT_ID: return
    from chat_manager import get_chat_status
    status = get_chat_status()
    msg = (
        f"<b>대화 장부 현황</b>\n"
        f"- 기록 건수: {status['count']}건\n"
        f"- 파일 용량: {status['size_kb']} KB\n"
        f"- 최근 기록: {status['last_time']}"
    )
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("삭제 및 초기화", callback_data="memory_clear"),
         InlineKeyboardButton("취소", callback_data="memory_cancel")]
    ])
    await update.message.reply_text(msg, parse_mode="HTML", reply_markup=keyboard)

async def handle_memory_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    [V13.1] /memory 버튼 콜백 처리
    """
    query = update.callback_query
    await query.answer()
    if str(query.message.chat_id) != ALLOWED_CHAT_ID: return

    if query.data == "memory_clear":
        from chat_manager import clear_chat_history
        success = clear_chat_history()
        if success:
            await query.edit_message_text("대화 장부 초기화 및 캐시 리로드 완료. (0건)")
        else:
            await query.edit_message_text("초기화 중 오류가 발생했습니다.")
    elif query.data == "memory_cancel":
        await query.edit_message_text("취소되었습니다.")

async def handle_shutdown_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    [V17.2] 부장님의 긴급 명령에 의한 서버 즉시 종료 전용 명령어입니다.
    이 명령어가 실행되면 봇 서버 프로세스가 완전히 멈춥니다. (수동 재시작 필요)
    """
    if str(update.message.chat_id) != ALLOWED_CHAT_ID: return

    logger.warning("🚨 부장님의 명령으로 시스템 강제 종료 시퀀스를 시작합니다.")
    await update.message.reply_text(
        "🔌 <b>[시스템 긴급 셧다운]</b>\n\n"
        "부장님의 명령을 받들어 피아니가 잠시 휴식에 들어갑니다.\n"
        "서버가 즉시 종료되며, 수동으로 다시 켜기 전까지는 응답할 수 없습니다.\n"
        "🫡 업무 지시 감사했습니다! 푹 쉬십시오!",
        parse_mode="HTML"
    )
    
    # 텔레그램 메시지가 확실히 전송될 수 있도록 아주 짧게 대기 후 프로세스 강제 사살
    await asyncio.sleep(1)
    os._exit(0)

def setup_telegram_handlers(application: Application):
    # 명령을 대기하는 두뇌 회로(수신기)에 '/status', '/note', '/update' 옵션을 박아 넣습니다.
    application.add_handler(CommandHandler("status", command_status))
    application.add_handler(CommandHandler("help", handle_help_command)) # [V16.3] /help 명령어 누락 복구
    application.add_handler(CommandHandler("note", handle_memo_command))
    application.add_handler(CommandHandler("notedel", handle_memo_del_command))
    application.add_handler(CommandHandler("notebackup", handle_export_backup_notes))
    application.add_handler(CommandHandler("notelist", handle_notelist_command))
    application.add_handler(CommandHandler("update", handle_update_command))
    application.add_handler(CommandHandler("restart", handle_restart_command))
    application.add_handler(CommandHandler("memory", handle_memory_command))
    application.add_handler(CommandHandler("shutdown", handle_shutdown_command)) # [V17.2] 비상 종료
    application.add_handler(CallbackQueryHandler(handle_memory_callback, pattern="^memory_"))
    
    # [V7.0] 시간 시계 관리 명령어 (메뉴 호출 분리)
    application.add_handler(CommandHandler("time", handle_time_info_command))
    application.add_handler(CommandHandler("timeupdate", handle_time_command))

    # [V7.0] 스마트폰 GPS 위치 공유 수신기
    application.add_handler(MessageHandler(filters.LOCATION, handle_location_update))
    
    # 인라인 버튼(문서 저장 등) 콜백 처리를 위한 리스너입니다.
    application.add_handler(CallbackQueryHandler(handle_button_callback))
    
    # [V3.0] 일반적인 텍스트 대화(명령어 제외)를 감지하는 수신기입니다.
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_normal_chat))
    
    logger.info("텔레그램 제어 수신기('/status', '/메모' 등)와 일상 대화 수신기가 메인 서버에 장착 완료되었습니다.")
