import asyncio
import os
from telegram.ext import Application
from config import TELEGRAM_BOT_TOKEN, logger

# 앞서 우리가 정성껏 만든 주요 도구들을 하나의 커다란 공장 상자로 불러옵니다!
from mail_parser import fetch_unseen_emails, save_processed_uid
from ai_processor import process_email_with_ai
from telegram_bot import send_email_alert, setup_telegram_handlers
from thread_manager import get_or_create_thread

async def background_mail_checker(application: Application):
    """
    공장의 거대한 톱니바퀴 메인 모터입니다! 프로그램이 꺼질 때까지 '무한 루프(끝나지 않는 사이클)'로 돌며,
    1분에 한 번씩만 우체통(메일 서버)을 열어보고 텔레그램 비서에게 건네주는 심장부 역할을 합니다.
    """
    logger.info("지능형 이메일 비서가 눈을 번쩍 뜨고 24시간 철통 경계 및 업무를 시작합니다!")
    
    is_first_run = True # 서버가 방금 켜졌는지 확인하는 첫 번째 순찰 티켓입니다.

    while True: # 언제 컴퓨터 전원이 뽑히기 전까지는 포기하지 않고 돕니다.
        try:
            # 1. 메일함에서 안 읽은 새 편지를 살금살금 (보안 속성 PEEK) 꺼내옵니다.
            unseen_emails = fetch_unseen_emails()
            
            # [아이디어 노트 반영] 서버 켜기 전부터 쌓여있던 안 읽은 메일은 알람을 보내지 않고 일괄 무시 처리합니다.
            if is_first_run:
                if unseen_emails:
                    for mail_data in unseen_emails:
                        save_processed_uid(mail_data['uid'])
                    logger.info(f"서버 켜기 전부터 쌓여있던 과거 미확인 메일 {len(unseen_emails)}통은 알림 없이 조용히 무시(패스) 완료했습니다!")
                is_first_run = False
                logger.info("이제부터 새로 도착하는 따끈따끈한 새 이메일만 감시하여 실시간으로 보고합니다.")
                await asyncio.sleep(60)
                continue

            if unseen_emails:
                logger.info(f"앗! 주인님에게 {len(unseen_emails)}통의 새로운 중요한 이메일이 찾아왔습니다.")
                
            from blacklist_manager import load_blacklist, extract_pure_email
            current_blacklist = load_blacklist()

            for mail_data in unseen_emails:
                # 🚫 [문지기 신설] 편지 봉투에 적힌 보낸 사람이 우리가 못 들어오게 막은 스팸 발송자인지 검열합니다.
                pure_sender = extract_pure_email(mail_data.get('sender', ''))
                if pure_sender in current_blacklist:
                    logger.info(f"🚫 [사전 차단 작동] 아하! 이 녀석({pure_sender})은 블랙리스트에 걸렸군요. AI를 깨우지 않고 편지를 바로 휴지통에 꽂아버립니다.")
                    save_processed_uid(mail_data['uid'])
                    continue

                # 2. 이번 메일이 어떤 핑퐁(스레드)방에 속하는지 기억 장부에서 찾고 카운트를 셉니다.
                base_subj, t_data = get_or_create_thread(mail_data["subject"])
                
                # 3. 핑퐁 카운트와 '누적 요약망(이전 대화 전체 리스트)'을 AI에게 주입해 문맥을 100% 이해시킵니다.
                ai_result = process_email_with_ai(mail_data, t_data["count"], t_data.get("summary_history", []))
                
                # [아이디어 노트 반영] 카테고리가 '스킵'으로 분류된 단순 인사/단답 메일은 알림을 보내지 않고 무시합니다.
                if ai_result.get('category', '') == '스킵':
                    logger.info(f"🛡️ AI 필터 작동: 단답형/인사성 무의미한 메일 알림 차단됨 (제목: {mail_data.get('subject')})")
                else:
                    # 4. 의미 있는 요약본만 이전 텔레그램 말풍선에 묶어(Reply) 배달합니다.
                    await send_email_alert(application, mail_data, ai_result, t_data, base_subj)
                
                # 5. 방금 우리가 처리한 이메일은 두 번 다시 읽어서 똑같은 카톡을 또 보내는 실수를 막기 위해
                # 중복 방어 장부에 고유번호를 볼펜으로 세게 꾹꾹 눌러 적습니다.
                save_processed_uid(mail_data['uid'])

            # 한 바퀴 싹 돌았으니, 1분(60초) 동안 공장의 과부하를 막고 인터넷 서버가 화나지 않게 숨을 고릅니다.
            await asyncio.sleep(60)

        except Exception as e:
            # 매우 중요: 예상치 못한 인터넷 선 고장 같은 치명적인 폭풍우(에러)가 와도
            # 컴퓨터 프로그램이 오류창을 띄우며 허무하게 완전히 죽어서 꺼져버리지 않도록 막아주는 최후의 '심폐소생술 방어막'입니다.
            logger.error(f"메일 확인 중 심각한 폭풍우(오류)가 몰아쳤습니다!: {e}")
            logger.warning("시스템이 완전히 망가지는 걸 방어하기 위해 잠시 5분 동안 땅굴에 대피(대기) 후 다시 밖으로 나옵니다!")
            await asyncio.sleep(300) # 300초 = 치명적 오류 후 시스템 안정을 위한 5분 긴급 휴식 시간

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
