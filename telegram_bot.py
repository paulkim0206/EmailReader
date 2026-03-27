import asyncio
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CallbackQueryHandler, ContextTypes
from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, logger
from local_storage import create_and_save_report

# 전 세계의 모든 사용자 중, 오직 '나(등록된 소유자)'에게만 알림을 보내고 명령을 받기 위한 검증용 정보입니다.
# 만약 누군가 내 비서 봇에 몰래 말을 걸어도 ID가 다르면 가차 없이 무시합니다. (보안 철저)
ALLOWED_CHAT_ID = str(TELEGRAM_CHAT_ID)

# 이메일 데이터와 AI가 고생해서 분석한 요약을 컴퓨터 기억장치에 임시로 '잠시' 넣어두는 상자입니다.
# 나중에 유저가 텔레그램 버튼으로 "이거 저장해줘!" 라고 할 때 여기서 꺼내다 씁니다.
# (컴퓨터를 끄면 상자가 비워지는 임시 보관소라 용량 걱정은 전혀 없습니다.)
temp_mail_cache = {}

import html

def escape_for_tg(text):
    """
    마크다운 대신 훨씬 튼튼한 HTML 방식을 쓰기 위해, 꺾쇠(<, >) 등만 살짝 보호합니다!
    이렇게 하면 AI가 쓴 내용이 절대 안 깨지고, 줄바꿈과 기호가 그대로 스마트폰에 예쁘게 나옵니다.
    """
    if not text:
        return ""
    return html.escape(str(text))

async def send_email_alert(application: Application, mail_data: dict, ai_result: dict):
    """
    새로운 이메일이 오고 AI가 분석을 마쳤을 때, 
    사용자의 텔레그램 방으로 '띠링!' 하고 예쁜 요약본을 배달해주는 우체부 로봇 함수입니다.
    """
    # 저장 버튼(Inline Keyboard)에 달아둘 이메일 전용 주민등록번호(UID)를 가져옵니다.
    uid = mail_data.get('uid', '알수없는번호')
    
    # 임시 상자에 원본 이메일 내용과 AI 분석 결과를 저장해 둡니다. 나중에 버튼이 눌렸을 때를 대비하는 겁니다.
    temp_mail_cache[uid] = {
        "mail": mail_data,
        "ai": ai_result
    }

    # 텔레그램 스마트폰 화면에 보일 예쁜 알림창의 내용을 빵빵하게 채워 넣습니다. (HTML 방식)
    message_text = (
        f"📧 <b>새로운 이메일 알림 보드</b>\n\n"
        f"🕒 <b>수신 일시:</b> {escape_for_tg(mail_data.get('date', ''))}\n"
        f"👤 <b>보낸 사람:</b> {escape_for_tg(mail_data.get('sender', ''))}\n"
        f"📝 <b>메일 제목:</b> {escape_for_tg(mail_data.get('subject', ''))}\n"
        f"🗂 <b>분류 결과:</b> {escape_for_tg(ai_result.get('category', ''))}\n\n"
        f"💡 <b>전체 흐름 요약 및 팁:</b>\n{escape_for_tg(ai_result.get('summary', ''))}\n\n"
        f"👨‍💼 <b>AI가 분석한 실무 조언:</b>\n{escape_for_tg(ai_result.get('advice', ''))}"
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
            
            # 여러 개의 쪼개진 편지가 왔을 때, 마지막 장 맨 아랫부분 바닥에만 [저장 버튼]을 딱 붙여줍니다.
            if i == len(message_chunks) - 1:
                # 사용자가 손가락으로 누르면 "save_<고유번호>" 라는 암호 신호를 봇에게 말없이 튕겨줍니다.
                keyboard = [
                    [InlineKeyboardButton("💾 이 내용 전체를 예쁜 워드 파일로 컴퓨터에 저장하기", callback_data=f"save_{uid}")]
                ]
                reply_markup = InlineKeyboardMarkup(keyboard)

            await application.bot.send_message(
                chat_id=ALLOWED_CHAT_ID,
                text=chunk,
                parse_mode="HTML",
                reply_markup=reply_markup
            )
            
        logger.info(f"텔레그램 비서가 새로운 소식(메일번호 {uid})을 성공적으로 사용자에게 전달했습니다!")
    except Exception as e:
        logger.error(f"텔레그램 전력망 장애로 소식 전달에 슬프게도 실패했습니다: {e}")

async def handle_button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    사용자가 텔레그램 대화방에서 [워드 파일로 저장하기] 버튼을 띡! 눌렀을 때만 작동하는 '동작 감지기'입니다.
    사용자의 명령(/save) 없이는 어떤 워드 파일도 제멋대로 생성하지 못하도록 통과 지점을 만든 방어벽입니다.
    """
    query = update.callback_query
    
    # 텔레그램 서버에 "주인님이 버튼 누르셨다! 화면에 모래시계 치워!" 라고 신호를 반환합니다.
    await query.answer()

    # 버튼 뒤에 숨겨두었던 암호문 (예: 'save_10번편지')을 가져옵니다.
    data = query.data
    
    # 보안 통과 검사: 암호문이 'save_'로 시작할 때만 동작합니다.
    if data.startswith("save_"):
        uid = data.split("_")[1]
        
        # 아까 준비해 둔 '임시 상자'에서 이 번호의 메일을 끄집어냅니다.
        cache_data = temp_mail_cache.get(uid)
        
        if cache_data:
            # 드디어 우리가 앞서 만든 4단계 모듈 '워드 자동 생성기'를 가동합니다!
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

def setup_telegram_handlers(application: Application):
    """
    텔레그램 봇의 인공지능 두뇌에 "만약 화면의 버튼이 눌리면 이렇게 대응해라~" 고 
    가이드라인(동작 감지기)을 등록시켜 주는 연결 장치입니다.
    """
    application.add_handler(CallbackQueryHandler(handle_button_callback))
