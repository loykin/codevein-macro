"""ps5view.py에 조작 명령을 보내는 작은 클라이언트 (매크로에서 import 해서 쓴다).

  python pscmd.py tap down          버튼 탭
  python pscmd.py stick left 0.3 -1 스틱 (놓으려면 0 0)
  python pscmd.py snap out.png      현재 화면 저장
  python pscmd.py play [파일]        녹화 재생 (기본 routes/analog_last.json)
  python pscmd.py stop              재생 멈춤
"""
import json
import os
import socket
import sys

PORT = 8650


class PS:
    def __init__(self):
        self.sock = socket.create_connection(('127.0.0.1', PORT), timeout=5)
        self.r = self.sock.makefile('rb')

    def _send(self, msg):
        self.sock.sendall((json.dumps(msg) + '\n').encode())
        reply = self.r.readline().decode().strip()
        if reply != 'ok':
            raise RuntimeError(reply)

    def button(self, name, action='tap', hold=0.08):
        self._send({'button': name, 'action': action, 'hold': hold})

    def stick(self, which, x, y):
        self._send({'stick': which, 'x': x, 'y': y})

    def snap(self, path):
        self._send({'snap': path})

    def play(self, path):
        self._send({'play': os.path.abspath(path)})

    def stop(self):
        self._send({'stop': True})

    def standby(self):
        self._send({'standby': True})


if __name__ == '__main__':
    ps = PS()
    cmd, *a = sys.argv[1:]
    if cmd == 'tap':
        for b in a:
            ps.button(b)
    elif cmd == 'stick':
        ps.stick(a[0], float(a[1]), float(a[2]))
    elif cmd == 'snap':
        ps.snap(a[0])
    elif cmd == 'play':
        ps.play(a[0] if a else 'routes/analog_last.json')
    elif cmd == 'stop':
        ps.stop()
