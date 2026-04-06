import os
import asyncio
import datetime
import json
import pytz
import sys
from telegram.ext import Application
from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, logger, USER_TIMEZONE, REPORTS_DIR, BASE_DIR

# 앞서 우리가 정성껏 만든 주요 도구들을 하나의 커다란 공장 상자로 불러옵니다!
from mail_parser import fetch_recent_emails, save_processed_uid
from ai_processor import process_email_with_ai, load_all_prompts_to_memory
from telegram_bot import send_email_alert, send_skip_alert, setup_telegram_handlers, escape_for_tg, send_failure_alert, clear_temp_cache
from thread_manager import save_summary_entry, get_thread_msg_id
from retry_queue_manager import add_to_retry_queue, get_pending_retries, remove_from_retry_queue, update_retry_status
from report_manager import update_daily_report, generate_weekly_summary
import memo_manager # [V12.29] 서버 시작 시 즉시 메모 일괄 이사(청소)를 수행하기 위해 임포트

# 중복 보고 방지를 위한 기록 파일 경로
LAST_REPORT_LOG = os.path.join(BASE_DIR, "data", "last_report.json")

async def handle_scheduled_reports(application: Application):
    """
    [V11.8] 매일 정해진 시간에 자동 보고서를 발송합니다.
    - 00:00 (자정): 어제 하루 동안 사용한 AI 토큰 정산 보고
    - 06:00 (오전): 전일 업무 또는 한 주간 업무 종합 보고
    """
    try:
        tz = pytz.timezone(USER_TIMEZONE)
        now = datetime.datetime.now(tz)
        today_str = now.strftime("%Y-%m-%d")
        
        # [V16.6] 스케줄링 운영 시간 확장
        if now.hour not in [0, 6]:
            return

        # 중복 보고 방지를 위한 정밀 장부 로드
        last_log = {}
        if os.path.exists(LAST_REPORT_LOG):
            try:
                with open(LAST_REPORT_LOG, "r") as f:
                    last_log = json.load(f)
            except Exception:
                last_log = {}

        # --- [1] 새벽 0시: 데일리 토큰 정산 보고서 ---
        if now.hour == 0:
            if last_log.get("token_report") != today_str:
                from token_manager import get_daily_token_report_message
                # 자정에 보고하는 것은 '어제' 하루치 데이터입니다.
                yesterday_obj = now - datetime.timedelta(days=1)
                yesterday_str = yesterday_obj.strftime("%Y-%m-%d")
                
                token_msg = get_daily_token_report_message(yesterday_str)
                if token_msg:
                    await application.bot.send_message(chat_id=str(TELEGRAM_CHAT_ID), text=token_msg, parse_mode="HTML")
                    logger.info(f"✅ 자정 토큰 정산 리포트 발송 완료 ({yesterday_str})")
                
                # 토큰 보고 완료 기록
                last_log["token_report"] = today_str
                with open(LAST_REPORT_LOG, "w") as f:
                    json.dump(last_log, f)
            return

        # --- [2] 오전 6시: 비즈니스 업무 보고서 ---
        if now.hour == 6:
            if last_log.get("business_report") == today_str or last_log.get("date") == today_str:
                return

            # 현지 시각 기준 '어제' 날짜 계산
            yesterday_obj = now - datetime.timedelta(days=1)
            yesterday_str = yesterday_obj.strftime("%Y-%m-%d")

            if now.weekday() == 0: # 월요일은 주간 통합
                await send_weekly_business_report(application)
            else: # 나머지는 일일 업무 보고
                await send_daily_business_report(application, target_date=yesterday_str)

            # 업무 보고 완료 기록 (구형 필드 'date'와 신형 필드 'business_report' 병행 기록)
            last_log["business_report"] = today_str
            last_log["date"] = today_str 
            os.makedirs(os.path.dirname(LAST_REPORT_LOG), exist_ok=True)
            with open(LAST_REPORT_LOG, "w") as f:
                json.dump(last_log, f)

    except Exception as e:
        logger.error(f"스케줄 보고서 트리거 중 오류: {e}")

# [V12.14] 중복 로직 제거: 매일 새벽 3시 '자가 재시작'이 메모리를 초기화하므로 
# 별도의 handle_cache_reset() 함수는 폐지되었습니다.

async def send_daily_business_report(application: Application, target_date=None):
    """[V11.8] 고객사별 슬림 일일 보고서를 작성하여 전달합니다."""
    try:
        from report_manager import update_daily_report
        logger.info("📅 일일 비즈니스 리포트 생성을 시작합니다.")
        daily_json = await asyncio.to_thread(update_daily_report, target_date)
        
        if daily_json:
            disp_date = target_date if target_date else (datetime.datetime.now(pytz.timezone(USER_TIMEZONE))).strftime('%Y-%m-%d')
            msg = f"☀️ <b>[피아니] 일일 비즈니스 리포트 ({disp_date})</b>\n\n"
            
            # [핵심] 고객사별로 묶어서 슬림하게 출력 (가시성 최적화)
            client_reports = daily_json.get("client_reports", [])
            if client_reports:
                for report in client_reports:
                    summaries = [s for s in report.get("summaries", []) if s.strip()]
                    if not summaries: continue # 유효 내용 없으면 스킵
                    
                    msg += f"🏢 <b>{escape_for_tg(report.get('client', '기타'))}</b>\n"
                    for item in summaries:
                        msg += f"- {escape_for_tg(item)}\n" # 문장 사이 공백 제거
                    msg += "\n" # 고객사 그룹 간 1줄 공백
            else:
                # 구형 데이터 호환 처리 (topics)
                for topic in daily_json.get("topics", []):
                    items = [i for i in topic.get("items", []) if i.strip()]
                    if not items: continue
                    
                    msg += f"📌 <b>{topic.get('category', '분류')}</b>\n"
                    for item in items:
                        msg += f"- {escape_for_tg(item)}\n"
                    msg += "\n"

            await application.bot.send_message(chat_id=str(TELEGRAM_CHAT_ID), text=msg, parse_mode="HTML")
            logger.info("일일 보고서 텔레그램 발송 완료")
    except Exception as e:
        logger.error(f"일일 보고서 발송 실패: {e}")

async def send_weekly_business_report(application: Application):
    """[V11.8] 한 주의 흐름을 분석한 주간 통합 리포트를 전달합니다."""
    try:
        from report_manager import generate_weekly_summary
        logger.info("📅 주간 업무 총괄 리포트 작성을 시작합니다.")
        weekly_summary = await asyncio.to_thread(generate_weekly_summary)
        
        if weekly_summary:
            msg = "📊 <b>[피아니] 주간 업무 총괄 리포트</b>\n\n"
            msg += f"🧐 <b>주간 전술적 분석:</b>\n{escape_for_tg(weekly_summary.get('주간 전술적 분석', '분석 완료'))}\n\n"
            
            achievements = weekly_summary.get("key_achievements", [])
            if achievements:
                msg += "🏆 <b>이번 주 핵심 추진 성과:</b>\n"
                for item in achievements:
                    msg += f"- {escape_for_tg(item)}\n"
                msg += "\n"
                

            msg += "\n실무가 시작되는 월요일입니다. 부장님, 이번 주도 건승하십시오! 👍"
            await application.bot.send_message(chat_id=str(TELEGRAM_CHAT_ID), text=msg, parse_mode="HTML")
            logger.info("주간 보고서 텔레그램 발송 완료")
    except Exception as e:
        logger.error(f"주간 보고서 발송 실패: {e}")

async def background_mail_checker(application: Application):
    """
    [V11.0] 피아니의 심장부 메인 엔진입니다. 
    1분에 한 번씩 메일을 감시하고, 정해진 시간에 일일/주간 보고서를 작성합니다.
    """
    is_first_run = True
    logger.info("⚙️ 메일 감시 엔진(Background Checker)이 시동되었습니다. (v11.0)")
    
    while True:
        try:
            # [V12.14] 중복 대청소 로직 제거 (새벽 3시 자가 재시작으로 통합)
            
            # [V9.0] 매분마다 현재 시각을 체크하여 보고서 작업 수행
            await handle_scheduled_reports(application)

            # [V12.13] 매일 새벽 3시(현지 시간 기준)에 스스로 재가동하여 메모리를 정화합니다.
            tz = pytz.timezone(USER_TIMEZONE)
            now = datetime.datetime.now(tz)
            if now.hour == 3 and now.minute == 0:
                logger.info("🕒 [새벽 자가 세탁] 시스템을 정화하고 다시 태어납니다...")
                os.execl(sys.executable, sys.executable, *sys.argv)
            
            # [V1.12.0] 재시도 대기열 확인 및 처리
            pending_retries = get_pending_retries()
            if pending_retries:
                logger.info(f"재시도 대기열에서 {len(pending_retries)}건 처리 시작...")
                for retry_item in pending_retries:
                    try:
                        retry_mail = retry_item["mail_data"]
                        retry_uid = retry_item["uid"]
                        retry_count = retry_item.get("retry_count", 1)
                        # [V12.7] 지능형 항복: 배경 재시도는 딱 '1회'만 더 기회를 줍니다.
                        ai_result = await asyncio.to_thread(process_email_with_ai, retry_mail)

                        if not ai_result.get('is_ai_error'):
                            logger.info(f"✅ [배경 재시도 성공] 요약 전송: {retry_mail.get('subject')}")
                            t_data = {} # 더 이상 쓰레드 묶기를 안 하므로 빈 객체 처리.
                            
                            await send_email_alert(application, retry_mail, ai_result, t_data, retry_mail.get('subject', ''))
                            # [V15.0] Flat DB로 저장
                            save_summary_entry(retry_mail.get('uid'), retry_mail.get('subject', ''), ai_result.get('summary', ''), None, ai_result.get('client_name'))
                            remove_from_retry_queue(retry_uid)
                            # [V12.15] 재시도 성공 시 명시적으로 처리 완료 기록
                            save_processed_uid(retry_mail.get('uid'))
                            logger.info(f"장부 저장 성공 (UID: {retry_mail.get('uid')})")
                        else:
                            # [V12.8] 지능형 항복: 마지막 기회 실패 시 부장님께 즉시 최종 보고합니다.
                            logger.error(f"❌ [최종 실패] 5분 뒤 재시도마저 실패: {retry_mail.get('subject')}")
                            
                            await send_failure_alert(application, retry_mail)
                            remove_from_retry_queue(retry_uid)
                            # [V12.15] 최종 실패 시에도 장부에 기록하여 무한 반복 방지
                            save_processed_uid(retry_mail.get('uid'))
                    except Exception as re:
                        logger.error(f"재시도 처리 중 개별 오류 발생 (스킵하고 다음 진행): {re}")
                        continue

            # [V1.12.2] 새 메일 가져오기
            logger.info("💓 메일함 확인 중... (Scanning for new emails)")
            unseen_emails = await asyncio.to_thread(fetch_recent_emails)
            
            # [V12.15] 서버 재시작 시 메일을 도배 방지 명목으로 무단 스킵하던 로직을 제거했습니다.
            # 이제 시작 시점의 모든 읽지 않은 메일까지 꼼꼼히 챙깁니다.
            if is_first_run:
                is_first_run = False
                logger.info("✅ 모든 시동 준비가 완료되었습니다. 실시간 감시를 시작합니다!")

            # 새 메일 처리
            for mail_data in unseen_emails:
                try:
                    ai_result = await asyncio.to_thread(process_email_with_ai, mail_data)

                    if ai_result.get('status') == '스킵':
                        logger.info(f"🙈 AI 판단: 학습 패턴에 의해 스킵 ({mail_data.get('subject')})")
                        await send_skip_alert(application, mail_data, ai_result)
                        # 분석은 안 하지만, 스킵 판단이 섰으므로 처리 완료로 기록
                        save_processed_uid(mail_data['uid'])
                    elif ai_result.get('is_ai_error'):
                        logger.warning(f"⚠️ AI 실패 → 지연 보고 및 대기열 등록: {mail_data.get('subject')}")
                        await send_email_alert(application, mail_data, ai_result, {}, mail_data.get('subject', ''))
                        add_to_retry_queue(mail_data)
                    else:
                        t_data = {} # 더 이상 쓰레드 묶기를 안 하므로 빈 객체 처리.
                        await send_email_alert(application, mail_data, ai_result, t_data, mail_data.get('subject', ''))
                        save_summary_entry(mail_data.get('uid'), mail_data.get('subject', ''), ai_result.get('summary', ''), None, ai_result.get('client_name'))
                        save_processed_uid(mail_data['uid'])
                        logger.info(f"장부 저장 성공 (UID: {mail_data.get('uid')})")
                except Exception as me:
                    logger.error(f"메일 개별 처리 중 돌발 오류 (다음 메일로 넘어감): {me}")
                    continue

            # 1분 대기
            await asyncio.sleep(60)

        except Exception as e:
            logger.error(f"메일 엔진 내부 오류 발생: {e}")
            await asyncio.sleep(60) # [V12.16] 무조건 5분 휴식 폐지. 1분 뒤 다시 시도하여 민첩성 강화.

async def main():
    """
    지능형 비서 시스템 공장을 가동하는 최종 '빅 버튼(전원 스위치)'입니다!
    여기서 텔레그램 메신저 통신망 조립과 무한반복 스케줄러(심장 모터)를 한 번에 합체시킵니다.
    """
    if not TELEGRAM_BOT_TOKEN:
        logger.error("텔레그램 봇 토큰이 하나도 없습니다. .env 파일에 비밀번호를 채워넣어 주세요 파업합니다!")
        return

    logger.info("텔레그램 봇 두뇌와 통신망을 열심히 뚝딱뚝딱 조립하고 있습니다...")
    
    # [V12.13] 모든 지침서(프롬프트)를 미리 암기하여 똑똑한 분석을 준비합니다.
    load_all_prompts_to_memory()
    
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
