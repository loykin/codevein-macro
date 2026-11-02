"""Mac에 연결한 듀얼센스 입력을 ps5view.py로 넘겨 PS5를 조작하고, 아날로그 값 그대로 녹화한다.

  uv run python -u padlink.py [녹화 파일=routes/analog_last.json]

듀얼센스 터치패드 클릭 = 녹화 시작/끝 (터치패드는 PS5로 넘기지 않는다).
"""
import json
import os
import socket
import sys
import time

os.environ['SDL_JOYSTICK_ALLOW_BACKGROUND_EVENTS'] = '1'
os.environ['PYGAME_HIDE_SUPPORT_PROMPT'] = '1'
import pygame                               # noqa: E402
from pygame._sdl2 import controller         # noqa: E402

PORT = 8650
STICK_DEAD = 0.08
TOUCHPAD = 20
P = pygame
BUTTONS = {
    P.CONTROLLER_BUTTON_A: 'cross', P.CONTROLLER_BUTTON_B: 'circle',
    P.CONTROLLER_BUTTON_X: 'square', P.CONTROLLER_BUTTON_Y: 'triangle',
    P.CONTROLLER_BUTTON_LEFTSHOULDER: 'l1', P.CONTROLLER_BUTTON_RIGHTSHOULDER: 'r1',
    P.CONTROLLER_BUTTON_LEFTSTICK: 'l3', P.CONTROLLER_BUTTON_RIGHTSTICK: 'r3',
    P.CONTROLLER_BUTTON_START: 'options', P.CONTROLLER_BUTTON_BACK: 'share',
    P.CONTROLLER_BUTTON_GUIDE: 'ps',
    P.CONTROLLER_BUTTON_DPAD_UP: 'up', P.CONTROLLER_BUTTON_DPAD_DOWN: 'down',
    P.CONTROLLER_BUTTON_DPAD_LEFT: 'left', P.CONTROLLER_BUTTON_DPAD_RIGHT: 'right',
}
AXES = {P.CONTROLLER_AXIS_LEFTX: ('left', 0), P.CONTROLLER_AXIS_LEFTY: ('left', 1),
        P.CONTROLLER_AXIS_RIGHTX: ('right', 0), P.CONTROLLER_AXIS_RIGHTY: ('right', 1)}
TRIGGERS = {P.CONTROLLER_AXIS_TRIGGERLEFT: 'l2', P.CONTROLLER_AXIS_TRIGGERRIGHT: 'r2'}

out = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
    os.path.dirname(os.path.abspath(__file__)), 'routes', 'analog_last.json')

sock = socket.create_connection(('127.0.0.1', PORT))
reply = sock.makefile('rb')


def send(msg):
    sock.sendall((json.dumps(msg) + '\n').encode())
    reply.readline()


pygame.init()
controller.init()
if controller.get_count() == 0:
    sys.exit('듀얼센스를 Mac에 연결해 주세요.')
pad = controller.Controller(0)
print(f'연결: {pad.name}. 터치패드 클릭 = 녹화 시작/끝. Ctrl+C로 종료')

sticks = {'left': [0.0, 0.0], 'right': [0.0, 0.0]}
trig_down = set()
rec = None


def log(ev):
    if rec is not None:
        ev['t'] = round(time.monotonic() - rec[0], 4)
        rec[1].append(ev)


def toggle_rec():
    global rec
    if rec is None:
        rec = [time.monotonic(), []]
        print('● 녹화 시작')
        return
    evs = rec[1]
    rec = None
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, 'w') as f:
        json.dump(evs, f)
    print(f'■ 녹화 끝: {len(evs)}개 입력, {evs[-1]["t"] if evs else 0:.1f}초 → {out}')


try:
    while True:
        for ev in pygame.event.get():
            if ev.type == P.CONTROLLERBUTTONDOWN and ev.button == TOUCHPAD:
                toggle_rec()
            elif ev.type in (P.CONTROLLERBUTTONDOWN, P.CONTROLLERBUTTONUP) and ev.button in BUTTONS:
                name = BUTTONS[ev.button]
                act = 'press' if ev.type == P.CONTROLLERBUTTONDOWN else 'release'
                send({'button': name, 'action': act})
                log({'b': name, 'a': act})
            elif ev.type == P.CONTROLLERAXISMOTION and ev.axis in AXES:
                which, k = AXES[ev.axis]
                v = ev.value / 32767
                v = 0.0 if abs(v) < STICK_DEAD else max(-1.0, min(1.0, v))
                st = sticks[which]
                if st[k] != v:
                    st[k] = v
                    send({'stick': which, 'x': st[0], 'y': st[1]})
                    log({'s': which, 'x': round(st[0], 3), 'y': round(st[1], 3)})
            elif ev.type == P.CONTROLLERAXISMOTION and ev.axis in TRIGGERS:
                name, down = TRIGGERS[ev.axis], ev.value / 32767 > 0.1
                if down != (name in trig_down):
                    (trig_down.add if down else trig_down.discard)(name)
                    act = 'press' if down else 'release'
                    send({'button': name, 'action': act})
                    log({'b': name, 'a': act})
        time.sleep(0.002)
except KeyboardInterrupt:
    if rec is not None:
        toggle_rec()
