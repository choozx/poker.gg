"""핵심 회귀 테스트 — 표준 라이브러리 unittest만 쓴다 (프로젝트 규칙).

    python3 -m unittest discover -s tests        # 레포 루트에서

실제 DB(hands_db.json)는 건드리지 않는다. sample_hand.txt와 테스트 안에서 만든 dict만 쓴다.
여기 있는 것들은 대부분 **실제로 한 번 났던 버그**다 — 고칠 때 같은 실수를 다시 하지 않게 남긴다.
"""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import coach      # noqa: E402
import convert    # noqa: E402
import grab_chart  # noqa: E402
import ranges     # noqa: E402
import store      # noqa: E402

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
        ranges._HERO_CACHE.update(n=None, data=None)
        self.assertEqual(ranges.hero_cells(db, "UTG", "short")["AKo"], [0, 1])            # 8인 UTG만
        self.assertEqual(ranges.hero_cells(db, "UTG1", "short")["AKo"], [5, 5])           # 7인 UTG
        self.assertEqual(ranges.hero_cells(db, "UTG", "short", builtin=True)["AKo"], [5, 6])  # 내장=원래 이름

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


class TestLeakReport(unittest.TestCase):
    def test_expected_is_weighted_by_dealt_combos(self):
        db = {"hands": {}}
        ranges.import_chart(db, "CO", "20", "AA:100,KK:100")       # AA·KK만 오픈
        db["hands"] = {
            "1": hand("CO", 7, ["Ah", "Ad"], rfi=True), "2": hand("CO", 7, ["Kh", "Kd"], rfi=False),
            **{f"t{i}": hand("CO", 7, ["7h", "2d"], rfi=False) for i in range(20)},
        }
        ranges._HERO_CACHE.update(n=None, data=None)
        rows = ranges.leak_report(db, 7)["rows"]
        self.assertEqual(len(rows), 1)
        r = rows[0]
        self.assertEqual((r["n"], r["expected"], r["actual"]), (22, round(2 / 22 * 100, 1), round(1 / 22 * 100, 1)))
        self.assertEqual(r["wrong"], 1.0)                            # KK 한 번을 안 열었다


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

    CHARTS = {"A": {"AA": 1.0}, "B": {"KK": 1.0}, "C": {"QQ": 1.0}}

    def run_watch(self, steps, todo_have=()):
        g = grab_chart
        saved, deleted, cur = [], [], {"i": 0, "scr": steps[0][1]}

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
            "read_grid": lambda im, box=None: (self.CHARTS[im], {}, {}, {"box": (0, 0, 1, 1), "lines_ok": True}),
            "fetch_state": lambda port: {"custom": [{"pos": "UTG", "stack": "20", "bb": 20, "vs": None,
                                                    "has_jam": False, "jam_pct": 0}]},
            "send": lambda port, pos, st, f, j, c, src, vs=None: saved.append((pos, st, vs, next(iter(f))))
                    or {"pct": 1.0},
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
                g.watch(argparse.Namespace(port=0, stacks="20", only="UTG1,LJ", watch=True))
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

    def test_enter_forces_save(self):
        saved, _ = self.run_watch([(None, "A"), ("\n", "A")])
        self.assertEqual(saved, [("UTG1", "20", "UTG", "AA")])


# ---------------------------------------------------------------------------
# AI 코치 · 목록 API
# ---------------------------------------------------------------------------

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
        ranges._HERO_CACHE.update(n=None, data=None)
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
