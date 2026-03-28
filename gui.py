import tkinter as tk
from tkinter import scrolledtext, messagebox
import subprocess
import threading
import os
import sys
import ctypes

# V1.8.0 분리 고도화! 이제 백엔드 라이브러리를 임포트하지 않습니다. 
# GUI(Tkinter)는 오직 가벼운 껍데기 역할만 수행하게 됩니다.

try:
    # 모니터 해상도 배율에 맞춰 글씨가 깨지지 않고 선명하게 보이도록 강제합니다.
    ctypes.windll.shcore.SetProcessDpiAwareness(1)
except Exception:
    pass

class EmailHelperGUI:
    def __init__(self, root):
        self.root = root
        self.root.title("이메일 비서")
        # 사용자 요청: 세로가 긴 황금비율 심플 디자인 (400 x 650)
        self.root.geometry("400x650")
        self.root.resizable(False, False) # 창 크기를 고정시켜 비율을 유지합니다
        self.root.configure(bg="#2d2d2d")

        self.process = None

        # [메인 프레임: 군더더기 테두리를 모두 지운 깔끔한 미니멀 디자인]
        main_frame = tk.Frame(root, bg="#2d2d2d")
        main_frame.pack(fill="both", expand=True, padx=25, pady=25)

        # [로그인 정보 입력]
        # .env 파일에 등록된 테스트 계정 정보를 자동으로 읽어와 채워넣습니다 (테스트 환경 최적화)
        import config
        tk.Label(main_frame, text="회사 이메일 주소", font=("맑은 고딕", 10), bg="#2d2d2d", fg="#bbbbbb").pack(anchor="w", pady=(0, 2))
        self.email_entry = tk.Entry(main_frame, font=("맑은 고딕", 11), bg="#3d3d3d", fg="white", insertbackground="white", relief="flat")
        self.email_entry.insert(0, config.EMAIL_ADDRESS if config.EMAIL_ADDRESS else "dbcv052@dongbochain.com")
        self.email_entry.pack(fill="x", ipady=6, pady=(0, 15))

        tk.Label(main_frame, text="비밀번호", font=("맑은 고딕", 10), bg="#2d2d2d", fg="#bbbbbb").pack(anchor="w", pady=(0, 2))
        self.pwd_entry = tk.Entry(main_frame, font=("맑은 고딕", 11), show="●", bg="#3d3d3d", fg="white", insertbackground="white", relief="flat")
        if config.EMAIL_PASSWORD:
            self.pwd_entry.insert(0, config.EMAIL_PASSWORD)
        self.pwd_entry.pack(fill="x", ipady=6, pady=(0, 25))

        # [제어 버튼] - 화려한 색상을 조금 죽여 세련되고 전문적인 느낌 부여
        self.start_btn = tk.Button(main_frame, text="서버 시작", font=("맑은 고딕", 12, "bold"), bg="#388E3C", fg="white", relief="flat", cursor="hand2", command=self.start_server)
        self.start_btn.pack(fill="x", ipady=8, pady=(0, 10))

        self.stop_btn = tk.Button(main_frame, text="서버 종료", font=("맑은 고딕", 12, "bold"), bg="#D32F2F", fg="white", relief="flat", cursor="hand2", state="disabled", command=self.stop_server)
        self.stop_btn.pack(fill="x", ipady=8, pady=(0, 25))

        # [로그창]
        tk.Label(main_frame, text="실시간 로그", font=("맑은 고딕", 10), bg="#2d2d2d", fg="#bbbbbb").pack(anchor="w", pady=(0, 5))
        self.log_area = scrolledtext.ScrolledText(main_frame, font=("Consolas", 9), bg="#1e1e1e", fg="#81C784", bd=0, padx=10, pady=10)
        self.log_area.pack(fill="both", expand=True)

        self.log("💡 시스템 대기 중입니다.")

    def log(self, message):
        """실시간 로그 출력 (오류 없는 안전한 줄바꿈)"""
        self.log_area.config(state='normal')
        self.log_area.insert(tk.END, message + "\n")
        self.log_area.see(tk.END)
        self.log_area.config(state='disabled')

    def start_server(self):
        email = self.email_entry.get().strip()
        pwd = self.pwd_entry.get().strip()

        if not email or not pwd:
            messagebox.showwarning("입력 오류", "이메일 주소와 비밀번호를 모두 입력하세요.")
            return

        self.log("\n==============================")
        self.log("🚀 서버 부팅을 시작합니다...")
        
        self.start_btn.config(state="disabled", bg="#555555")
        self.stop_btn.config(state="normal", bg="#D32F2F")
        self.email_entry.config(state="disabled")
        self.pwd_entry.config(state="disabled")

        env = os.environ.copy()
        env["EMAIL_ADDRESS"] = email
        env["EMAIL_PASSWORD"] = pwd
        env["PYTHONIOENCODING"] = "utf-8" # 윈도우 파이썬 터미널 한글 깨짐(외계어) 현상 완벽 방어!
        
        startupinfo = None
        if os.name == 'nt':
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW

        # V1.8.0 분리 고도화 핵심: 이제 EXE가 스스로를 복제하지 않습니다!
        # 단순히 윈도우 파이썬 명령어로 백그라운드에서 `python run_server.py`를 몰래 타이핑시켜주는 리모컨 역할을 합니다.
        cmd = ["python", "-u", "run_server.py"]

        self.process = subprocess.Popen(
            cmd,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            startupinfo=startupinfo,
            encoding='utf-8',
            errors='replace'
        )

        threading.Thread(target=self.read_output, daemon=True).start()

    def read_output(self):
        """로그를 쓰레드로 옮기는 함수"""
        try:
            for line in iter(self.process.stdout.readline, ''):
                if line:
                    self.root.after(0, self.log, line.strip())
        except Exception as e:
            self.root.after(0, self.log, f"⚠️ 로그 오류: {e}")
        
        if self.process:
            try:
                self.process.stdout.close()
                self.process.wait()
            except Exception:
                pass
        self.root.after(0, self.on_process_exit)

    def stop_server(self):
        """강력한 서버 종료 (Kill)"""
        if self.process:
            self.log("🛑 서버를 강제 종료합니다...")
            try:
                if os.name == 'nt':
                    subprocess.run(['taskkill', '/F', '/T', '/PID', str(self.process.pid)], creationflags=subprocess.CREATE_NO_WINDOW)
                else:
                    self.process.kill()
            except Exception as e:
                self.log(f"종료 오류: {e}")
            self.process = None

    def on_process_exit(self):
        self.log("💤 서버가 종료되었습니다.")
        self.log("==============================\n")
        self.start_btn.config(state="normal", bg="#388E3C")
        self.stop_btn.config(state="disabled", bg="#555555")
        self.email_entry.config(state="normal")
        self.pwd_entry.config(state="normal")

if __name__ == "__main__":
    # V1.8.0 부터는 프로그램(.exe)이 실행되면 무조건 투명창 대신 순수하게 화면(리모컨)만 눈앞에 그려냅니다.
    root = tk.Tk()
    app = EmailHelperGUI(root)
    root.mainloop()
