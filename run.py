"""파밍 전체 실행기: 뷰어(ps5view)와 반복(farm)을 켜고, 뷰어가 죽으면 둘 다 다시 켠다.

  uv run python -u run.py
"""
import os
import signal
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable          # 자기를 실행한 가상환경의 파이썬으로 뷰어·파밍도 띄운다
LOGDIR = os.path.join(HERE, 'routes')

stop = False


def on_signal(*_):
    global stop
    stop = True


signal.signal(signal.SIGTERM, on_signal)
signal.signal(signal.SIGINT, on_signal)


def start_viewer():
    """세션 연결에 성공할 때까지 재시도 (직전 세션이 PS5에서 정리될 때까지 '다른 세션 연결 중'이 날 수 있음)."""
    while not stop:
        log = open(os.path.join(LOGDIR, 'viewer.log'), 'w')
        p = subprocess.Popen([PY, '-u', 'ps5view.py'], cwd=HERE, stdout=log, stderr=subprocess.STDOUT)
        t = time.monotonic()
        while time.monotonic() - t < 25 and p.poll() is None:
            if '연결됨' in open(log.name, encoding='utf-8', errors='ignore').read():
                print(time.strftime('%H:%M:%S'), '뷰어 연결됨', flush=True)
                return p
            time.sleep(0.5)
        p.terminate()
        p.wait()
        print(time.strftime('%H:%M:%S'), '뷰어 연결 실패, 10초 뒤 다시 시도', flush=True)
        time.sleep(10)
    return None


restarts = 0
frozen = 0
while not stop:
    viewer = start_viewer()
    if viewer is None:
        break
    time.sleep(2)
    farm = subprocess.Popen([PY, '-u', 'farm.py', '0'], cwd=HERE)
    while not stop and viewer.poll() is None and farm.poll() is None:
        time.sleep(1)
    if farm.poll() == 3:            # 연속 실패: 영상이 멈춘 경우가 많아 뷰어를 새로 연결하고 다시 해 본다
        frozen += 1
        if frozen >= 3:
            print(time.strftime('%H:%M:%S'), '재연결해도 계속 실패해서 멈춰요', flush=True)
            stop = True
    elif farm.poll() == 4:          # 목표 개수 달성 (PS5는 farm이 휴식 모드로 보냄)
        print(time.strftime('%H:%M:%S'), '목표 개수를 모아서 끝내요', flush=True)
        stop = True
    elif farm.poll() == 2:          # 겨우살이로 돌아오지 못함: 사람이 봐야 하는 상황
        print(time.strftime('%H:%M:%S'), '캐릭터를 겨우살이로 되돌리지 못해서 멈춰요', flush=True)
        stop = True
    for proc in (farm, viewer):
        if proc.poll() is None:
            proc.send_signal(signal.SIGINT if proc is farm else signal.SIGTERM)
            try:
                proc.wait(10)
            except subprocess.TimeoutExpired:
                proc.kill()
    if not stop:
        restarts += 1
        print(time.strftime('%H:%M:%S'), f'뷰어가 끊겨서 다시 시작 ({restarts}번째)', flush=True)
        time.sleep(5)
print('종료')
sys.exit(0)
