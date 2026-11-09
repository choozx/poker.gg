"""핵심 회귀 테스트 — 표준 라이브러리 unittest만 쓴다 (프로젝트 규칙).

    python3 -m unittest discover -s tests        # 레포 루트에서

실제 DB(hands_db.json)는 건드리지 않는다. sample_hand.txt와 테스트 안에서 만든 dict만 쓴다.
여기 있는 것들은 대부분 **실제로 한 번 났던 버그**다 — 고칠 때 같은 실수를 다시 하지 않게 남긴다.
"""
import os
import sys
import tempfile
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import coach      # noqa: E402
import convert    # noqa: E402
import grab_chart  # noqa: E402
import ranges     # noqa: E402
import store      # noqa: E402
import sync_charts  # noqa: E402

SAMPLE = open(os.path.join(ROOT, "sample_hand.txt"), encoding="utf-8").read()


def sample_with_preflop(lines):
    """샘플 핸드의 프리플랍 액션만 바꾼 핸드 텍스트 (3인: BTN 084a1d59 / SB Hero / BB 78c4f548)."""
    out = SAMPLE.splitlines()
    i = next(k for k, l in enumerate(out) if l.startswith("084a1d59: raises"))
    tail = ["78c4f548 collected 2,100 from pot", "*** SUMMARY ***", "Total pot 2,100"]
    return "\n".join(out[:i] + lines + tail)


def meta(raw, hero):
    return convert.hand_meta(convert.parse_hand(raw), hero=hero)


# ---------------------------------------------------------------------------
# 파서 — 얼려 두는 메타 필드
# ---------------------------------------------------------------------------

class TestHandMeta(unittest.TestCase):
    def test_single_open_raise(self):
        m = meta(SAMPLE, "Hero")                       # BTN 레이즈 → SB(Hero)
        self.assertEqual((m["pf_faced"], m["pf_opener"], m["pf_opener_allin"]), ("raise", "BTN", False))
        self.assertIsNone(m["pf_limper"])

    def test_open_allin(self):
        raw = sample_with_preflop(["084a1d59: ALLIN 11,058", "Hero: folds", "78c4f548: folds"])
        m = meta(raw, "78c4f548")                      # BB 시점: BTN 오픈 올인을 받음
        self.assertEqual((m["pf_opener"], m["pf_opener_allin"]), ("BTN", True))

    def test_sb_limp_to_bb(self):
        raw = sample_with_preflop(["084a1d59: folds", "Hero: calls 175",
                                   "78c4f548: raises 1,400 to 1,750", "Hero: folds"])
        m = meta(raw, "78c4f548")
        self.assertEqual((m["pf_faced"], m["pf_limper"], m["pf_opener"]), ("limp", "SB", None))
        self.assertEqual(m["pf_action"], "open")       # 림프에 대한 레이즈 = 아이솔 (리포트가 레이즈로 센다)

    def test_raise_after_caller_is_not_single_open(self):
        # 오픈 + 콜러가 있으면 방어 차트가 전제하는 상황이 아니다 → pf_opener None
        raw = sample_with_preflop(["084a1d59: raises 350 to 700", "Hero: calls 525"])
        self.assertIsNone(meta(raw, "78c4f548")["pf_opener"])


    def test_effective_stack_is_smaller_of_opener_and_hero(self):
        # 방어 차트는 내 스택이 아니라 유효 스택으로 고른다 — BTN 11,058 / BB 20,122 / SB(Hero) 10,000, bb 350
        bb = meta(SAMPLE, "78c4f548")
        self.assertEqual((bb["stack_bb"], bb["pf_eff_bb"]), (57.5, 31.6))
        self.assertEqual(meta(SAMPLE, "Hero")["pf_eff_bb"], 28.6)      # 내가 더 작으면 내 스택
        self.assertIsNone(meta(SAMPLE, "084a1d59")["pf_eff_bb"])        # 오픈 스팟엔 상대가 없다

    def test_allin_below_one_bb_is_not_an_open(self):
        # 실제로 있던 핸드 모양: BB 800에 BTN이 95칩 올인 → '오픈 올인을 받음'으로 세면 안 된다
        raw = sample_with_preflop(["084a1d59: ALLIN 300", "Hero: folds", "78c4f548: RETURN 50"])
        m = meta(raw, "Hero")
        self.assertEqual((m["pf_faced"], m["pf_opener"], m["pf_limper"]), ("limp", None, None))
        self.assertIsNone(ranges.hand_spot(m))
        m = meta(sample_with_preflop(["084a1d59: ALLIN 400", "Hero: folds"]), "Hero")
        self.assertEqual((m["pf_opener"], m["pf_opener_allin"]), ("BTN", True))   # 1bb 넘으면 올인 오픈

    def test_return_is_not_a_decision(self):
        # BB 워크와, BB가 블라인드로 올인해 판이 끝난 SB — 반환 줄을 결정으로 읽어 '폴드'로 셌었다
        m = meta(sample_with_preflop(["084a1d59: folds", "Hero: folds", "78c4f548: RETURN 175"]), "78c4f548")
        self.assertIsNone(m["pf_faced"])
        self.assertFalse(m["rfi_opp"])
        raw = sample_with_preflop(["084a1d59: folds", "Hero: RETURN 75"]).replace(
            "78c4f548: posts big blind 350", "78c4f548: posts big blind 100 ALLIN")
        m = meta(raw, "Hero")
        self.assertIsNone(m["pf_faced"])
        self.assertIsNone(ranges.hand_spot(m))


# ---------------------------------------------------------------------------
# 차트 키 · 상대 종류
# ---------------------------------------------------------------------------

class TestVsKinds(unittest.TestCase):
    def test_vs_parts_and_norm(self):
        self.assertEqual(ranges.vs_parts("UTG"), ("UTG", "raise"))
        self.assertEqual(ranges.vs_parts("UTG-allin"), ("UTG", "allin"))
        self.assertEqual(ranges.vs_parts("SB-limp"), ("SB", "limp"))
        # 꼬리까지 대문자가 되면 키가 갈린다 — 포지션만 정규화
        self.assertEqual(ranges._norm_vs("utg+1-ALLIN"), "UTG1-allin")

    def test_parse_vs_rules(self):
        self.assertEqual(ranges.parse_vs("BB", None)[1] is not None, True)      # BB는 상대 필수
        self.assertEqual(ranges.parse_vs("BB", "CO"), ("CO", None))
        self.assertIsNotNone(ranges.parse_vs("CO", "BTN")[1])                   # 뒷자리는 오프너 불가
        self.assertIsNotNone(ranges.parse_vs("BB", "CO-limp")[1])               # 림프는 SB→BB만
        self.assertEqual(ranges.parse_vs("BB", "SB-limp"), ("SB-limp", None))

    def test_key_roundtrip(self):
        for pos, slot, vs in (("UTG", "20", None), ("BB", "13", "UTG-allin"), ("BB", "13", "SB-limp")):
            self.assertEqual(ranges._split_key(ranges._ckey(pos, slot, vs)), (pos, slot, vs))

    def test_allin_import_stores_everything_as_call(self):
        db = {"hands": {}}
        ranges.import_chart(db, "BB", "13", "AA:100,KK:100,AKs:50", vs="UTG-allin", jam="AA:100")
        rec = db["ranges"]["charts"]["BB|13|vsUTG-allin"]
        self.assertEqual(rec["call"], rec["weights"])                 # 빨강으로 읽혀도 전부 콜
        self.assertEqual(rec["jam"], {})
        q = self._question(db, "UTG-allin")
        self.assertEqual([c["id"] for c in q["choices"]], ["call", "fold"])

    def test_limp_import_folds_check_away(self):
        db = {"hands": {}}
        ranges.import_chart(db, "BB", "13", "AA:100,72o:100,KQo:100", vs="SB-limp", call="72o:100,KQo:60")
        w = db["ranges"]["charts"]["BB|13|vsSB-limp"]["weights"]
        self.assertEqual(w, {"AA": 1.0, "KQo": 0.4})                  # 초록(체크) 몫은 빠진다
        c = ranges.chart("BB", "13", db, "SB-limp")
        self.assertEqual((c["verb"], c["fold_name"]), ("레이즈", "체크"))
        q = self._question(db, "SB-limp")
        self.assertEqual([c["label"] for c in q["choices"]], ["레이즈(아이솔)", "체크"])
        g = ranges.grade(db, "BB", "pf", "72o", "open", record=False, vs="SB-limp")
        self.assertIn("체크", g["text"])

    @staticmethod
    def _question(db, vs):
        for _ in range(200):
            q = ranges.next_question(db, positions=["BB"])["question"]
            if q["vs"] == vs:
                return q
        raise AssertionError(f"{vs} 문제가 나오지 않음")


# ---------------------------------------------------------------------------
# 자리 환산 · 실전 기록 집계
# ---------------------------------------------------------------------------

def hand(pos, players, combo_cards, faced="none", rfi=False, **kw):
    r = {"hero_pos": pos, "players": players, "stack_bb": 20, "hero_cards": combo_cards,
         "pf_faced": faced, "rfi": rfi, "pf_opener": kw.pop("pf_opener", None),
         "tournament_id": "t1", "tournament_name": "테스트", "datetime": "2026/01/01 00:00:00"}
    r.update(kw)
    return r


def reset_caches():
    ranges._HERO_CACHE.update(n=None, data=None)
    ranges._HERO_SLOT_CACHE.update(key=None, data=None)
    ranges._SLOT_CACHE.update(key=None, index=None)


class TestNearestSlot(unittest.TestCase):
    def test_nearest_with_tolerance(self):
        self.assertEqual(ranges.nearest_slot([7, 10, 13], 12), 13)
        self.assertEqual(ranges.nearest_slot([7, 10, 13], 8.4), 7)
        self.assertEqual(ranges.nearest_slot([25, 28, 30, 32, 35], 40), 35)      # 35의 15% = 5.25 안
        self.assertIsNone(ranges.nearest_slot([25, 28, 30, 32, 35], 41))
        self.assertIsNone(ranges.nearest_slot([7, 10, 13], 3))                   # 7에서 4bb — 너무 멀다

    def test_13bb_hand_uses_13bb_chart_not_10bb(self):
        # 예전 버그: 버킷(<15bb)마다 차트 한 장(11bb에 가까운 10bb)만 써서 13bb 핸드도 10bb 차트로 쟀다
        db = {"hands": {}}
        ranges.import_chart(db, "CO", "10", "72o:100")          # 10bb: 72o 오픈
        ranges.import_chart(db, "CO", "13", "AA:100")           # 13bb: AA만 오픈
        reset_caches()
        r13 = dict(hand("CO", 7, ["7h", "2d"], rfi=True), stack_bb=13)
        r10 = dict(hand("CO", 7, ["7h", "2d"], rfi=True), stack_bb=10)
        self.assertEqual(ranges.hand_slot(db, r13), ("CO", None, 13))
        self.assertIn("13bb", ranges.hand_deviation(db, r13, {})["chart"])     # 13bb 차트로 이탈
        self.assertIsNone(ranges.hand_deviation(db, r10, {}))                  # 10bb 차트로는 정답


    def test_defense_uses_effective_stack(self):
        # 20bb인 내가 7bb 올인을 받으면 '7bb 올인을 받음' 차트 — 내 스택(20bb) 차트가 아니다
        db = {"hands": {}}
        for bb in ("7", "20"):
            ranges.import_chart(db, "BB", bb, "AA:100", vs="BTN-allin")
            ranges.import_chart(db, "BTN", bb, "AA:100")
        reset_caches()
        r = hand("BB", 7, ["Ah", "Ad"], faced="raise", pf_opener="BTN", pf_opener_allin=True,
                 stack_bb=20, pf_eff_bb=7)
        self.assertEqual(ranges.hand_slot(db, r), ("BB", "BTN-allin", 7))
        self.assertEqual(ranges.hand_deviation(db, dict(r, pf_action="fold"), {})["eff_bb"], 7)
        old = dict(r); del old["pf_eff_bb"]                     # 이 필드 전에 rebuild한 DB → 내 스택
        self.assertEqual(ranges.hand_slot(db, old), ("BB", "BTN-allin", 20))
        o = hand("BTN", 7, ["Ah", "Ad"], rfi=True, stack_bb=20, pf_eff_bb=7)
        self.assertEqual(ranges.hand_slot(db, o), ("BTN", None, 20))   # 오픈은 내 스택


class TestSeatsAndHero(unittest.TestCase):
    def test_pos_8max_by_players_behind(self):
        self.assertEqual(ranges._pos_8max("UTG", 7), "UTG1")
        self.assertEqual(ranges._pos_8max("UTG", 6), "LJ")
        self.assertEqual(ranges._pos_8max("MP1", 7), "LJ")
        self.assertEqual(ranges._pos_8max("UTG", 8), "UTG")
        self.assertEqual(ranges._pos_8max("CO", 6), "CO")

    def test_utg_not_mixed_across_schemes(self):
        # 실제로 났던 버그: 7인 UTG가 8맥스 UTG 차트에 섞였다 (8인 UTG 6번이 9,466번으로)
        db = {"hands": {str(i): hand("UTG", 7, ["Ah", "Kd"], rfi=True) for i in range(5)}}
        db["hands"]["x"] = hand("UTG", 8, ["Ah", "Kd"], rfi=False)
        for p in ("UTG", "UTG1"):
            ranges.import_chart(db, p, "20", "AKo:100")
        reset_caches()
        self.assertEqual(ranges.hero_cells(db, "UTG", slot=20)["AKo"], [0, 1, 0])        # 8인 UTG만
        self.assertEqual(ranges.hero_cells(db, "UTG1", slot=20)["AKo"], [5, 5, 5])       # 7인 UTG
        self.assertEqual(ranges.hero_cells(db, "UTG", bucket="short", builtin=True)["AKo"][:2], [5, 6])  # 내장=원래 이름

    def test_seats_off_and_contexts(self):
        self.assertEqual(ranges.seats_off(7), {"UTG"})
        self.assertEqual(ranges.seats_off(6), {"UTG", "UTG1"})
        db = {"hands": {}}
        for p in ("UTG", "UTG1", "LJ"):
            ranges.import_chart(db, p, "20", "22+,A2s+")
        ranges.import_chart(db, "BB", "20", "22+", vs="UTG", call="22:100")
        ctx = ranges._contexts(db, max_seats=7)
        self.assertFalse(any(p == "UTG" or ranges.vs_parts(v)[0] == "UTG" for p, _, v, _ in ctx))
        self.assertFalse(any(p == "MP" for p, _, _, _ in ctx))      # 가져온 LJ가 있으면 옛 MP는 뺀다


class TestPinnedSpot(unittest.TestCase):
    def test_vs_only_pins_one_situation(self):
        db = {"hands": {}}
        ranges.import_chart(db, "BB", "20", "22+", vs="CO", call="22:100")
        ranges.import_chart(db, "BB", "20", "AA", vs="CO-allin")
        ctx = ranges._contexts(db, positions=["BB"], stacks=["short"], vs_only="CO-allin")
        self.assertEqual([(p, s, v) for p, s, v, _ in ctx], [("BB", "20", "CO-allin")])   # 문제는 bb 차트 단위
        ctx = ranges._contexts(db, positions=["BB"], stacks=["20"], vs_only="CO")           # bb로 고정해도 같다
        self.assertEqual([(p, s, v) for p, s, v, _ in ctx], [("BB", "20", "CO")])
        ctx = ranges._contexts(db, positions=["CO"], stacks=["short"], vs_only="")   # 오픈 차트만
        self.assertTrue(ctx and all(v is None for _, _, v, _ in ctx))


class TestLeakReport(unittest.TestCase):
    def test_expected_is_weighted_by_dealt_combos(self):
        db = {"hands": {}}
        ranges.import_chart(db, "CO", "20", "AA:100,KK:100")       # AA·KK만 오픈
        db["hands"] = {
            "1": hand("CO", 7, ["Ah", "Ad"], rfi=True), "2": hand("CO", 7, ["Kh", "Kd"], rfi=False),
            **{f"t{i}": hand("CO", 7, ["7h", "2d"], rfi=False) for i in range(20)},
        }
        reset_caches()
        rows = ranges.leak_report(db, 7)["rows"]
        self.assertEqual(len(rows), 1)
        r = rows[0]
        self.assertEqual((r["n"], r["expected"], r["actual"]), (22, round(2 / 22 * 100, 1), round(1 / 22 * 100, 1)))
        self.assertEqual(r["wrong"], 1.0)                            # KK 한 번을 안 열었다


class TestHandDeviation(unittest.TestCase):
    def setUp(self):
        self.db = {"hands": {}}
        ranges.import_chart(self.db, "CO", "20", "AA:100,KK:100")              # AA·KK만 오픈
        ranges.import_chart(self.db, "BB", "20", "AA:100,KK:100,22:100", vs="CO", call="22:100")

    def dev(self, r):
        return ranges.hand_deviation(self.db, r, {})

    def test_open_spot(self):
        self.assertIsNone(self.dev(hand("CO", 7, ["Ah", "Ad"], rfi=True)))     # 차트대로 오픈
        d = self.dev(hand("CO", 7, ["Ah", "Ad"], rfi=False))                  # 오픈 100%인데 폴드
        self.assertEqual((d["did"], d["best"], d["best_pct"]), ("폴드", "오픈", 100))
        self.assertIsNotNone(self.dev(hand("CO", 7, ["7h", "2d"], rfi=True)))  # 폴드 칸을 오픈

    def test_defense_spot_and_annotate(self):
        call22 = hand("BB", 7, ["2h", "2d"], faced="raise", pf_opener="CO", pf_action="call",
                      pf_opener_allin=False)
        fold22 = dict(call22, pf_action="fold")
        self.assertIsNone(self.dev(call22))
        self.assertEqual(self.dev(fold22)["did"], "폴드")
        hands = [call22, fold22]
        self.assertEqual(ranges.annotate_deviations(self.db, hands), 1)
        self.assertIn("chart_dev", hands[1])

    def test_no_chart_for_bucket_no_flag(self):
        # 그 구간(40bb+)엔 가져온 차트가 없다 — 다른 구간 차트로 판정하지 않는다
        self.assertIsNone(self.dev(dict(hand("CO", 7, ["Ah", "Ad"], rfi=False), stack_bb=60)))


# ---------------------------------------------------------------------------
# 감시 모드 — 순서표 · 안내 · 저장 규칙
# ---------------------------------------------------------------------------

class TestWatchPlan(unittest.TestCase):
    def test_plan_counts_and_order(self):
        p = grab_chart.plan(["13"], None, jams={("UTG", "13")}, limps={"13"})
        self.assertEqual(len(p), 7 + 28 + 7 + 1)                    # 오픈 7 · 방어 28 · UTG 올인 7 · SB 림프 1
        self.assertEqual(p[0], ("UTG", "13", None))
        self.assertEqual(p[8], ("UTG1", "13", "UTG-allin"))          # UTG 레이즈 방어 7장 뒤에 올인 방어
        self.assertEqual(p[-1], ("BB", "13", "SB-limp"))

    def test_hint_names_folding_seats_only(self):
        self.assertEqual(grab_chart.slot_hint(("UTG1", "13", "UTG-allin")), "GTO 툴: UTG 올인 → UTG1 차례")
        self.assertEqual(grab_chart.slot_hint(("CO", "13", None)), "GTO 툴: UTG~HJ 폴드 → CO 차례")
        self.assertEqual(grab_chart.slot_name(("BTN", "13", "UTG")), "UTG vs BTN · 13bb")


class TestWatchLoop(unittest.TestCase):
    """화면 대신 '어떤 차트가 떠 있나'를 이름으로 흉내 내 감시 루프를 돌린다."""

    CHARTS = {"A": {"AA": 1.0}, "B": {"KK": 1.0}, "C": {"QQ": 1.0},
              "J": ({"AA": 1.0, "KK": 1.0}, {"AA": 1.0, "KK": 1.0}, {}),   # 올인만 있는 오픈 차트
              # 빨강이 한 톤뿐인 차트 — 화면만으론 레이즈인지 올인인지 모른다 (밝기 80 / 140)
              "D": ({"AA": 1.0, "KK": 1.0}, {}, {}, {"red_lum": 80.0}),
              "L": ({"AA": 1.0, "QQ": 1.0}, {}, {}, {"red_lum": 140.0})}

    DEFAULT_STATE = {"custom": [{"pos": "UTG", "stack": "20", "bb": 20, "vs": None, "pct": 15,
                                 "has_jam": False, "jam_pct": 0, "call_pct": 0}]}

    def run_watch(self, steps, stacks="20", only="UTG1,LJ", state=None, tones=None):
        g = grab_chart
        saved, deleted, cur = [], [], {"i": 0, "scr": steps[0][1]}
        self.jams, self.tones = [], dict(tones or {})

        def line(_t):
            if cur["i"] >= len(steps):
                return "q\n"
            key, scr = steps[cur["i"]]
            cur["i"] += 1
            if scr:
                cur["scr"] = scr
            return key

        patches = {
            "read_line": line,
            "capture_fast": lambda: cur["scr"],
            "read_grid": lambda im, box=None: (*(self.CHARTS[im][:3] if isinstance(self.CHARTS[im], tuple)
                                                 else (self.CHARTS[im], {}, {})),
                                               {"box": (0, 0, 1, 1), "lines_ok": True,
                                                **(self.CHARTS[im][3] if isinstance(self.CHARTS[im], tuple)
                                                   and len(self.CHARTS[im]) > 3 else {})}),
            "fetch_state": lambda port: state if state is not None else self.DEFAULT_STATE,
            "send": lambda port, pos, st, f, j, c, src, vs=None: self.jams.append(bool(j))
                    or saved.append((pos, st, vs, next(iter(f)))) or {"pct": 1.0},
            "load_tones": lambda: dict(self.tones),
            "save_tones": lambda t: self.tones.update(t),
            "delete_slot": lambda port, pos, st, vs: deleted.append((pos, st, vs)) or saved.pop(),
        }
        old = {k: getattr(g, k) for k in patches}
        try:
            for k, v in patches.items():
                setattr(g, k, v)
            import argparse
            import io
            import contextlib
            with contextlib.redirect_stdout(io.StringIO()):
                g.watch(argparse.Namespace(port=0, stacks=stacks, only=only, watch=True))
        finally:
            for k, v in old.items():
                setattr(g, k, v)
        return saved, deleted

    def test_start_screen_is_not_saved(self):
        saved, _ = self.run_watch([(None, "A"), (None, "A"), (None, "A")])
        self.assertEqual(saved, [])

    def test_new_chart_saved_after_stable(self):
        saved, _ = self.run_watch([(None, "A"), (None, "B"), (None, "B")])
        # --only UTG1,LJ 의 첫 칸은 'UTG 레이즈 → UTG1 차례' (UTG1 오픈은 UTG 갈래가 끝난 뒤)
        self.assertEqual(saved, [("UTG1", "20", "UTG", "KK")])

    def test_undo_does_not_resave_unchanged_screen(self):
        # 실제로 났던 버그: 되돌린 직후 화면은 그대로인데 그 차트가 방금 열린 칸으로 곧장 저장됐다
        # B를 저장한 뒤 다음 칸용 화면 C가 떠 있는 상태에서 되돌린다 — C는 되돌린 칸으로 저장되면 안 된다
        saved, deleted = self.run_watch([(None, "A"), (None, "B"), (None, "B"), (None, "C"),
                                         ("u\n", None), (None, "C"), (None, "C"), (None, "C")])
        self.assertEqual(deleted, [("UTG1", "20", "UTG")])
        self.assertEqual(saved, [])

    def test_jam_only_open_rewrites_plan_in_the_same_run(self):
        # 짧은 스택: UTG 오픈 차트에 레이즈가 없고 올인만 있다 → 'UTG1 vs UTG(레이즈)' 칸은 빠지고
        # 'UTG1 vs UTG 올인' 칸이 바로 다음에 들어와야 한다 (다시 실행하지 않아도)
        saved, _ = self.run_watch([(None, "A"), (None, "J"), (None, "J"), (None, "B"), (None, "B")],
                                  stacks="10", only="UTG,UTG1", state={"custom": []})
        self.assertEqual(saved, [("UTG", "10", None, "AA"), ("UTG1", "10", "UTG-allin", "KK")])

    def test_plan_skips_raise_defense_without_raise_open(self):
        p = grab_chart.plan(["10"], None, jams={("UTG", "10")}, no_raise={("UTG", "10")})
        self.assertNotIn(("UTG1", "10", "UTG"), p)
        self.assertIn(("UTG1", "10", "UTG-allin"), p)

    def test_single_tone_unknown_asks_then_j_saves_jam(self):
        # 실제로 났던 일: 7bb UTG~HJ '전부 올인' 오픈이 한 톤이라 '전부 레이즈'로 저장돼, 순서표가 레이즈
        # 0%짜리 노드의 방어 칸을 만들었다. 이제 모르면 저장하지 않고 묻고, j면 올인 → 올인을 받는 칸으로
        saved, _ = self.run_watch([(None, "A"), (None, "D"), (None, "D"), (None, "D"),
                                   ("j\n", "D"), (None, "B"), (None, "B")],
                                  stacks="7", only="UTG,UTG1", state={"custom": []})
        self.assertEqual(saved, [("UTG", "7", None, "AA"), ("UTG1", "7", "UTG-allin", "KK")])
        self.assertEqual(self.jams[0], True)
        self.assertEqual(self.tones, {"jam": 80.0})                 # 답한 밝기를 기억

    def test_single_tone_guessed_from_remembered_tones(self):
        tones = {"jam": 82.0, "raise": 141.0}
        saved, _ = self.run_watch([(None, "A"), (None, "D"), (None, "D")],
                                  stacks="7", only="UTG,UTG1", state={"custom": []}, tones=tones)
        self.assertEqual((saved, self.jams), ([("UTG", "7", None, "AA")], [True]))
        saved, _ = self.run_watch([(None, "A"), (None, "L"), (None, "L")],
                                  stacks="7", only="UTG,UTG1", state={"custom": []}, tones=tones)
        self.assertEqual((saved, self.jams), ([("UTG", "7", None, "AA")], [False]))

    def test_tone_helpers(self):
        g = grab_chart
        self.assertEqual(g.guess_tone(85, {"jam": 80, "raise": 140}), "jam")
        self.assertIsNone(g.guess_tone(110, {"jam": 80, "raise": 140}))     # 어중간
        self.assertIsNone(g.guess_tone(85, {}))
        self.assertIsNone(g.guess_tone(140, {"jam": 80}))                   # 아는 톤과 멀면 짐작하지 않는다
        self.assertEqual(g.apply_tone({"AA": 1.0, "KQs": 0.6}, {"KQs": 0.2}, "jam"), {"AA": 1.0, "KQs": 0.4})
        self.assertEqual(g.apply_tone({"AA": 1.0}, {}, "raise"), {})
        t = {}
        old = g.save_tones
        g.save_tones = lambda x: None
        try:
            # 한 톤에 번진 테두리가 '밝은 톤' 3%로 잡힌 건 배우지 않는다
            self.assertFalse(g.learn_tones({"tones": {"jam": 80, "raise": 140, "jam_share": 0.97}}, t))
            self.assertTrue(g.learn_tones({"tones": {"jam": 80, "raise": 140, "jam_share": 0.6}}, t))
        finally:
            g.save_tones = old
        self.assertEqual(t, {"jam": 80, "raise": 140})
        self.assertIsNone(g.single_tone({"two_tone": False, "red_lum": 80}, "UTG-allin"))  # 올인을 받음은 전부 콜

    def test_enter_forces_save(self):
        saved, _ = self.run_watch([(None, "A"), ("\n", "A")])
        self.assertEqual(saved, [("UTG1", "20", "UTG", "AA")])


# ---------------------------------------------------------------------------
# AI 코치 · 목록 API
# ---------------------------------------------------------------------------

class TestSyncCharts(unittest.TestCase):
    """S3 솔루션 노드 → 차트. 코드 이름만 믿으면 틀리는 곳들."""

    @staticmethod
    def body(*actions):
        """actions: (type, allin, AA 빈도, 72o 빈도)"""
        return {"players_info": [{"simple_hand_counters": {"AA": {}, "72o": {}}}],
                "action_solutions": [{"action": {"type": t, "allin": a}, "strategy": [x, y]}
                                     for t, a, x, y in actions]}

    def spot(self, game, depth, pf):
        stacks = "-".join([depth] * 8)
        return sync_charts.node_spot(game, depth, stacks, pf)

    def test_seven_max_utg_is_utg1(self):
        self.assertEqual(self.spot("MTTGeneral_7m", "20.125", ""), (20, "UTG1", None))
        self.assertEqual(self.spot("MTTGeneral_7m", "20.125", "R2"), (20, "LJ", "UTG1"))

    def test_raise_for_whole_stack_is_allin_even_without_rai_code(self):
        self.assertEqual(self.spot("MTTGeneral_8m", "10.125", "F-R10"), (10, "LJ", "UTG1-allin"))
        self.assertEqual(self.spot("MTTGeneral_8m", "10.125", "F-R2"), (10, "LJ", "UTG1"))

    def test_sb_limp_and_rejected_nodes(self):
        self.assertEqual(self.spot("MTTGeneral_8m", "20.125", "F-F-F-F-F-F-C"), (20, "BB", "SB-limp"))
        self.assertIsNone(self.spot("MTTGeneral_8m", "20.125", "C"))              # SB 말고 림프
        self.assertIsNone(self.spot("MTTGeneral_8m", "20.125", "R2-R6"))          # 3벳 진행
        self.assertIsNone(self.spot("MTTGeneral_ICM8m200PTT2", "20.125", ""))     # ICM
        self.assertIsNone(sync_charts.node_spot("MTTGeneral_8m", "20.125", "20.125-30.125", ""))

    def test_body_splits_call_and_jam_and_drops_check(self):
        item = sync_charts.body_to_item(self.body(("FOLD", False, 0, 0.5), ("CALL", False, 0.25, 0.5),
                                                  ("RAISE", False, 0.25, 0), ("RAISE", True, 0.5, 0)),
                                        20, "SB", None)
        self.assertEqual(item["text"], "72o:0.5000, AA:1.0000")
        self.assertEqual(item["jam"], "AA:0.5000")
        self.assertEqual(item["call"], "72o:0.5000, AA:0.2500")
        limp = sync_charts.body_to_item(self.body(("CHECK", False, 0.5, 1), ("RAISE", False, 0.5, 0)),
                                        20, "BB", "SB-limp")
        self.assertEqual(limp["text"], "AA:0.5000")

    def test_float_overflow_is_not_read_as_percent(self):
        # 합이 1을 살짝 넘어 parse_range가 0~100 스케일로 읽으면 모든 빈도가 100분의 1이 된다
        item = sync_charts.body_to_item(self.body(("RAISE", False, 0.6002, 0.3), ("RAISE", True, 0.4, 0)),
                                        20, "CO", None)
        weights, _ = ranges.parse_range(item["text"])
        self.assertEqual(weights["AA"], 1.0)
        self.assertAlmostEqual(weights["72o"], 0.3)

    def test_8max_node_wins_over_7max(self):
        import gzip, sqlite3, tempfile
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "s.db")
            con = sqlite3.connect(path)
            con.execute("CREATE TABLE solution (gametype, depth, stacks, preflop_actions, flop_actions, board, "
                        "street, body_gz, fetched_at)")
            for game, tag, ts in (("MTTGeneral_8m", b"8", 1), ("MTTGeneral_7m", b"7", 99)):
                n = int(game[-2])
                pf = "F" if n == 8 else ""     # 둘 다 UTG1의 오픈 노드
                con.execute("INSERT INTO solution VALUES (?,?,?,?,'','','PREFLOP',?,?)",
                            (game, "20.125", "-".join(["20.125"] * n), pf, gzip.compress(tag), ts))
            con.commit()
            con.close()
            found = {}
            sync_charts.read_db(path, {20}, found)
        self.assertEqual(gzip.decompress(found[(20, "UTG1", None)][1]), b"8")

    def test_replace_is_all_or_nothing(self):
        db = {"ranges": {"attempts": [1], "charts": {"UTG|13": {"weights": {"AA": 1.0}}}}}
        bad = ranges.replace_charts(db, [{"pos": "UTG", "stack": "20", "text": "AA:1"},
                                         {"pos": "NOPE", "stack": "20", "text": "AA:1"}], "s")
        self.assertIn("error", bad)
        self.assertIn("UTG|13", db["ranges"]["charts"])
        self.assertIn("error", ranges.replace_charts(db, [], "s"))
        self.assertIn("UTG|13", db["ranges"]["charts"])

        ok = ranges.replace_charts(db, [{"pos": "BB", "stack": "20", "vs": "SB-limp", "text": "AA:0.5"}], "s")
        self.assertEqual((ok["n"], ok["removed"], ok["changed"]), (1, 1, True))
        self.assertEqual(list(db["ranges"]["charts"]), ["BB|20|vsSB-limp"])
        self.assertEqual(db["ranges"]["attempts"], [1])


class TestEnvFilePath(unittest.TestCase):
    """PC마다 poker-tracker 위치가 달라 한 경로로 고정하면 동기화가 말없이 꺼진다."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.first, self.second = os.path.join(tmp.name, "a.env"), os.path.join(tmp.name, "b.env")

    def test_later_candidate_is_used_when_first_is_absent(self):
        open(self.second, "w").close()
        self.assertEqual(sync_charts.default_env_file((self.first, self.second)), self.second)

    def test_first_candidate_wins_when_both_exist(self):
        open(self.first, "w").close()
        open(self.second, "w").close()
        self.assertEqual(sync_charts.default_env_file((self.first, self.second)), self.first)

    def test_no_file_means_sync_stays_off(self):
        path = sync_charts.default_env_file((self.first, self.second))
        self.assertEqual(path, self.first)
        self.assertIsNone(sync_charts.open_s3(path))


class TestAutoSync(unittest.TestCase):
    """앱 시작 시 자동 교체 — 안 받아도 될 때 안 받고, 실패해도 기존 차트가 남아야 한다."""

    OLD = {"pos": "UTG", "stack": "20", "text": "AA:1.0000"}
    NEW = {"pos": "UTG", "stack": "20", "text": "AA:0.5000"}

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.etag_file = os.path.join(self.tmp.name, "sub", "etags.json")
        self.db = {}
        ranges.replace_charts(self.db, [self.OLD], sync_charts.SOURCE)
        self.saves, self.logs = [], []
        self.objects = {"p/pcs/mac.db.gz": '"e1"'}

    def run_sync(self, items=None, boom=None):
        s3 = mock.Mock()
        s3.list.return_value = self.objects
        fetch = mock.Mock(return_value=items, side_effect=boom)
        with mock.patch.object(sync_charts, "open_s3", return_value=(s3, "p/")), \
                mock.patch.object(sync_charts, "fetch_items", fetch):
            sync_charts.auto_sync(self.db, self.saves.append, etag_file=self.etag_file, log=self.logs.append)
        return fetch

    def weight(self):
        return self.db["ranges"]["charts"]["UTG|20"]["weights"]["AA"]

    def test_new_content_replaces_and_saves(self):
        self.run_sync([self.NEW])
        self.assertEqual(self.weight(), 0.5)
        self.assertEqual(len(self.saves), 1)

    def test_unchanged_etags_skip_download(self):
        self.run_sync([self.OLD])
        self.assertEqual(self.run_sync([self.NEW]).call_count, 0)
        self.assertEqual(self.weight(), 1.0)

    def test_changed_etag_downloads_again(self):
        self.run_sync([self.OLD])
        self.objects = {"p/pcs/mac.db.gz": '"e2"'}
        self.assertEqual(self.run_sync([self.NEW]).call_count, 1)
        self.assertEqual(self.weight(), 0.5)

    def test_same_content_does_not_save_or_touch_ts(self):
        # ETag만 바뀌고 내용이 같으면 130MB 저장·클라우드 업로드를 하지 않는다 (ts는 날마다 달라도)
        self.db["ranges"]["charts"]["UTG|20"]["ts"] = "old"
        self.run_sync([self.OLD])
        self.assertEqual(self.saves, [])
        self.assertEqual(self.db["ranges"]["charts"]["UTG|20"]["ts"], "old")

    def test_failure_keeps_charts_and_retries_next_time(self):
        self.run_sync(boom=OSError("network down"))
        self.assertEqual(self.weight(), 1.0)
        self.assertEqual(self.saves, [])
        self.assertTrue(any("network down" in m for m in self.logs))
        self.assertEqual(self.run_sync([self.NEW]).call_count, 1)

    def test_big_shrink_is_refused_and_retried(self):
        ranges.replace_charts(self.db, [dict(self.OLD, stack=str(b)) for b in (10, 20, 30, 40)], sync_charts.SOURCE)
        self.run_sync([self.NEW])
        self.assertEqual(len(self.db["ranges"]["charts"]), 4)
        self.assertEqual(self.saves, [])
        self.assertEqual(self.run_sync([self.NEW]).call_count, 1)

    def test_empty_db_redownloads_despite_same_etags(self):
        self.run_sync([self.OLD])
        self.db["ranges"]["charts"] = {}
        self.assertEqual(self.run_sync([self.NEW]).call_count, 1)

    def test_no_s3_config_is_silent(self):
        sync_charts.auto_sync(self.db, self.saves.append, env_file=os.path.join(self.tmp.name, "none"),
                              etag_file=self.etag_file, log=self.logs.append)
        self.assertEqual((self.saves, self.logs), ([], []))
        self.assertEqual(self.weight(), 1.0)


class TestCoach(unittest.TestCase):
    def setUp(self):
        self.db = {"hands": {}}
        store.import_text(self.db, SAMPLE)

    def test_hand_ref_by_suffix(self):
        refs, missing = coach.hand_refs(self.db, ["#300001 이거 어때? #999999"])
        self.assertEqual((refs, missing), (["69187300001"], ["999999"]))

    def test_followup_reattaches_hand_and_quotes_history(self):
        cid = coach.new_id()
        coach.save_exchange(self.db, cid, "#300001 폴드 맞아?", "## 답\n- 맞습니다", ["69187300001"])
        prompt, refs, _ = coach.build_prompt(self.db, cid, "그럼 3벳은?")
        self.assertEqual(refs, ["69187300001"])
        self.assertIn("> ## 답", prompt)                 # 지난 답의 제목은 인용으로 — 구역 제목과 섞이지 않게

    def test_profile_uses_leak_report_numbers(self):
        # 코치 요약과 📊 리크 리포트가 같은 숫자를 말해야 한다 (예전엔 계산이 두 벌이었다)
        db = {"hands": {}}
        ranges.import_chart(db, "CO", "20", "AA:100,KK:100")
        db["hands"] = {"1": hand("CO", 7, ["Ah", "Ad"], rfi=True, pf_opener=None),
                       **{f"t{i}": hand("CO", 7, ["7h", "2d"], rfi=False) for i in range(20)}}
        reset_caches()
        row = ranges.leak_report(db, 8)["rows"][0]
        p = coach.profile_text(db)
        self.assertIn(f"실제 {row['actual']}% / 차트 {row['expected']}%", p)

    def test_chat_caps(self):
        for i in range(coach.MAX_CHATS + 5):
            coach.save_exchange(self.db, str(i), "q", "a", [])
        self.assertEqual(len(self.db["coach"]["chats"]), coach.MAX_CHATS)


class TestWebFiles(unittest.TestCase):
    def test_index_references_only_served_static_files(self):
        # 화면은 web/ 파일이다 — index.html이 부르는 /static/ 파일이 전부 WEB_STATIC에 있어야 실제로 내려간다
        import re
        import gui
        html = gui.web_file("index.html").decode("utf-8")
        refs = set(re.findall(r'/static/([\w.-]+)', html))
        self.assertEqual(refs, set(gui.WEB_STATIC))
        for name in refs:
            self.assertTrue(gui.web_file(name).strip(), name)


class TestAdherence(unittest.TestCase):
    def setUp(self):
        reset_caches()
        self.db = {"hands": {}}
        ranges.import_chart(self.db, "CO", "20", "AA:100,KK:100,QQ:50")

    def report(self, hands):
        self.db["hands"] = {str(i): h for i, h in enumerate(hands)}
        reset_caches()
        return ranges.adherence_report(self.db)

    def test_trivial_folds_do_not_inflate_core_rate(self):
        hands = [hand("CO", 7, ["7h", "2d"], rfi=False) for _ in range(8)]   # 당연한 폴드
        hands += [hand("CO", 7, ["Ah", "Ad"], rfi=False),                    # AA 폴드 — 타이트
                  hand("CO", 7, ["7h", "2d"], rfi=True),                     # 72o 오픈 — 루즈
                  hand("CO", 7, ["Kh", "Kd"], rfi=True),
                  hand("CO", 7, ["Qh", "Qd"], rfi=True)]                     # 50% 칸은 어느 쪽이든 일치
        t = self.report(hands)["total"]
        self.assertEqual((t["n"], t["core_n"], t["bad"], t["tight"], t["loose"]), (12, 4, 2, 1, 1))
        self.assertEqual((t["agree"], t["core_agree"]), (83.3, 50.0))

    def test_counts_match_chart_deviation_badge(self):
        hands = [hand("CO", 7, ["Ah", "Ad"], rfi=False), hand("CO", 7, ["Ah", "Ad"], rfi=True)]
        t = self.report(hands)["total"]
        self.assertEqual(t["bad"], sum(1 for h in hands if ranges.hand_deviation(self.db, h, {})))

    def test_coverage_splits_why_not_compared(self):
        hands = [hand("CO", 7, ["Ah", "Ad"], rfi=True),                      # 비교됨
                 dict(hand("CO", 7, ["Ah", "Ad"], rfi=True), stack_bb=60),   # 차트보다 깊음
                 hand("CO", 7, ["Ah", "Ad"], faced="raise"),                 # 콜러 낀 팟 등 — pf_opener 없음
                 hand("CO", 7, ["Ah", "Ad"], faced=None)]                    # 결정 없음 (워크)
        c = self.report(hands)["cover"]
        self.assertEqual((c["decisions"], c["compared"], c["no_chart"], c["deep"], c["unmeasurable"]),
                         (3, 1, 1, 1, 1))


def upload_raw(hand_id, lines):
    """샘플 핸드의 ID와 프리플랍을 바꾼 원문 (BTN 084a1d59 / SB Hero / BB 78c4f548)."""
    return sample_with_preflop(lines).replace("69187300001", hand_id)


FLOP_HU = ["084a1d59: folds", "Hero: raises 350 to 700", "78c4f548: calls 350",
           "*** FLOP *** [Jd 8c 7d]", "Hero: bets 500", "78c4f548: folds"]
FLOP_3WAY = ["084a1d59: calls 350", "Hero: calls 175", "78c4f548: checks",
             "*** FLOP *** [Jd 8c 7d]", "Hero: checks", "78c4f548: checks", "084a1d59: checks"]
FLOP_NO_HERO = ["084a1d59: raises 350 to 700", "Hero: folds", "78c4f548: calls 350",
                "*** FLOP *** [Jd 8c 7d]", "78c4f548: checks", "084a1d59: checks"]


class TestUploadHands(unittest.TestCase):
    TODAY = __import__("datetime").date(2026, 6, 10)

    def setUp(self):
        reset_caches()
        ranges._SHAPE_CACHE.clear()
        self.db = {"hands": {}}
        ranges.import_chart(self.db, "CO", "20", "AA:100,KK:100")

    def add(self, hand_id, lines=None, **kw):
        raw = upload_raw(hand_id, lines or ["084a1d59: folds", "Hero: folds"])
        r = dict(hand("CO", 7, ["7h", "2d"]), hand_id=hand_id, raw=raw, net_bb=0,
                 datetime="2026/06/05 10:00:00 KST", no_action_fold=False)
        r.update(kw)
        self.db["hands"][hand_id] = r
        return r

    def pick(self, **kw):
        kw.setdefault("today", self.TODAY)
        return ranges.upload_hands(self.db, **kw)

    def ids(self, **kw):
        return [r["hand_id"] for r in self.pick(**kw)["hands"]]

    def test_pot_shape(self):
        self.assertEqual(convert.pot_shape(convert.parse_hand(upload_raw("1", FLOP_HU))), (2, True))
        self.assertEqual(convert.pot_shape(convert.parse_hand(upload_raw("1", FLOP_3WAY))), (3, True))
        self.assertEqual(convert.pot_shape(convert.parse_hand(upload_raw("1", FLOP_NO_HERO))), (2, False))

    def test_multiway_is_excluded_and_counted(self):
        self.add("100", FLOP_HU, faced=None)
        self.add("101", FLOP_3WAY, faced=None)
        res = self.pick(kinds=["hu"])
        self.assertEqual([r["hand_id"] for r in res["hands"]], ["100"])
        self.assertEqual(res["multiway"], 1)

    def test_preflop_fold_is_not_a_postflop_candidate(self):
        self.add("100", FLOP_NO_HERO, faced=None, no_action_fold=True)
        self.add("101", FLOP_NO_HERO, faced=None, no_action_fold=False)   # 플랍을 봤지만 히어로 결정은 없음
        self.assertEqual(self.ids(kinds=["hu"]), [])

    def test_days_window(self):
        self.add("100", FLOP_HU, faced=None, datetime="2026/06/05 10:00:00 KST")
        self.add("101", FLOP_HU, faced=None, datetime="2026/01/05 10:00:00 KST")
        self.assertEqual(self.ids(kinds=["hu"], days=30), ["100"])
        self.assertEqual(set(self.ids(kinds=["hu"], days=0)), {"100", "101"})

    def test_limit_alternates_kinds_and_dedupes(self):
        for i in range(4):                               # 차트 이탈 (AA 폴드)이 여럿이어도
            self.add(f"20{i}", hero_cards=["Ah", "Ad"], rfi=False)
        self.add("300", FLOP_HU, faced=None, net_bb=30)  # 포스트플랍 핸드가 밀려나지 않는다
        got = self.ids(limit=2)
        self.assertEqual(len(got), 2)
        self.assertIn("300", got)
        # 이탈이면서 헤즈업 포스트플랍인 핸드는 한 번만
        self.db["hands"].clear()
        self.add("400", FLOP_HU, hero_cards=["7h", "2d"], rfi=True)
        self.assertEqual(self.ids(), ["400"])

    def test_upload_text_is_original_and_resplits(self):
        a, b = self.add("100", FLOP_HU), self.add("101", FLOP_HU)
        parts = convert.split_hands(ranges.upload_text([a, b]))
        self.assertEqual([p.strip() for p in parts], [a["raw"].strip(), b["raw"].strip()])


class TestListApis(unittest.TestCase):
    def test_hands_by_combo_includes_markdown(self):
        # 실제로 났던 버그: markdown을 DB에 저장하지 않게 된 뒤 그리드 드릴다운만 본문 없이 와서 렌더가 깨졌다
        db = {"hands": {}}
        store.import_text(db, SAMPLE)
        hands = store.hands_by_combo(db, "JTo")["hands"]
        self.assertTrue(hands and hands[0].get("markdown"))
        self.assertNotIn("raw", hands[0])


if __name__ == "__main__":
    unittest.main()
