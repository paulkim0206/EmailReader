"""
V1.8.0 마이크로서비스 분리 이후, 순수하게 백엔드 엔진(이메일 파싱 + AI 텔레그램 송수신)만을
백그라운드에서 빙글빙글 돌리는 '독립형 서버 진입점' 파일입니다.
이제 사용자님은 껍데기 화면(EXE)을 닫지 않고도, 이 안의 코드만 백날천날 고쳐서 테스트할 수 있습니다!
"""
import asyncio
import os
import sys
import main

if __name__ == "__main__":
    if os.name == 'nt':
        # 윈도우 환경에서 비동기 루프 에러를 방어하는 마법의 주문입니다.
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    try:
        asyncio.run(main.main())
    except KeyboardInterrupt:
        pass
    sys.exit(0)
