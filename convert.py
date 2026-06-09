#!/usr/bin/env python3
"""CoinPoker 핸드 히스토리를 AI 분석용 포맷으로 변환하는 CLI 도구.

사용법:
    python3 convert.py hands.txt              # 마크다운(AI 분석용) 출력
    python3 convert.py hands.txt --format json
    python3 convert.py hands.txt -o out.md    # 파일로 저장
"""

import argparse
import json
import re
import sys
from dataclasses import dataclass, field


# ---------------------------------------------------------------------------
# 데이터 모델
# ---------------------------------------------------------------------------

@dataclass
class Player:
    seat: int
    name: str
    chips: float
    position: str = ""
    hole_cards: list = field(default_factory=list)


@dataclass
class Action:
    street: str          # preflop / flop / turn / river
    player: str
    verb: str            # folds, checks, calls, bets, raises, allin, posts...
    amount: float = 0.0  # 이 액션으로 추가 투입한 칩
    to_amount: float = 0.0  # raises X to Y 의 Y (스트리트 누적 기준)
    pot_before: float = 0.0  # 액션 직전 팟
    to_call: float = 0.0     # 액션 시점에 콜하려면 필요한 추가 칩


@dataclass
class Hand:
    hand_id: str = ""
    game: str = ""
    sb: float = 0.0
    bb: float = 0.0
    ante: float = 0.0
    datetime: str = ""
    tournament: str = ""
    tournament_id: str = ""
    table_max: int = 0
    button_seat: int = 0
    players: list = field(default_factory=list)
    actions: list = field(default_factory=list)
    board: dict = field(default_factory=dict)  # flop/turn/river -> [cards]
    showdown: list = field(default_factory=list)  # (player, cards, rank)
    winners: list = field(default_factory=list)   # (player, amount)
    total_pot: float = 0.0
    raw: str = ""


# ---------------------------------------------------------------------------
# 파서
# ---------------------------------------------------------------------------

NUM = r"[\d,]+(?:\.\d+)?"

RE_HEADER = re.compile(
    r"CoinPoker Hand #(\d+):\s*(\S+)\s*\((" + NUM + r")/(" + NUM + r")(?:/(" + NUM + r"))?\)\s*(.+)"
)
# 이름 캡처는 greedy(.+) — 토너 이름에 작은따옴표가 들어가도(예: "Lil' Kahuna")
# ID가 '(\d+)'로 숫자에 한정돼 있어 마지막 따옴표에서 정확히 멈춤
RE_TOURNEY = re.compile(r"Tournament '(.+)' '(\d+)'\s*(\d+)-max Seat #(\d+) is the button")
RE_SEAT = re.compile(r"Seat (\d+): (\S+) \((" + NUM + r") in chips\)")
RE_DEALT = re.compile(r"Dealt to (\S+)(?: \[([^\]]+)\])?")
RE_STREET = re.compile(r"\*\*\* (FLOP|TURN|RIVER) \*\*\*.*\[([^\]]+)\]\s*$")
RE_ACTION = re.compile(
    r"(\S+): (folds|checks|calls|bets|raises|posts ante|posts small blind|posts big blind|ALLIN|RETURN|shows)"
    r"(?:\s+(" + NUM + r"))?(?:\s+to\s+(" + NUM + r"))?(?:\s*\[([^\]]+)\])?(?:\s*\(([^)]+)\))?"
)
RE_COLLECTED = re.compile(r"(\S+) collected (" + NUM + r") from pot")
RE_TOTAL_POT = re.compile(r"Total pot (" + NUM + r")")
# 헤더의 게임 토큰만 가볍게 추출 (NLH / "PLO 5" 등). non-greedy로 블라인드 괄호 앞에서 멈춤
RE_GAME = re.compile(r"CoinPoker Hand #\d+:\s*(.+?)\s*\(")


def fnum(s):
    return float(s.replace(",", "")) if s else 0.0


def hand_game(raw):
    m = RE_GAME.match(raw.lstrip())
    return m.group(1) if m else ""


def is_excluded_game(raw):
    """NLH가 아닌 게임(PLO/오마하 등)이면 True — 이 앱은 NLH 토너먼트 전용."""
    g = hand_game(raw).upper()
    return "PLO" in g or "OMAHA" in g


def assign_positions(players, button_seat):
    """버튼 기준으로 포지션 부여. 버튼 다음 자리부터 SB, BB, UTG..."""
    seats = sorted(p.seat for p in players)
    n = len(seats)
    btn_idx = seats.index(button_seat)
    # 버튼 다음부터 SB, BB, 그 다음 얼리부터
    order = [seats[(btn_idx + 1 + i) % n] for i in range(n)]
    if n == 2:
        # 헤즈업: 버튼이 SB. order[0]은 버튼 다음 자리 = BB
        names = ["BB", "SB(BTN)"]
    else:
        post = ["SB", "BB"]
        rest_count = n - 3  # BTN 제외 나머지
        rest_names = {0: [], 1: ["UTG"], 2: ["UTG", "CO"], 3: ["UTG", "MP", "CO"]}.get(
            rest_count, ["UTG"] + [f"MP{i}" for i in range(1, rest_count - 1)] + ["CO"]
        )
        names = post + rest_names + ["BTN"]
    pos_by_seat = dict(zip(order, names))
    for p in players:
        p.position = pos_by_seat[p.seat]


def parse_hand(text):
    hand = Hand(raw=text.strip())
    street = "preflop"
    contrib = {}          # 핸드 전체 누적 투입 (팟 계산용)
    street_contrib = {}   # 현재 스트리트 투입 (to_call 계산용)
    players_by_name = {}

    def pot():
        return sum(contrib.values())

    def new_street(name):
        nonlocal street, street_contrib
        street = name
        street_contrib = {}

    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue

        m = RE_HEADER.match(line)
        if m:
            hand.hand_id, hand.game = m.group(1), m.group(2)
            hand.sb, hand.bb, hand.ante = fnum(m.group(3)), fnum(m.group(4)), fnum(m.group(5))
            hand.datetime = m.group(6).strip()
            continue

        m = RE_TOURNEY.match(line)
        if m:
            hand.tournament, hand.tournament_id = m.group(1), m.group(2)
            hand.table_max, hand.button_seat = int(m.group(3)), int(m.group(4))
            continue

        m = RE_SEAT.match(line)
        if m and not hand.board.get("_summary_started"):
            seat, name, chips = int(m.group(1)), m.group(2), fnum(m.group(3))
            if name not in players_by_name:
                p = Player(seat=seat, name=name, chips=chips)
                hand.players.append(p)
                players_by_name[name] = p
            continue

        if line.startswith("*** HOLE CARDS ***"):
            # 블라인드 투입 기록(street_contrib)은 유지해야 to_call이 정확함
            street = "preflop"
            continue
        if line.startswith("*** SUMMARY ***"):
            hand.board["_summary_started"] = True
            continue
        if line.startswith("*** SHOWDOWN ***"):
            continue

        m = RE_STREET.match(line)
        if m:
            name = m.group(1).lower()
            cards = m.group(2).split()
            hand.board[name] = cards
            new_street(name)
            continue

        m = RE_DEALT.match(line)
        if m and m.group(2):
            p = players_by_name.get(m.group(1))
            if p:
                p.hole_cards = m.group(2).split()
            continue

        m = RE_COLLECTED.match(line)
        if m:
            hand.winners.append((m.group(1), fnum(m.group(2))))
            continue

        m = RE_TOTAL_POT.match(line)
        if m:
            hand.total_pot = fnum(m.group(1))
            continue

        m = RE_ACTION.match(line)
        if m and not hand.board.get("_summary_started"):
            name, verb = m.group(1), m.group(2)
            amt, to_amt = fnum(m.group(3)), fnum(m.group(4))
            cards, rank = m.group(5), m.group(6)

            if verb == "shows":
                # 멀티웨이 올인 사이드팟: CoinPoker는 한 명이 여러 팟을 먹으면
                # collected 줄마다 shows를 반복 출력 → 플레이어당 1회만 기록
                if not any(s[0] == name for s in hand.showdown):
                    hand.showdown.append((name, cards.split() if cards else [], rank or ""))
                continue

            if name not in players_by_name:
                continue

            pot_before = pot()
            max_street = max(street_contrib.values(), default=0.0)
            my_street = street_contrib.get(name, 0.0)
            to_call = max(0.0, max_street - my_street)

            put_in = 0.0
            if verb.startswith("posts"):
                put_in = amt
            elif verb == "calls":
                put_in = amt
            elif verb == "bets":
                put_in = amt
            elif verb == "raises":
                put_in = to_amt - my_street
            elif verb == "ALLIN":
                put_in = amt  # 추가 투입분
                to_amt = my_street + amt
                verb = "allin"
            elif verb == "RETURN":
                # 언콜드 베팅 반환 (상대 올인이 더 작을 때 차액 돌려받음)
                put_in = -amt
                verb = "return"

            # 이미 올인한 플레이어 뒤에 명목 블라인드 줄이 더 찍히는 경우 등(앤티로 올인 →
            # 이후 'posts big blind' 줄), 시작 스택을 넘는 투입은 불가 — 남은 스택으로 클램프
            if put_in > 0:
                remaining = players_by_name[name].chips - contrib.get(name, 0.0)
                put_in = max(0.0, min(put_in, remaining))

            if put_in:
                contrib[name] = contrib.get(name, 0.0) + put_in
                if not verb.startswith("posts ante"):
                    street_contrib[name] = my_street + put_in

            hand.actions.append(Action(
                street=street, player=name, verb=verb, amount=put_in,
                to_amount=to_amt or street_contrib.get(name, 0.0),
                pot_before=pot_before, to_call=to_call,
            ))
            continue

    hand.board.pop("_summary_started", None)
    if hand.button_seat and hand.players:
        assign_positions(hand.players, hand.button_seat)
    return hand


def split_hands(text):
    """파일 텍스트를 핸드 단위로 분리."""
    parts = re.split(r"(?=CoinPoker Hand #)", text)
    return [p for p in parts if p.strip().startswith("CoinPoker Hand #")]


# ---------------------------------------------------------------------------
# 출력: AI 분석용 마크다운
# ---------------------------------------------------------------------------

def bb(hand, x):
    return f"{x / hand.bb:.1f}bb" if hand.bb else str(x)


def chips_str(x):
    return f"{x:,.0f}" if x == int(x) else f"{x:,.2f}"


def render_markdown(hand, hero="Hero"):
    out = []
    n = len(hand.players)
    out.append(f"## Hand #{hand.hand_id} — {hand.tournament or hand.game}")
    ante_s = f" ante {chips_str(hand.ante)}" if hand.ante else ""
    out.append(f"{hand.game} | Blinds {chips_str(hand.sb)}/{chips_str(hand.bb)}{ante_s} "
               f"| {n}-handed | {hand.datetime}")
    out.append("")

    out.append("**Players:**")
    hero_p = None
    for p in sorted(hand.players, key=lambda p: p.seat):
        mark = " ← **HERO**" if p.name == hero else ""
        if p.name == hero:
            hero_p = p
        out.append(f"- {p.position} {p.name}: {chips_str(p.chips)} ({bb(hand, p.chips)}){mark}")
    out.append("")

    if hero_p and hero_p.hole_cards:
        out.append(f"**Hero hole cards: [{' '.join(hero_p.hole_cards)}]** ({hero_p.position})")
        out.append("")

    pos = {p.name: p.position for p in hand.players}

    def label(name):
        return f"{pos.get(name, '?')} {'Hero' if name == hero else name}"

    streets = ["preflop", "flop", "turn", "river"]
    board_so_far = []
    for st in streets:
        acts = [a for a in hand.actions if a.street == st and not a.verb.startswith("posts")]
        if st != "preflop":
            cards = hand.board.get(st)
            if not cards:
                continue
            board_so_far = board_so_far + cards
        if not acts and st != "preflop":
            pot_now = next((a.pot_before for a in hand.actions if a.street == st), None)
            out.append(f"**{st.upper()}** [{' '.join(board_so_far)}]")
            out.append("- (no action)")
            out.append("")
            continue
        if not acts:
            continue

        header = f"**{st.upper()}**"
        if st != "preflop":
            header += f" [{' '.join(board_so_far)}]"
        header += f" (pot: {chips_str(acts[0].pot_before)} = {bb(hand, acts[0].pot_before)})"
        out.append(header)

        for a in acts:
            line = f"- {label(a.player)}"
            if a.verb == "folds":
                line += " folds"
            elif a.verb == "checks":
                line += " checks"
            elif a.verb == "calls":
                line += f" calls {chips_str(a.amount)} ({bb(hand, a.amount)})"
            elif a.verb == "bets":
                pct = f", {a.amount / a.pot_before * 100:.0f}% pot" if a.pot_before else ""
                line += f" bets {chips_str(a.amount)} ({bb(hand, a.amount)}{pct})"
            elif a.verb == "raises":
                line += f" raises to {chips_str(a.to_amount)} ({bb(hand, a.to_amount)})"
            elif a.verb == "allin":
                line += f" goes ALL-IN, total {chips_str(a.to_amount)} ({bb(hand, a.to_amount)})"
            elif a.verb == "return":
                line += f" takes back uncalled {chips_str(-a.amount)} ({bb(hand, -a.amount)})"

            if a.player == hero and a.verb in ("folds", "calls", "raises", "allin", "checks", "bets"):
                extra = ""
                if a.to_call > 0:
                    pot_if_call = a.pot_before + a.to_call
                    odds = a.to_call / pot_if_call * 100
                    extra = (f" ← **HERO DECISION** (to call {chips_str(a.to_call)}, "
                             f"pot {chips_str(a.pot_before)}, pot odds {odds:.0f}%)")
                else:
                    extra = " ← **HERO DECISION**"
                line += extra
            out.append(line)
        out.append("")

    if hand.showdown:
        out.append("**SHOWDOWN**")
        for name, cards, rank in hand.showdown:
            out.append(f"- {label(name)}: [{' '.join(cards)}] — {rank}")
        out.append("")

    out.append("**RESULT**")
    full_board = []
    for st in ("flop", "turn", "river"):
        full_board += hand.board.get(st, [])
    if full_board:
        out.append(f"- Board: [{' '.join(full_board)}]")
    for name, amount in hand.winners:
        out.append(f"- {label(name)} wins {chips_str(amount)} ({bb(hand, amount)})")
    if hand.total_pot:
        out.append(f"- Total pot: {chips_str(hand.total_pot)} ({bb(hand, hand.total_pot)})")

    # Hero 손익
    hero_invested = sum(a.amount for a in hand.actions if a.player == hero)
    hero_won = sum(amt for nm, amt in hand.winners if nm == hero)
    net = hero_won - hero_invested
    sign = "+" if net >= 0 else ""
    out.append(f"- Hero net: {sign}{chips_str(net)} ({sign}{bb(hand, net)})")
    out.append("")
    return "\n".join(out)


# ---------------------------------------------------------------------------
# 핸드 메타데이터 (GUI/DB 용 파생 데이터)
# ---------------------------------------------------------------------------

def hand_meta(h, hero="Hero"):
    hero_p = next((p for p in h.players if p.name == hero), None)
    invested = sum(a.amount for a in h.actions if a.player == hero)
    won = sum(amt for n, amt in h.winners if n == hero)
    net = won - invested
    hero_actions = [a for a in h.actions if a.player == hero and not a.verb.startswith("posts")]
    hero_acts = [a.verb for a in hero_actions]
    went_showdown = any(n == hero for n, _, _ in h.showdown)
    # 자발적 액션 없이 프리플랍에서 폴드만 한 핸드 (앤티/블라인드만 내고 폴드)
    # BB 워크(액션 없이 팟 획득)는 폴드가 아니므로 제외하지 않음
    no_action_fold = (
        all(a.verb == "folds" and a.street == "preflop" for a in hero_actions) and won == 0
    )
    # 프리플랍 자발적 레이즈/올인 = PFR (통계용)
    pfr = any(
        a.street == "preflop" and a.verb in ("raises", "allin") for a in hero_actions
    )
    # 프리플랍 히어로의 첫 자발적 액션 분석:
    #  rfi_opp = 오픈 기회(폴드 투 히어로, 앞에 콜·레이즈 없음)
    #  rfi     = 그 기회에 첫 레이즈했는지 (솔버 오픈 차트와 동일, rfi/rfi_opp로 봄)
    #  pf_action = 첫 자발적 액션 분류 (open/3bet/call/allin/fold) — 액션 구성 스택바용
    #   · open = 앞 레이즈 없이 첫 레이즈(리밋 위 이졸 포함) / 3bet = 레이즈에 맞서 레이즈
    rfi_opp = rfi = False
    pf_action = "fold"
    prior_raise = prior_vol = False
    for a in h.actions:
        if a.street != "preflop":
            break
        if a.verb.startswith("posts"):
            continue                                  # 블라인드/앤티는 자발적 액션 아님
        if a.player == hero:
            rfi_opp = not prior_vol                   # 폴드 투 히어로면 오픈 기회
            if a.verb in ("raises", "bets"):
                pf_action = "3bet" if prior_raise else "open"
            elif a.verb == "allin":
                pf_action = "allin"
            elif a.verb == "calls":
                pf_action = "call"
            rfi = rfi_opp and a.verb in ("raises", "allin")
            break
        if a.verb in ("raises", "allin"):
            prior_raise = True
        if a.verb in ("calls", "bets", "raises", "allin"):
            prior_vol = True                          # 앞에 자발적 참여(콜/레이즈)가 있었음
    # 핸드 시작 시 히어로 스택(bb) — 스택 깊이 필터용
    stack_bb = round(hero_p.chips / h.bb, 1) if hero_p and h.bb else None
    net_bb = round(net / h.bb, 1) if h.bb else None
    # 복기 추천 사유 (휴리스틱) — 비어있지 않으면 복기 추천 대상
    review = []
    if net_bb is not None and net_bb <= -10:
        review.append(f"큰 손실 {net_bb}bb")
    if went_showdown and net < 0:
        review.append("쇼다운 패배")
    if "allin" in hero_acts and net < 0:
        review.append("올인 패배")
    return {
        "hand_id": h.hand_id,
        "datetime": h.datetime,
        "blinds": f"{h.sb:g}/{h.bb:g}" + (f"/{h.ante:g}" if h.ante else ""),
        "players": len(h.players),
        "hero_pos": hero_p.position if hero_p else None,
        "hero_cards": hero_p.hole_cards if hero_p else [],
        "net": net,
        "net_bb": net_bb,
        "vpip": any(v in ("calls", "bets", "raises", "allin") for v in hero_acts),
        "pfr": pfr,
        "rfi": rfi,
        "rfi_opp": rfi_opp,
        "pf_action": pf_action,
        "stack_bb": stack_bb,
        "showdown": went_showdown,
        "no_action_fold": no_action_fold,
        "review": review,
        "markdown": render_markdown(h, hero=hero),
    }


# ---------------------------------------------------------------------------
# 출력: JSON
# ---------------------------------------------------------------------------

def render_json(hand):
    return {
        "hand_id": hand.hand_id,
        "game": hand.game,
        "tournament": hand.tournament,
        "tournament_id": hand.tournament_id,
        "datetime": hand.datetime,
        "blinds": {"sb": hand.sb, "bb": hand.bb, "ante": hand.ante},
        "players": [
            {"seat": p.seat, "name": p.name, "position": p.position,
             "chips": p.chips, "chips_bb": round(p.chips / hand.bb, 1) if hand.bb else None,
             "hole_cards": p.hole_cards or None}
            for p in sorted(hand.players, key=lambda p: p.seat)
        ],
        "actions": [
            {"street": a.street, "player": a.player, "action": a.verb,
             "amount": a.amount, "to_amount": a.to_amount,
             "pot_before": a.pot_before, "to_call": a.to_call}
            for a in hand.actions if not a.verb.startswith("posts")
        ],
        "board": {k: v for k, v in hand.board.items()},
        "showdown": [{"player": n, "cards": c, "rank": r} for n, c, r in hand.showdown],
        "winners": [{"player": n, "amount": a} for n, a in hand.winners],
        "total_pot": hand.total_pot,
    }


# ---------------------------------------------------------------------------
# 토너먼트 그룹핑 / 인터랙티브 모드
# ---------------------------------------------------------------------------

def group_by_tournament(hands):
    """토너먼트 ID 기준으로 그룹핑. (id, name, hands) 리스트를 등장 순서대로 반환."""
    groups = {}
    order = []
    for h in hands:
        key = h.tournament_id or "(cash)"
        if key not in groups:
            groups[key] = {"name": h.tournament or h.game, "hands": []}
            order.append(key)
        groups[key]["hands"].append(h)
    return [(k, groups[k]["name"], groups[k]["hands"]) for k in order]


def tournament_summary_line(idx, tid, name, hands):
    times = [h.datetime for h in hands if h.datetime]
    span = ""
    if times:
        start, end = times[0], times[-1]
        # "2026/06/04 22:44:39 KST" → 날짜는 한 번만, 끝은 시:분만
        span = f" | {start[:16]} ~ {end[11:16]}" if len(end) > 16 else f" | {start}"
    return f"[{idx}] {name} (#{tid}) — 핸드 {len(hands)}개{span}"


def render_hands(hands, fmt, hero):
    if fmt == "json":
        return json.dumps([render_json(h) for h in hands], ensure_ascii=False, indent=2)
    return "\n---\n\n".join(render_markdown(h, hero=hero) for h in hands)


def interactive(hands, fmt, hero):
    tournaments = group_by_tournament(hands)
    while True:
        print("\n=== 토너먼트 목록 ===")
        for i, (tid, name, ths) in enumerate(tournaments, 1):
            print(tournament_summary_line(i, tid, name, ths))
        choice = input("\n토너먼트 번호 선택 (q 종료): ").strip().lower()
        if choice == "q":
            return
        if not choice.isdigit() or not (1 <= int(choice) <= len(tournaments)):
            print("잘못된 입력입니다.")
            continue

        tid, name, ths = tournaments[int(choice) - 1]
        result = render_hands(ths, fmt, hero)
        print(f"\n{'=' * 60}")
        print(f"{name} (#{tid}) — {len(ths)}개 핸드")
        print("=" * 60 + "\n")
        print(result)

        while True:
            cmd = input("\n[s] 파일로 저장  [b] 토너먼트 목록  [q] 종료: ").strip().lower()
            if cmd == "s":
                ext = "json" if fmt == "json" else "md"
                default = f"tournament_{tid}.{ext}"
                path = input(f"저장 파일명 (엔터 = {default}): ").strip() or default
                with open(path, "w", encoding="utf-8") as f:
                    f.write(result)
                print(f"저장 완료 → {path}")
            elif cmd == "b":
                break
            elif cmd == "q":
                return


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description="CoinPoker 핸드 히스토리 → AI 분석용 포맷 변환")
    ap.add_argument("input", nargs="+", help="핸드 히스토리 텍스트 파일 (여러 개 가능)")
    ap.add_argument("--format", choices=["md", "json"], default="md")
    ap.add_argument("--hero", default="Hero", help="히어로 플레이어 이름 (기본: Hero)")
    ap.add_argument("-o", "--output", help="출력 파일 (지정 시 비대화형으로 전체/선택 토너 변환)")
    ap.add_argument("--list", action="store_true", help="토너먼트 목록만 출력하고 종료")
    ap.add_argument("--tournament", metavar="ID",
                    help="해당 토너먼트 ID의 핸드만 비대화형으로 변환")
    ap.add_argument("--all", action="store_true", help="전체 핸드를 비대화형으로 변환")
    args = ap.parse_args()

    text = ""
    for path in args.input:
        with open(path, encoding="utf-8") as f:
            text += f.read() + "\n"

    hands = [parse_hand(h) for h in split_hands(text)]
    if not hands:
        print("핸드를 찾지 못했습니다. 'CoinPoker Hand #' 로 시작하는 로그인지 확인하세요.",
              file=sys.stderr)
        sys.exit(1)

    # --list: 토너먼트 목록만
    if args.list:
        for i, (tid, name, ths) in enumerate(group_by_tournament(hands), 1):
            print(tournament_summary_line(i, tid, name, ths))
        return

    # 비대화형 모드: --tournament / --all / -o 지정 시
    if args.tournament or args.all or args.output:
        selected = hands
        if args.tournament:
            selected = [h for h in hands if h.tournament_id == args.tournament]
            if not selected:
                print(f"토너먼트 #{args.tournament} 핸드가 없습니다. "
                      f"--list 로 ID를 확인하세요.", file=sys.stderr)
                sys.exit(1)
        result = render_hands(selected, args.format, args.hero)
        if args.output:
            with open(args.output, "w", encoding="utf-8") as f:
                f.write(result)
            print(f"{len(selected)}개 핸드 변환 완료 → {args.output}", file=sys.stderr)
        else:
            print(result)
        return

    # 기본: 인터랙티브 모드
    try:
        interactive(hands, args.format, args.hero)
    except (EOFError, KeyboardInterrupt):
        print()


if __name__ == "__main__":
    main()
