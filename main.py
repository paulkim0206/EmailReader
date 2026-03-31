import os
import asyncio
import datetime
import json
import pytz
from telegram.ext import Application
from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, logger, USER_TIMEZONE, REPORTS_DIR, BASE_DIR

# 앞서 우리가 정성껏 만든 주요 도구들을 하나의 커다란 공장 상자로 불러옵니다!
from mail_parser import fetch_unseen_emails, save_processed_uid
from ai_processor import process_email_with_ai
from telegram_bot import send_email_alert, send_skip_alert, setup_telegram_handlers, escape_for_tg
from thread_manager import format_threads_for_prompt, save_thread_entry, get_thread_msg_id
from retry_queue_manager import add_to_retry_queue, get_pending_retries, remove_from_retry_queue
from report_manager import update_daily_report, generate_weekly_summary

# 중복 보고 방지를 위한 기록 파일 경로
LAST_REPORT_LOG = os.path.join(BASE_DIR, "data", "last_report.json")

async def handle_scheduled_reports(application: Application):
    """
    [V9.0] 매일/매주 오전 6시가 되면 보고서를 작성하여 부장님께 배달합니다.
    """
    try:
        # 1. 부장님 시간대로 현재 시각 확인
        tz = pytz.timezone(USER_TIMEZONE)
        now = datetime.datetime.now(tz)
        today_str = now.strftime("%Y-%m-%d")
        
        # 06시 정각~07시 사이인지 확인
        if now.hour != 6:
            return

        # 2. 이미 보고했는지 장부 확인
        if os.path.exists(LAST_REPORT_LOG):
            with open(LAST_REPORT_LOG, "r") as f:
                last_log = json.load(f)
                if last_log.get("date") == today_str:
                    return # 오늘 이미 보고 완료

        logger.info(f"⏰ 오전 6시 정각! [{today_str}] 비즈니스 리포트 생성을 시작합니다.")

        # 3. 일일 보고서 생성 (어제 메일 요약)
        daily_json = await asyncio.to_thread(update_daily_report)
        
        if daily_json:
            msg = "☀️ <b>[피아니] 일일 비즈니스 리포트 (어제자)</b>\n\n"
            for topic in daily_json.get("topics", []):
                msg += f"📌 <b>{topic['category']}</b>\n"
                for item in topic.get("items", []):
                    msg += f"- {escape_for_tg(item)}\n"
                msg += "\n"
            
            await application.bot.send_message(chat_id=str(TELEGRAM_CHAT_ID), text=msg, parse_mode="HTML")
            logger.info("일일 보고서 텔레그램 발송 완료")
        
        # 4. 일요일인 경우 주간 통합 보고서 추가 생성 (월~토)
        # 0:월, 1:화 ... 5:토, 6:일
        if now.weekday() == 6:
            logger.info("📅 오늘은 일요일입니다. 주간 통합 리포트 작성을 시작합니다.")
            weekly_json = await asyncio.to_thread(generate_weekly_summary)
            
            if weekly_json:
                w_msg = "🏛 <b>[피아니] 주간 비즈니스 트렌드 요약 (월~토)</b>\n\n"
                w_msg += f"📜 <b>종합 총평:</b>\n{escape_for_tg(weekly_json.get('weekly_summary', ''))}\n\n"
                w_msg += "🏆 <b>핵심 성과 리스트:</b>\n"
                for ach in weekly_json.get("key_achievements", []):
                    w_msg += f"✨ {escape_for_tg(ach)}\n"
                
                await application.bot.send_message(chat_id=str(TELEGRAM_CHAT_ID), text=w_msg, parse_mode="HTML")
                logger.info("주간 보고서 텔레그램 발송 완료")

        # 5. 장부에 오늘 보고 마쳤다고 기록
        os.makedirs(os.path.dirname(LAST_REPORT_LOG), exist_ok=True)
        with open(LAST_REPORT_LOG, "w") as f:
            json.dump({"date": today_str}, f)

    except Exception as e:
        logger.error(f"스케줄 보고서 작성 중 오류 발생: {e}")

async def background_mail_checker(application: Application):
    """
    [V11.0] 피아니의 심장부 메인 엔진입니다. 
    1분에 한 번씩 메일을 감시하고, 정해진 시간에 일일/주간 보고서를 작성합니다.
    """
    is_first_run = True
    logger.info("⚙️ 메일 감시 엔진(Background Checker)이 시동되었습니다. (v11.0)")
    
    while True:
        try:
            # [V9.0] 매 분마다 현재 시각을 체크하여 보고서 작업 수행
            await handle_scheduled_reports(application)
            
            # [V1.12.0] 재시도 대기열 확인 및 처리
            pending_retries = get_pending_retries()
            if pending_retries:
                logger.info(f"재시도 대기열에서 {len(pending_retries)}건 처리 시작...")
                for retry_item in pending_retries:
                    retry_mail = retry_item["mail_data"]
                    retry_uid = retry_item["uid"]
                    thread_history_text = format_threads_for_prompt()
                    ai_result = await asyncio.to_thread(process_email_with_ai, retry_mail, thread_history_text)

                    if not ai_result.get('is_ai_error'):
                        logger.info(f"재시도 성공! 요약 전송: {retry_mail.get('subject')}")
                        thread_key = ai_result.get('thread_key', retry_mail.get('subject', ''))
                        thread_index = ai_result.get('thread_index', 1)
                        is_thread = ai_result.get('is_thread', False)
                        t_data = {"msg_id": get_thread_msg_id(thread_key)} if is_thread else {}
                        await send_email_alert(application, retry_mail, ai_result, t_data, thread_key)
                        save_thread_entry(thread_key, thread_index, ai_result.get('summary', ''), t_data.get('msg_id'))
                        remove_from_retry_queue(retry_uid)

            # [V1.12.2] 새 메일 가져오기
            logger.info("💓 메일함 확인 중... (Scanning for new emails)")
            unseen_emails = await asyncio.to_thread(fetch_unseen_emails)
            
            # [V10.0] 서버 재시작 시 과거 메일 도배 방지 로직
            if is_first_run:
                if unseen_emails:
                    for mail_data in unseen_emails:
                        save_processed_uid(mail_data['uid'])
                    logger.info(f"서버 가동 전의 {len(unseen_emails)}통의 메일은 기록만 남기고 알림 없이 무시했습니다.")
                is_first_run = False
                logger.info("✅ 모든 시동 준비가 완료되었습니다. 이제부터 실시간 감시를 시작합니다.")
                await asyncio.sleep(10)
                continue

            # 새 메일 처리
            for mail_data in unseen_emails:
                thread_history_text = format_threads_for_prompt()
                ai_result = await asyncio.to_thread(process_email_with_ai, mail_data, thread_history_text)

                if ai_result.get('status') == '스킵':
                    logger.info(f"AI 판단: 학습 패턴에 의해 스킵 ({mail_data.get('subject')})")
                    await send_skip_alert(application, mail_data, ai_result)
                elif ai_result.get('is_ai_error'):
                    logger.warning(f"AI 실패 → 재시도 대기열 등록: {mail_data.get('subject')}")
                    add_to_retry_queue(mail_data)
                else:
                    thread_key = ai_result.get('thread_key', mail_data.get('subject', ''))
                    thread_index = ai_result.get('thread_index', 1)
                    is_thread = ai_result.get('is_thread', False)
                    t_data = {"msg_id": get_thread_msg_id(thread_key)} if is_thread else {}
                    
                    await send_email_alert(application, mail_data, ai_result, t_data, thread_key)
                    save_thread_entry(thread_key, thread_index, ai_result.get('summary', ''), t_data.get('msg_id'))
                
                save_processed_uid(mail_data['uid'])

            # 1분 대기
            await asyncio.sleep(60)

        except Exception as e:
            logger.error(f"메일 엔진 내부 오류 발생: {e}")
            await asyncio.sleep(300) # 오류 시 5분 휴식

async def main():
    """
    지능형 비서 시스템 공장을 가동하는 최종 '빅 버튼(전원 스위치)'입니다!
    여기서 텔레그램 메신저 통신망 조립과 무한반복 스케줄러(심장 모터)를 한 번에 합체시킵니다.
    """
    if not TELEGRAM_BOT_TOKEN:
        logger.error("텔레그램 봇 토큰이 하나도 없습니다. .env 파일에 비밀번호를 채워넣어 주세요 파업합니다!")
        return

    logger.info("텔레그램 봇 두뇌와 통신망을 열심히 뚝딱뚝딱 조립하고 있습니다...")
    
    # 텔레그램 봇의 뼈대와 코어 엔진 장착
    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    
    # 텔레그램 봇에게 '사용자가 버튼 누르면 이렇게 행동해야 해'라는 지시사항을 귀속에 세뇌시킵니다.
    setup_telegram_handlers(application)
    
    # 봇이 사용자 스마트폰과 메시지를 언제든 주고받을 수 있게 인터넷 망과 연결합니다.
    await application.initialize()
    await application.start()
    await application.updater.start_polling()

    # 여기까지 아무런 폭발 없이 진행되었다면 성공입니다.
    logger.info("모든 부품과 톱니바퀴 준비 완료! 파이프라인 공장이 24시간 쌩쌩 가동됩니다.")
    
    # 자 이제 백그라운드(보이지 않는 무대 뒤편)에서 1분에 한 번 확인하는 무한 공장 모터를 가동시킵니다!
    try:
        await background_mail_checker(application)
    except asyncio.CancelledError:
        # 누군가 끌 버튼을 눌렀다면 아주 평화롭게 다음 단계로 넘어갑니다.
        pass
    finally:
        # 우리가 직접 조용히 이 프로그램을 끄려고 할 때,
        # 텔레그램 봇도 꼬리잡혀 에러 나지 않게 "잘 가~" 하고 예의 바르게 뒷정리를 다 해주고 문을 잠그고 끕니다.
        logger.info("안전하고 우아하게 전체 지능형 파이프라인 전원을 종료합니다. 수고하셨습니다!")
        await application.updater.stop()
        await application.stop()
        await application.shutdown()

if __name__ == "__main__":
    # 파이썬 프로그램의 심장을 최초로 뛰게 하는 시동 거는 구역입니다.
    try:
         # 윈도우(Windows) 컴퓨터는 간혹 백그라운드 멀티태스킹(비동기) 환경에서
         # 고질적인 에러 충돌을 일이키곤 합니다. 그 에러를 원천 차단하는 방어막 쉴드 셋팅입니다. 
         if os.name == 'nt':
             asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
             
         # 전원버튼 꾹 누르기!
         asyncio.run(main())
    except KeyboardInterrupt:
        # 주인님이 키보드로 (Ctrl+C) 강제 종료를 눌렀을 때의 반응입니다.
        logger.info("사용자님께서 강제로 전원 플러그를 뽑았습니다(Ctrl+C). 봇 비서 칼같이 퇴근합니다!")
