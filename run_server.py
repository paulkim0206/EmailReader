"""
[V11.9.9 긴급 복원]
서버 시동(nohup) 경로가 이 파일을 가중하고 있어 긴급히 복구했습니다.
이 파일은 단순히 main.py를 실행시켜 비동기 루프로 진입하는 '진입점' 역할을 수행합니다.
"""
import asyncio
import os
import sys
import main

if __name__ == "__main__":
    if os.name == 'nt':
        # 윈도우 환경 대응용 (필요 시)
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    try:
        # 메인 엔진 가동 시작!
        asyncio.run(main.main())
    except KeyboardInterrupt:
        pass
    sys.exit(0)
