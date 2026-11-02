"""녹화한 한 바퀴를 반복 재생한다 (ps5view.py가 켜져 있어야 함).

  uv run python -u farm.py [반복 횟수=0(무한)] [녹화 파일=routes/analog_last.json] [귀환 후 대기초=1.5]

매 바퀴 전에 '겨우살이에서 휴식한다' 문구가 뜰 때까지(=귀환이 끝나 조작 가능) 기다렸다가 재생한다.
녹화 때와 같은 시작 상태를 맞추기 위해서다. Ctrl+C로 멈춘다.
"""
import os
import re
import sys
import tempfile
import time

import cv2

from ocr import read_text
from pscmd import PS

PROMPT_MIN = 400     # 720p 화면 기준, 가운데 아래 상호작용 문구 상자의 밝은 픽셀 수
GOAL = 400           # 이만큼 모이면 PS5를 휴식 모드로 보내고 끝낸다
MARK_OFFSET = 1      # 게임 안 실제 개수와 매크로 카운트의 차이 (사용자 확인: 매크로 185일 때 실제 186)
START_DELAY = 1.5    # 겨우살이 문구가 뜬 뒤 캐릭터가 일어나 조작이 돌아올 때까지 기다리는 시간
WATCH_FROM = 9.0     # 게이트를 연 뒤 이때부터 오른쪽 아이템 획득 알림을 읽는다 (처치는 약 11초 뒤)
BUFFS = (('밤 안개의 옷', 'cross', 6), ('블랙 샤우트', 'square', 4))   # (이름, R2와 같이 누를 버튼, 명혈 소모)
LOG = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'routes', 'farm_log.txt')


def in_game(img):
    """코드베인 게임 화면인지: 왼쪽 아래 HP바가 빨갛게 보이면 게임 중 (PS5 홈·메뉴·로딩 화면은 0)."""
    h, w = img.shape[:2]
    roi = img[int(h * 0.855):int(h * 0.885), int(w * 0.07):int(w * 0.28)]
    hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
    red = ((hsv[:, :, 0] < 10) | (hsv[:, :, 0] > 170)) & (hsv[:, :, 1] > 120) & (hsv[:, :, 2] > 90)
    return red.mean() > 0.3


def prompt_visible(img):
    h, w = img.shape[:2]
    roi = img[int(h * 0.63):int(h * 0.75), int(w * 0.38):int(w * 0.62)]
    return int((cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY) > 200).sum()) > PROMPT_MIN


def pickups(img):
    """오른쪽 위 아이템 획득 알림에서 (이름, 개수) 목록을 읽는다."""
    h, w = img.shape[:2]
    roi = img[int(h * 0.20):int(h * 0.55), int(w * 0.62):w]
    roi = cv2.resize(roi, None, fx=2, fy=2)
    ok, buf = cv2.imencode('.png', roi)
    found = []
    for line in read_text(buf.tobytes()):
        m = re.search(r'[x×X]\s*(\d+)\s*$', line)
        name = re.sub(r'[x×X]\s*\d+\s*$', '', line).strip()
        # 미니맵·귀환 창 등에서 잡힌 글자 조각은 버린다 (아이템 이름은 한글 2자 이상)
        if len(re.findall(r'[가-힣]', name)) >= 2 and not name.endswith('?'):
            found.append((name, int(m.group(1)) if m else 1))
    return found


def center_text(img):
    h, w = img.shape[:2]
    roi = cv2.resize(img[int(h * 0.25):int(h * 0.75), int(w * 0.2):int(w * 0.8)], None, fx=1.5, fy=1.5)
    ok, buf = cv2.imencode('.png', roi)
    return ' '.join(read_text(buf.tobytes()))


def ps_home(img):
    """PS5 홈 화면·제어 센터인지 (게임에서 빠져나온 상태). 홈의 '게임 플레이하기' 버튼이나 오른쪽 위 시계로 판정
    (코드베인 화면에는 시계가 없다)."""
    h, w = img.shape[:2]
    for roi in (img[int(h * 0.7):int(h * 0.9), 0:int(w * 0.4)], img[0:int(h * 0.12), int(w * 0.8):w]):
        ok, buf = cv2.imencode('.png', roi)
        for line in read_text(buf.tobytes()):
            if '플레이하기' in line or re.search(r'\d{1,2}:\d\d\s*(AM|PM|오전|오후)', line):
                return True
    return False


def palette_open(img):
    """아이템 팔레트 편집 창이나 아이템 선택 칸('팔레트 편집 △' 안내)이 열려 있는지."""
    h, w = img.shape[:2]
    roi = cv2.resize(img[int(h * 0.4):int(h * 0.85), int(w * 0.15):int(w * 0.85)], None, fx=1.5, fy=1.5)
    ok, buf = cv2.imencode('.png', roi)
    # 작은 안내 글자는 '파레드 면집'처럼 틀리게 읽히기도 해서 '편집' 쪽 오독까지 받아 준다
    return any(re.search(r'팔레트|파레|편집|면집', line) for line in read_text(buf.tobytes()))


def back_to_game(ps, screen):
    """홈 화면에서 게임으로 돌아간다. PS 버튼은 홈에서 제어 센터만 열어서 쓰지 않는다:
    ○로 열린 창을 닫으면 포커스가 실행 중인 코드베인 타일에 있고, ×를 누르면 게임이 이어진다."""
    for _ in range(3):
        if not ps_home(screen()):
            break
        print('PS5 홈 화면이라 게임으로 복귀 시도 (○ 다음 ×)')
        ps.button('circle')
        time.sleep(1.5)
        ps.button('cross')
        time.sleep(4)
    return in_game(screen())


def prompt_text(img):
    """가운데 아래 상호작용 문구를 글자로 읽는다 (겨우살이 문구와 게이트 문구 구분용)."""
    h, w = img.shape[:2]
    roi = cv2.resize(img[int(h * 0.63):int(h * 0.75), int(w * 0.38):int(w * 0.62)], None, fx=2, fy=2)
    ok, buf = cv2.imencode('.png', roi)
    return ' '.join(read_text(buf.tobytes()))


def ichor(img):
    """오른쪽 아래 명혈 수치(예: 24/30)의 현재 값. 못 읽으면 None."""
    h, w = img.shape[:2]
    roi = cv2.resize(img[int(h * 0.86):int(h * 0.93), int(w * 0.70):int(w * 0.82)], None, fx=3, fy=3)
    ok, buf = cv2.imencode('.png', roi)
    for line in read_text(buf.tobytes()):
        m = re.search(r'(\d+)\s*/\s*(\d+)', line)
        if m:
            return int(m.group(1))
    return None


def cast_buffs(ps, screen):
    """낙하 중에 버프를 건다. 누른 뒤 명혈이 줄었는지 보고, 안 줄었으면(입력이 씹힘) 한 번 더 누른다."""
    time.sleep(2.8)                 # 게이트 열리는 연출·낙하 초반에는 연혈 입력이 안 먹는다
    notes = []
    try:
        for name, btn, cost in BUFFS:
            before = ichor(screen())
            done = False
            for attempt in range(3):
                ps.button('r2', 'release')  # R2 누름이 씹혔을 수도 있어서 매번 새로 누른다
                ps.button('r2', 'press')
                time.sleep(0.15)
                ps.button(btn)
                t0 = time.monotonic()
                while time.monotonic() - t0 < 0.9:
                    now = ichor(screen())
                    if before is None or (now is not None and now <= before - cost):
                        done = True
                        break
                    time.sleep(0.05)
                if done:
                    if attempt:
                        notes.append(f'{name} {attempt + 1}번째에 걸림')
                    break
            if not done:
                notes.append(f'{name} 안 걸림')
    finally:
        ps.button('r2', 'release')
        ps.button('r2', 'release')
    return notes


def gate_visible(img):
    return '하층' in prompt_text(img)


DEBUG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'routes', 'debug')


def small_jpg(path, img):
    """확인용 캡처는 작게 (640px JPEG, 장당 약 40KB)."""
    cv2.imwrite(path, cv2.resize(img, (640, 360)), [cv2.IMWRITE_JPEG_QUALITY, 75])


def save_debug(img, why, keep=200):
    os.makedirs(DEBUG_DIR, exist_ok=True)
    small_jpg(os.path.join(DEBUG_DIR, f'{time.strftime("%m%d_%H%M%S")}_{why}.jpg'), img)
    old = sorted(os.listdir(DEBUG_DIR))[:-keep]     # 최근 200장만 남긴다
    for f in old:
        os.remove(os.path.join(DEBUG_DIR, f))


HOLDABLE = ('r2', 'l2', 'r1', 'l1', 'r3', 'l3', 'cross', 'circle', 'square', 'triangle',
            'up', 'down', 'left', 'right')


def release_all(ps):
    """'뗐다' 신호가 네트워크에서 빠지면 버튼이 눌린 채로 남는다(R2 팔레트가 열린 채 등).
    모든 버튼을 두 번씩 확실히 떼고 스틱도 가운데로 돌린다."""
    for _ in range(2):
        for b in HOLDABLE:
            ps.button(b, 'release')
        ps.stick('left', 0, 0)
        ps.stick('right', 0, 0)
        time.sleep(0.05)


def safe_warp(ps, screen, tries=5):
    """겨우살이 뿌리로 귀환. ↓ 뒤에 확인창이 실제로 뜬 걸 글자로 확인한 다음에만 ↑ ×를 누른다.
    확인창이 안 뜨면(맞아서 경직 중 등 아이템을 못 쓰는 순간) 잠시 뒤 다시 시도한다."""
    for k in range(tries):
        img = screen()
        if not in_game(img):
            return False
        release_all(ps)
        # ↓ 떼기 신호가 빠지면 길게 누른 걸로 쳐서 아이템 팔레트 편집 창이 열린다.
        # ○ 한 번이면 편집 창만 닫히고 아이템 선택 칸('팔레트 편집 △' 안내)이 남으므로 글자가 사라질 때까지 닫는다.
        # 글자 읽기가 느려서 첫 시도에서는 건너뛰고, 확인창이 안 떠서 다시 할 때만 본다
        for _ in range(4 if k else 0):
            if not palette_open(screen()):
                break
            print('아이템 팔레트 창이 열려 있어서 ○로 닫음')
            ps.button('circle')
            time.sleep(1.0)
        time.sleep(0.15)
        ps.button('down')
        for _ in range(6):
            time.sleep(0.25)
            if '돌아갈' in center_text(screen()):
                ps.button('up')
                time.sleep(0.35)
                ps.button('cross')
                return True
        save_debug(screen(), f'warp_fail{k + 1}')
        time.sleep(1.5)
    return False


def split_route(events):
    """녹화를 게이트 열기(첫 ×) 앞뒤로 나눈다. 게이트는 화면을 보고 직접 연다."""
    i = next((k for k, e in enumerate(events) if e.get('b') == 'cross' and e['a'] == 'press'), None)
    if i is None:
        return events, None
    j = next(k for k in range(i, len(events)) if events[k].get('b') == 'cross' and events[k]['a'] == 'release')
    t0 = events[j]['t']
    before = events[:i]
    # 녹화 앞쪽의 가만히 서 있던 시간(약 2.4초)은 START_DELAY와 겹치므로 0.4초만 남긴다
    lead = max(0.0, before[0]['t'] - 0.4) if before else 0.0
    before = [dict(e, t=round(e['t'] - lead, 4)) for e in before]
    after = [dict(e, t=round(e['t'] - t0, 4)) for e in events[j + 1:]]
    # 끝부분의 녹화된 귀환(↓ ↑ ×)은 뺀다. 확인창이 안 뜬 채로 ↑ ×가 눌리면 단축 아이템이 바뀌거나
    # 메뉴가 열리기 때문에, 귀환은 safe_warp()가 화면을 보면서 따로 한다.
    k = next((m for m in range(len(after)) if after[m].get('b') == 'down' and after[m]['a'] == 'press'), None)
    if k is not None:
        after = after[:k]
    # 녹화된 버프 입력(R2를 누른 채 × × □, 첫 R2를 뗄 때까지)도 뺀다. cast_buffs()가 화면을 보며 건다.
    r = next((m for m in range(len(after)) if after[m].get('b') == 'r2' and after[m]['a'] == 'release'), None)
    if r is not None:
        t1 = after[r]['t']
        after = [dict(e, t=round(e['t'] - t1, 4)) for e in after[r + 1:]]
    return before, after


def main():
    count = int(sys.argv[1]) if len(sys.argv) > 1 else 0
    route = sys.argv[2] if len(sys.argv) > 2 else 'routes/analog_last.json'
    delay = float(sys.argv[3]) if len(sys.argv) > 3 else START_DELAY
    ps = PS()
    snap = os.path.join(tempfile.gettempdir(), 'ps5_farm_snap.png')

    def screen():
        ps.snap(snap)
        return cv2.imread(snap)

    def playing():
        ps.sock.sendall(b'{"status": true}\n')
        return ps.r.readline().decode().strip() == 'ok playing'

    # 다시 켜도 이어서 세도록 기록 파일의 마지막 바퀴 번호와 누적 개수를 가져온다
    n, total = 0, 0
    skips = 0           # 연속으로 건너뛴 바퀴 수
    if os.path.exists(LOG):
        for old in open(LOG, encoding='utf-8'):
            m = re.search(r' (\d+)바퀴 .*누적 (\d+)\)', old)
            if m:
                n, total = int(m.group(1)), int(m.group(2))
    start_n = n
    import json
    before, after = split_route(json.load(open(route)))
    part_a = os.path.join(tempfile.gettempdir(), 'ps5_farm_a.json')
    part_b = os.path.join(tempfile.gettempdir(), 'ps5_farm_b.json')
    json.dump(before, open(part_a, 'w'))
    json.dump(after, open(part_b, 'w'))
    try:
        while count == 0 or n - start_n < count:
            # 겨우살이 앞이 아니면(재생이 끊겨 중간에 멈춘 경우 등) 겨우살이 뿌리로 귀환을 시도한다.
            # 죽었다면 부활 지점이 겨우살이라 그냥 기다리면 문구가 뜬다.
            t_wait = t_ret = time.monotonic()
            warped = False
            homes = 0           # 이번 대기 중 홈 화면 복귀 시도 횟수
            while True:
                img = screen()
                # 밝기만 보면 귀환 확인창 등 다른 상자도 잡혀서 글자로 겨우살이 문구인지 확인한다
                if in_game(img) and prompt_visible(img) and '겨우살이' in prompt_text(img):
                    break
                waited = time.monotonic() - t_wait
                if not in_game(img) and waited > 3 and homes < 3 and ps_home(img):
                    homes += 1
                    back_to_game(ps, screen)
                    t_wait, warped = time.monotonic(), False   # 게임으로 돌아왔으면 처음부터 다시 기다린다
                    continue
                if waited > 8 and not warped and in_game(img):   # 게임 화면이 아니면 버튼을 절대 누르지 않는다
                    print('겨우살이 앞이 아니라서 귀환 시도:', '확인창 확인 후 귀환' if safe_warp(ps, screen) else '확인창이 안 떠서 안 누름')
                    warped = True
                if waited > 90:             # 죽었다가 겨우살이에서 부활하는 시간까지 넉넉하게
                    print('겨우살이 앞으로 돌아오지 못했어요 (게임 화면이 아니거나 꼬임). 멈춤')
                    sys.exit(2)
                time.sleep(0.2)
            release_all(ps)
            time.sleep(delay)
            n += 1
            t = time.monotonic()
            print(f'{n}바퀴 시작 (귀환 대기 {t - t_ret:.1f}초)')
            ps.play(part_a)
            time.sleep(0.3)
            while playing():
                time.sleep(0.05)
            # 게이트('하층으로 향한다') 문구가 보일 때까지 기다리고, 안 보이면 조금 더 뒤로 걸어 본다.
            # 게이트 문구 상자는 작고 어두워서 밝기(prompt_visible)로는 놓치므로 글자로만 판정한다.
            gate = False
            t_gate = time.monotonic()
            while time.monotonic() - t_gate < 3.0:
                img = screen()
                if in_game(img) and gate_visible(img):
                    gate = True
                    print(f'  게이트 문구까지 {time.monotonic() - t_gate:.1f}초, 재생 시작부터 {time.monotonic() - t:.1f}초')
                    break
                if time.monotonic() - t_gate > 1.5:
                    ps.stick('left', 0, 0.5)
                    time.sleep(0.12)
                    ps.stick('left', 0, 0)
            if not gate:
                save_debug(screen(), 'no_gate')
                skips += 1
                print(f'{n}바퀴: 게이트 지점에 못 닿아서 이번 바퀴는 건너뜀 ({skips}번 연속)')
                if skips >= 3:
                    print('3번 연속 실패: 화면이 멈췄거나 상황이 꼬였어요. 멈춤')
                    sys.exit(3)
                safe_warp(ps, screen)
                time.sleep(2)
                continue
            # ×를 눌렀는데도 게이트 문구가 그대로면 안 열린 것: 한 번 더, 그래도 안 되면 귀환 후 다시
            opened = False
            for _ in range(2):
                ps.button('cross')
                time.sleep(1.0)
                img = screen()
                if not gate_visible(img):
                    opened = True
                    break
            if not opened:
                skips += 1
                print(f'{n}바퀴: 게이트가 안 열려서 귀환 후 다시 시작 ({skips}번 연속)')
                if skips >= 3:
                    print('3번 연속 실패. 멈춤')
                    sys.exit(3)
                safe_warp(ps, screen)
                time.sleep(2)
                continue
            skips = 0
            t = time.monotonic()
            buff_notes = cast_buffs(ps, screen)
            ps.play(part_b)
            time.sleep(1)
            got = {}
            off_since = None
            lost = False
            while True:
                still = playing()
                img = screen()
                el = time.monotonic() - t
                if el < 12.0 and not in_game(img):     # 처치·귀환 전까지는 게임 화면이어야 함
                    off_since = off_since or time.monotonic()
                    if time.monotonic() - off_since > 2.5:
                        ps.stop()
                        release_all(ps)
                        print(f'{n}바퀴: 재생 중 게임 화면이 사라짐 (PS5 홈/메뉴로 나간 듯). 복귀 후 다시')
                        lost = True
                        break
                else:
                    off_since = None
                if el > WATCH_FROM:
                    for name, qty in pickups(img):
                        got[name] = max(got.get(name, 0), qty)   # 같은 알림이 여러 장에 걸쳐 보이므로 최대값
                        if '경애' in name and not os.path.exists(f'{LOG}.{n}.jpg'):
                            small_jpg(f'{LOG}.{n}.jpg', img)  # 확인용
                if not still:
                    break
                time.sleep(0.3)
            if lost:
                continue        # 맨 위 대기 단계에서 홈 화면이면 게임으로 복귀하고, 겨우살이로 돌아간다
            # 재생이 끝나면(처치 직후) 확인창을 보면서 귀환. 알림 읽기는 귀환 확인창 직전까지 이어진다
            img = screen()
            for name, qty in pickups(img):
                got[name] = max(got.get(name, 0), qty)
            time.sleep(0.9)    # 보드니크 시전 후딜 동안은 아이템을 못 쓴다 (녹화에서도 0.9초 뒤에 ↓)
            if not safe_warp(ps, screen):
                save_debug(screen(), 'lap_end_warp_fail')
                print(f'{n}바퀴: 귀환 실패 (경직·사망 등). 겨우살이로 돌아올 때까지 기다림')
            # 아이템 획득 알림은 귀환 확인창이 떠 있는 동안에도 보이므로 2초 더 읽는다
            t_read = time.monotonic()
            while time.monotonic() - t_read < 2.0:
                img = screen()
                for name, qty in pickups(img):
                    got[name] = max(got.get(name, 0), qty)
                    if '경애' in name and not os.path.exists(f'{LOG}.{n}.jpg'):
                        small_jpg(f'{LOG}.{n}.jpg', img)
            # 알림 한 줄이 '경애의'와 '경애의 표식'처럼 조각나 읽히기도 해서 합치지 않고 가장 큰 값만 센다
            marks = max((q for k, q in got.items() if '경애' in k), default=0)
            total += marks
            line = (f'{time.strftime("%H:%M:%S")} {n}바퀴 ({time.monotonic() - t:.1f}초) '
                    f'경애의 표식 +{marks} (누적 {total}) 획득: {got or "-"}'
                    + (f' [{", ".join(buff_notes)}]' if buff_notes else ''))
            print(line)
            try:
                ps._send({'osd': f'{n}바퀴  경애의 표식 약 {total + MARK_OFFSET}개 (남은 {max(0, GOAL - total - MARK_OFFSET)}개)'})
            except Exception:   # noqa: BLE001 - 표시 실패는 파밍에 영향 없음
                pass
            with open(LOG, 'a') as f:
                f.write(line + '\n')
            if total + MARK_OFFSET >= GOAL:
                print(f'{GOAL}개 달성. 귀환이 끝나면 PS5를 휴식 모드로 보내요')
                time.sleep(10)     # 귀환·로딩이 끝나 겨우살이에서 저장될 때까지
                ps.standby()
                sys.exit(4)
            time.sleep(0.3)        # 귀환 연출이 시작될 때까지 (대기 쪽에서 겨우살이 문구를 글자로 확인한다)
    except KeyboardInterrupt:
        ps.stop()
        print('\n멈춤')


if __name__ == '__main__':
    main()
