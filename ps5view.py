"""PS5 화면 보기 + 조작 클라이언트 (chiaki 대신).

pyremoteplay 세션 하나로 영상을 받아 창에 띄우고, 같은 세션으로 컨트롤러 입력(아날로그 스틱 포함)을 보낸다.
매크로는 이 모듈의 Client를 가져다 frame()으로 화면을 읽고 button()/stick()으로 조작한다.

ps5rmtctl이 설치된 가상환경으로 실행 (로그인·PS5 등록은 ~/.pyremoteplay 에 저장돼 있음):
  uv run python -u ps5view.py

듀얼센스 전달·녹화는 padlink.py가 따로 하고, 이 창은 화면과 명령 포트만 맡는다 (창 Q=종료).
"""
import asyncio
import threading
import time

import cv2
import numpy as np
from pyremoteplay.receiver import QueueReceiver

from ps5rmtctl import config
from ps5rmtctl.core import PS5


class Client:
    def __init__(self, resolution='720p', fps='high'):
        self.ps5 = PS5(config.get_default('host'), config.get_default('user'))
        self.receiver = QueueReceiver(max_video_frames=2, max_audio_frames=1)
        self.resolution, self.fps = resolution, fps
        self.loop = None
        self.controller = None
        self.ready = threading.Event()
        self.closed = threading.Event()
        self.error = None
        self.player = None
        self.osd = ''               # 창 왼쪽 위에 띄울 글자 (매크로가 'osd' 명령으로 갱신)
        self._frame_lock = threading.Lock()
        self._last_frame = None
        self._last_img = None
        self.stop_flag = threading.Event()

    # ------------------------------------------------------------ 세션 (별도 스레드의 asyncio)
    def start(self, timeout=15):
        threading.Thread(target=self._run, daemon=True).start()
        if not self.ready.wait(timeout):
            raise RuntimeError(self.error or 'PS5 세션 연결 시간 초과')
        if self.error:
            raise RuntimeError(self.error)

    def _run(self):
        self.loop = asyncio.new_event_loop()
        try:
            self.loop.run_until_complete(self._session())
        except Exception as e:          # noqa: BLE001 - 연결 실패 이유를 그대로 보여 준다
            self.error = str(e)
            self.ready.set()
        finally:
            self.closed.set()

    async def _session(self):
        dev = self.ps5.device
        self.ps5.refresh()
        if not dev.is_on:
            raise RuntimeError('PS5가 꺼져 있거나 휴식 모드예요')
        if dev.create_session(self.ps5.user, loop=self.loop, receiver=self.receiver,
                              resolution=self.resolution, fps=self.fps) is None:
            raise RuntimeError('세션을 만들지 못했어요 (PS5 등록 확인)')
        if not await dev.connect():
            raise RuntimeError(f'세션 시작 실패: {getattr(dev.session, "error", "?")}')
        if not await dev.async_wait_for_session(10):
            dev.disconnect()
            raise RuntimeError('세션 준비 시간 초과 (다른 Remote Play가 연결돼 있지 않은지 확인)')
        self.controller = dev.controller
        self.controller.start()             # 스틱 상태를 계속 보내는 워커
        self.ready.set()
        while not self.closed.is_set() and dev.connected:
            await asyncio.sleep(0.1)
        dev.disconnect()

    def close(self):
        self.closed.set()

    def standby(self):
        """PS5를 휴식 모드로 보낸다 (세션도 끊긴다)."""
        self.loop.call_soon_threadsafe(self.ps5.device.session._send_standby)

    # ------------------------------------------------------------ 화면
    def frame(self):
        """가장 최근 화면 (BGR numpy) 또는 None.

        PyAV의 프레임 변환은 여러 스레드가 동시에 부르면 충돌(세그폴트)한다. 창 그리기와 명령 포트(매크로)가
        같이 부르므로 잠금을 걸고, 같은 프레임은 한 번만 변환해서 나눠 쓴다."""
        with self._frame_lock:
            f = self.receiver.get_latest_video_frame()
            if f is None:
                return None if self._last_img is None else self._last_img.copy()
            if f is not self._last_frame:
                self._last_img = f.to_ndarray(format='bgr24')
                self._last_frame = f
            return self._last_img.copy()

    # ------------------------------------------------------------ 조작
    def button(self, name, action='tap', hold=0.08):
        """name: cross circle square triangle up down left right l1 r1 l2 r2 l3 r3 options share ps touchpad"""
        self.controller.button(name, action, delay=hold)
        if action == 'tap':
            # 떼기 이벤트가 한 번 빠지면 버튼이 눌린 채로 남는다(↓가 길게 눌려 아이템 팔레트 편집이 열림 등).
            # tap은 누르고 hold만큼 기다렸다 뗀 뒤 돌아오므로, 조금 있다가 떼기를 한 번 더 보낸다
            time.sleep(0.05)
            self.controller.button(name, 'release')

    def stick(self, which, x, y):
        """which: 'left'/'right', x·y: -1.0~1.0 (위가 -)."""
        self.controller.stick(which, point=(x, y))


CMD_PORT = 8650   # 같은 Mac의 다른 프로그램(매크로)이 조작 명령을 보내는 포트 (localhost 전용)


def serve_commands(c):
    """한 줄에 JSON 하나: {"button":"cross","action":"tap","hold":0.08} / {"stick":"left","x":0.3,"y":-1}
    / {"snap":"경로.png"} (현재 화면 저장). 응답은 "ok" 또는 "err ..." 한 줄."""
    import json
    import socketserver

    class H(socketserver.StreamRequestHandler):
        def handle(self):
            for line in self.rfile:
                try:
                    m = json.loads(line)
                    if 'button' in m:
                        c.button(m['button'], m.get('action', 'tap'), m.get('hold', 0.08))
                    elif 'stick' in m:
                        c.stick(m['stick'], float(m['x']), float(m['y']))
                    elif 'play' in m:
                        if c.player and c.player.is_alive():
                            raise RuntimeError('이미 재생 중')
                        c.stop_flag.clear()
                        c.player = threading.Thread(target=play, args=(c, m['play'], c.stop_flag), daemon=True)
                        c.player.start()
                    elif 'stop' in m:
                        c.stop_flag.set()
                    elif 'status' in m:
                        self.wfile.write(f"ok {'playing' if c.player and c.player.is_alive() else 'idle'}\n".encode())
                        continue
                    elif 'standby' in m:
                        c.standby()
                    elif 'osd' in m:
                        c.osd = str(m['osd'])
                    elif 'snap' in m:
                        img = c.frame()
                        if img is None:
                            raise RuntimeError('아직 화면 없음')
                        cv2.imwrite(m['snap'], img)
                    self.wfile.write(b'ok\n')
                except Exception as e:      # noqa: BLE001
                    self.wfile.write(f'err {e}\n'.encode())

    socketserver.ThreadingTCPServer.allow_reuse_address = True
    srv = socketserver.ThreadingTCPServer(('127.0.0.1', CMD_PORT), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()


def play(c, path, stop_flag):
    """녹화 파일을 그대로 재생 (아날로그 값·타이밍 그대로)."""
    import json
    evs = json.load(open(path))
    held = set()
    t0 = time.monotonic()
    try:
        for ev in evs:
            if stop_flag.is_set():
                break
            d = t0 + ev['t'] - time.monotonic()
            if d > 0:
                time.sleep(d)
            if 'b' in ev:
                c.button(ev['b'], ev['a'])
                if ev['a'] == 'release':     # 떼기 신호가 빠지면 버튼이 눌린 채로 남으므로 한 번 더
                    c.button(ev['b'], 'release')
                (held.add if ev['a'] == 'press' else held.discard)(ev['b'])
            else:
                c.stick(ev['s'], ev['x'], ev['y'])
    finally:
        for _ in range(2):              # 떼기 신호 유실 대비 두 번
            for b in held:
                c.button(b, 'release')
        c.stick('left', 0, 0)
        c.stick('right', 0, 0)


def draw_osd(img, text):
    """한글을 그리려면 OpenCV 기본 글꼴로는 안 돼서 Pillow로 그린다."""
    from PIL import Image, ImageDraw, ImageFont
    global _FONT
    if '_FONT' not in globals():
        _FONT = ImageFont.truetype('/System/Library/Fonts/AppleSDGothicNeo.ttc', 26)
    pil = Image.fromarray(cv2.cvtColor(img, cv2.COLOR_BGR2RGB))
    d = ImageDraw.Draw(pil)
    box = d.multiline_textbbox((14, 12), text, font=_FONT)
    d.rectangle((box[0] - 8, box[1] - 6, box[2] + 8, box[3] + 6), fill=(0, 0, 0))
    d.multiline_text((14, 12), text, font=_FONT, fill=(255, 220, 120))
    img[:] = cv2.cvtColor(np.asarray(pil), cv2.COLOR_RGB2BGR)


def main():
    import signal
    c = Client()
    print('PS5 연결 중...')
    c.start()
    # 강제 종료돼도 세션을 정상적으로 끊어야 PS5가 '다른 세션 연결 중'으로 막지 않는다
    signal.signal(signal.SIGTERM, lambda *_: c.close())
    signal.signal(signal.SIGINT, lambda *_: c.close())
    serve_commands(c)
    print(f'연결됨. 명령 포트 {CMD_PORT}. 창에서 Q를 누르면 종료')
    shown, t0 = 0, time.monotonic()
    while not c.closed.is_set():
        img = c.frame()
        if img is not None:
            if c.osd:
                draw_osd(img, c.osd)
            cv2.imshow('PS5', img)
            shown += 1
        if cv2.waitKey(10) & 0xFF == ord('q'):
            break
    c.stop_flag.set()
    c.close()
    time.sleep(1.0)                 # 세션 스레드가 disconnect를 보낼 시간
    print(f'종료 (평균 {shown / max(time.monotonic() - t0, 1e-6):.0f} fps 표시)')


if __name__ == '__main__':
    main()
