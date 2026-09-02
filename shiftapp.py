import streamlit as st
import json
import pandas as pd
from datetime import date
import calendar
import random
import base64

try:
    import requests as _requests
    HAS_REQUESTS = True
except ImportError:
    HAS_REQUESTS = False

try:
    import jpholiday
    HAS_JPHOLIDAY = True
except ImportError:
    HAS_JPHOLIDAY = False

# ─────────────────────────────────────────────
# 定数
# ─────────────────────────────────────────────
DEPT_IDS    = ["A", "B", "C", "D"]
DEPT_COLORS = {"A": "#4A90D9", "B": "#27AE60", "C": "#E67E22", "D": "#8E44AD"}
DATA_FILE   = "shiftapp_data.json"
WEEKDAY_JP  = ["月", "火", "水", "木", "金", "土", "日"]

# ─────────────────────────────────────────────
# GitHub同期ヘルパー
# ─────────────────────────────────────────────
def _gh_config():
    """secrets.tomlからGitHub設定を取得。未設定時はNoneを返す"""
    try:
        token = st.secrets["github"]["token"]
        repo  = st.secrets["github"]["repo"]    # 例: "hamaura04/shift-app"
        path  = st.secrets["github"].get("path", DATA_FILE)
        branch= st.secrets["github"].get("branch", "main")
        return token, repo, path, branch
    except Exception:
        return None, None, None, None

def _gh_get_file():
    """GitHubからJSONファイルを取得。(content_dict, sha) を返す。失敗時は(None, None)"""
    token, repo, path, branch = _gh_config()
    if not token or not HAS_REQUESTS:
        return None, None
    url = f"https://api.github.com/repos/{repo}/contents/{path}"
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github.v3+json"}
    try:
        resp = _requests.get(url, headers=headers, params={"ref": branch}, timeout=10)
        if resp.status_code == 200:
            body = resp.json()
            content = json.loads(base64.b64decode(body["content"]).decode("utf-8"))
            return content, body["sha"]
    except Exception:
        pass
    return None, None

def _gh_put_file(data: dict, sha: str, message: str = "Update shiftapp_data.json"):
    """GitHubにJSONファイルをPush。成功したらTrue"""
    token, repo, path, branch = _gh_config()
    if not token or not HAS_REQUESTS:
        return False, "token or requests missing"
    url = f"https://api.github.com/repos/{repo}/contents/{path}"
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github.v3+json"}
    content_b64 = base64.b64encode(
        json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
    ).decode("utf-8")
    payload = {"message": message, "content": content_b64, "branch": branch}
    if sha:
        payload["sha"] = sha
    try:
        resp = _requests.put(url, headers=headers, json=payload, timeout=15)
        if resp.status_code in (200, 201):
            return True, ""
        else:
            return False, f"HTTP {resp.status_code}: {resp.json().get('message','')}"
    except Exception as e:
        return False, str(e)

# ─────────────────────────────────────────────
# データ永続化（GitHub優先・ローカルフォールバック）
# ─────────────────────────────────────────────
def load_data() -> dict:
    """
    起動時のデータ読み込み順:
      1. GitHubからPull（secrets設定済みの場合）
      2. ローカルファイル（DATA_FILE）
      3. デフォルト値
    """
    d = None
    # GitHub優先
    gh_data, gh_sha = _gh_get_file()
    if gh_data is not None:
        d = gh_data
        # GitHubのSHAをsession_stateに保存しておく（Push時に必要）
        try:
            st.session_state["_gh_sha"] = gh_sha
        except Exception:
            pass
    else:
        # ローカルファイル
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                d = json.load(f)
        except FileNotFoundError:
            d = default_data()

    # 補完
    d.setdefault("requests", {})
    d.setdefault("request_lock", False)
    for did in DEPT_IDS:
        cfg = d["dept_config"].setdefault(did, {"label": did, "min_staff": 1})
        cfg.setdefault("label", did)
        cfg.pop("max_staff", None)
    return d

def save_data(data: dict, to_github: bool = False):
    """
    to_github=False: ローカル（session_state経由）のみ保存
    to_github=True : GitHub + ローカル両方に保存（確定ボタン用）
    戻り値: (success: bool, message: str)
    """
    # 常にローカルにも書く（コンテナ再起動まで有効）
    try:
        with open(DATA_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception:
        pass

    if to_github:
        sha = st.session_state.get("_gh_sha", None)
        ok, detail = _gh_put_file(data, sha)
        if ok:
            # Push成功後はSHAを更新
            _, new_sha = _gh_get_file()
            if new_sha:
                st.session_state["_gh_sha"] = new_sha
            return True, "✅ GitHubに保存しました"
        else:
            token, repo, path, branch = _gh_config()
            if not token:
                return False, "⚠️ GitHub未設定のためローカルのみ保存。secrets.tomlを確認してください。"
            return False, f"❌ GitHubへの保存に失敗しました: {detail}"
    return True, ""

def default_data() -> dict:
    return {
        "staff": {},
        "dept_config": {
            "A": {"label": "A", "min_staff": 1},
            "B": {"label": "B", "min_staff": 1},
            "C": {"label": "C", "min_staff": 1},
            "D": {"label": "D", "min_staff": 1},
        },
        "shifts": {},
        "requests": {},
        "request_lock": False
    }

# ─────────────────────────────────────────────
# ヘルパー
# ─────────────────────────────────────────────
def dept_label(data: dict, did: str) -> str:
    """部門ID → 表示名（設定されていればそちら、なければID）"""
    lbl = data["dept_config"].get(did, {}).get("label", did)
    return lbl if lbl else did

def dept_display(data: dict, did: str) -> str:
    """selectbox 等の表示用: 表示名(ID) または 表示名のみ"""
    lbl = dept_label(data, did)
    return f"{lbl}({did})" if lbl != did else did

# ─────────────────────────────────────────────
# 日付種別
# ─────────────────────────────────────────────
def day_type(d: date) -> str:
    wd = d.weekday()
    if wd == 6:
        return "holiday"
    if HAS_JPHOLIDAY and jpholiday.is_holiday(d):
        return "holiday"
    if wd == 5:
        return "saturday"
    return "weekday"

def is_work_day(d: date) -> bool:
    return day_type(d) == "weekday"

# ─────────────────────────────────────────────
# 夜勤ローテーション生成（代休ルール対応）
# ─────────────────────────────────────────────
def plan_night_shifts(year: int, month: int, data: dict,
                      cate_duty_plan: dict = None,
                      requests: dict = None,
                      prev_shifts: dict = None,
                      duty_locked: dict = None) -> dict:
    """
    ルール:
    - 毎平日1名が夜勤入り（均等ローテーション）
    - 夜入の翌日は夜明け（土日祝でも勤務扱い）
    - 夜入 or 夜明けが土日祝に被った日数分 → 代休が発生
    - 代休配置: 原則=夜入より前の直近平日、月初等で前がない=夜明け後の直近平日
    - 代休日はメイン部署から除外（完全休み）
    戻り値: {date_key: {sid: "夜入"|"夜明"|"代休"}}
    """
    from datetime import timedelta
    _, num_days = calendar.monthrange(year, month)
    staff = data["staff"]

    night_staff = [sid for sid, s in staff.items() if s.get("night_shift", 0) == 1]
    if not night_staff:
        return {}
    _requests = requests or {}
    _prev_shifts = prev_shifts or {}
    _month_str_n = f"{year}-{month:02d}-"

    # 前月末が夜入のスタッフは当月1日が夜明け → 当月1日は夜勤候補から除外
    import calendar as _cal_n
    _prev_year  = year if month > 1 else year - 1
    _prev_month = month - 1 if month > 1 else 12
    _, _prev_nd = _cal_n.monthrange(_prev_year, _prev_month)
    _prev_last_dk_n = date(_prev_year, _prev_month, _prev_nd).strftime("%Y-%m-%d")
    _prev_night_in = {
        sid for sid, st in _prev_shifts.get(_prev_last_dk_n, {}).items()
        if st == "夜入"
    }
    def _req_off_night(sid_, dk_):
        return _requests.get(sid_, {}).get(dk_) in ("off_duty", "off_only")
    # plan_night_shifts内で使用するreq_off_days（代休配置用）
    req_off_days = {}
    req_no_duty_days = {}
    would_cause_6consec = None  # 夜勤代休はauto_assign_month側で後処理するためここでは不要
    for _sid_n in staff:
        req_off_days[_sid_n] = {dk for dk,v in _requests.get(_sid_n,{}).items()
                                  if dk.startswith(_month_str_n) and v == "off_duty"}
                                  # off_only（当番可）は代休優先配置の対象外
        req_no_duty_days[_sid_n] = {dk for dk,v in _requests.get(_sid_n,{}).items()
                                     if dk.startswith(_month_str_n) and v in ("off_duty","no_duty")}

    # ── 当番不足期間を事前検出 ─────────────────────────────────
    # opeスキル持ちスタッフが3名以上当番不可の期間を検出
    # → その期間の2日前以内に「当番可能スタッフ」の夜入を避ける
    _ope_all_n = [s for s, si in staff.items()
                  if any(sk in ["ope1","ope2"] for sk in si.get("duty_skills",[]))]
    from datetime import timedelta as _tdn
    _no_duty_period_dates = set()   # 夜入を避けるべき日付
    _protect_from_night = {}        # {date_key: set of sids} 保護すべきスタッフ

    for _dd in range(1, num_days + 1):
        _dk = date(year, month, _dd).strftime("%Y-%m-%d")
        # この日に当番不可のopeスタッフ数
        _no_duty_ope = [s for s in _ope_all_n
                        if req_no_duty_days.get(s, set()) & {_dk}]
        if len(_no_duty_ope) >= 3:
            # 当番可能スタッフ（この日に当番できる人）を特定
            _duty_capable = [s for s in _ope_all_n if s not in _no_duty_ope]
            # この日の2日前・1日前の夜入からこのスタッフを除外
            for _pre in [2, 1]:
                _pre_d = date(year, month, _dd) - _tdn(days=_pre)
                if _pre_d.month != month: continue
                _pre_dk = _pre_d.strftime("%Y-%m-%d")
                if _pre_dk not in _protect_from_night:
                    _protect_from_night[_pre_dk] = set()
                _protect_from_night[_pre_dk].update(_duty_capable)

    # 全日（土日祝含む）に夜勤入りを割り当て
    all_days = [date(year, month, d) for d in range(1, num_days + 1)]

    # 夜勤入りを均等割り当て（365日全日）
    night_count = {sid: 0 for sid in night_staff}
    # 前月繰り越し夜勤スタッフは夜勤回数+1で均等化カウントを補正
    for _sid_nc in _prev_night_in:
        if _sid_nc in night_count:
            night_count[_sid_nc] += 1
    night_plan  = {}  # {date_key: sid}

    for d in all_days:
        dk    = d.strftime("%Y-%m-%d")
        prev1 = (d - timedelta(days=1)).strftime("%Y-%m-%d")
        prev2 = (d - timedelta(days=2)).strftime("%Y-%m-%d")
        busy  = {night_plan.get(prev1), night_plan.get(prev2)} - {None}

        # カテ当番が確定している日はその人を夜勤から除外
        _cate_sid = cate_duty_plan.get(dk) if cate_duty_plan else None
        cate_today = {_cate_sid} if _cate_sid else set()
        # 希望休のスタッフを夜勤候補から除外
        _off_today = {s for s in night_staff if _req_off_night(s, dk)}
        # 前月末夜入→当月1日夜明けのスタッフは
        # 夜明け当日＋翌2日間（夜明け・休み・代休）は夜勤候補から除外
        _month1_dk_n = date(year, month, 1).strftime("%Y-%m-%d")
        _month2_dk_n = date(year, month, 2).strftime("%Y-%m-%d") if calendar.monthrange(year, month)[1] >= 2 else ""
        _month3_dk_n = date(year, month, 3).strftime("%Y-%m-%d") if calendar.monthrange(year, month)[1] >= 3 else ""
        # 繰り越しスタッフを1〜3日目の夜入から除外
        _prev_carry_excl = _prev_night_in if dk in (_month1_dk_n, _month2_dk_n, _month3_dk_n) else set()
        # その日に透析土日日勤が予定されているスタッフを除外（重複防止）
        _dial_day_today = {s for s, v in data["shifts"].get(dk, {}).items() if v == "D" and s != "_duty"}
        # 事前ope当番確定済みスタッフはその日の夜入から除外
        _locked_today = set((duty_locked or {}).get(dk, []))
        candidates = [s for s in night_staff
                      if s not in busy and s not in cate_today
                      and s not in _off_today and s not in _prev_carry_excl
                      and s not in _dial_day_today and s not in _locked_today]
        if not candidates:
            candidates = [s for s in night_staff
                          if s not in busy and s not in _off_today
                          and s not in _prev_carry_excl
                          and s not in _dial_day_today
                          and s not in _locked_today] or [
                s for s in night_staff
                if s not in busy and s not in _off_today
                and s not in _prev_carry_excl
                and s not in _locked_today] or night_staff[:]
        random.shuffle(candidates)
        # 当番不足期間の前2日：当番可能スタッフを夜入候補から除外（保護）
        _protected = _protect_from_night.get(dk, set())
        _cands_safe   = [s for s in candidates if s not in _protected]
        _cands_protect = [s for s in candidates if s in _protected]

        # ope2スタッフ（ope1なし）は月前半の夜勤を後回しにする
        _d_obj_n = date(int(dk[:4]), int(dk[5:7]), int(dk[8:10]))
        _month_mid = num_days // 2
        _is_first_half = (_d_obj_n.day <= _month_mid)
        _is_wednesday = (_d_obj_n.weekday() == 2)

        # 部門ローテーション：A→B→C→D→A の順で夜勤を回す
        # 前回の夜勤部門を追跡して次の部門を優先する
        _dept_order = ["A", "B", "C", "D"]
        _last_night_dept = None
        _last_night_d = None
        for _prev_dk_n, _prev_sid_n in sorted(night_plan.items(), reverse=True):
            _last_night_dept = staff[_prev_sid_n].get("main_dept","A")
            _last_night_d = date(int(_prev_dk_n[:4]),int(_prev_dk_n[5:7]),int(_prev_dk_n[8:10]))
            break
        # 次の優先部門
        if _last_night_dept and _last_night_dept in _dept_order:
            _next_dept_idx = (_dept_order.index(_last_night_dept) + 1) % len(_dept_order)
            _next_dept = _dept_order[_next_dept_idx]
            # 同一部門が連続する場合（候補不足）は水曜を優先スロットとして使う
            _same_dept_consec = (_last_night_dept == _next_dept)
        else:
            _next_dept = "A"
            _same_dept_consec = False

        # 部門ごとのナイトカウント
        _dept_night_count = {dept: 0 for dept in _dept_order}
        for _ps, _pn in night_plan.items():
            _pd = staff[_pn].get("main_dept","A")
            if _pd in _dept_night_count:
                _dept_night_count[_pd] += 1

        def _night_sort_key(s_):
            _dept = staff[s_].get("main_dept","A")
            _is_ope2_only = (
                any(sk in ["ope1","ope2"] for sk in staff[s_].get("duty_skills",[]))
                and "ope1" not in staff[s_].get("duty_skills",[])
            )
            # 前半はope2を後回し
            _ope2_penalty = 1 if (_is_ope2_only and _is_first_half) else 0
            # 部門ローテーション優先度（次の部門が最優先、回数少ない部門が次）
            _dept_priority = 0 if _dept == _next_dept else _dept_night_count.get(_dept, 0) + 1
            # 同一部門連続の場合、水曜以外は同一部門を避ける
            _same_dept_penalty = (
                1 if _dept == _last_night_dept and not _is_wednesday
                else 0
            )
            return (_ope2_penalty, _same_dept_penalty, _dept_priority, night_count[s_])

        _cands_safe.sort(key=_night_sort_key)
        _cands_protect.sort(key=_night_sort_key)
        candidates = _cands_safe + _cands_protect
        chosen = candidates[0]
        night_plan[dk] = chosen
        night_count[chosen] += 1

    # 夜明け・代休を展開
    result = {}  # {date_key: {sid: status}}

    for dk, sid in night_plan.items():
        d_in   = date(int(dk[:4]), int(dk[5:7]), int(dk[8:10]))
        d_off  = d_in + timedelta(days=1)
        dk_off = d_off.strftime("%Y-%m-%d")

        result.setdefault(dk,     {})[sid] = "夜入"
        result.setdefault(dk_off, {})[sid] = "夜明"

        # 代休が必要な日数（土日祝に被った日数）
        kyukei = 0
        if not is_work_day(d_in):  kyukei += 1
        if not is_work_day(d_off): kyukei += 1

        if kyukei == 0:
            continue

        # ── 連勤チェック用ヘルパー ──────────────────────────────
        def consecutive_work_days(base_date, result_so_far, sid, direction):
            """base_dateからdirection方向に何日連続勤務か数える（夜入・夜明・代休は勤務扱い）"""
            count = 0
            d = base_date + timedelta(days=direction)
            for _ in range(14):  # 最大2週間分確認
                dks = d.strftime("%Y-%m-%d")
                status = result_so_far.get(dks, {}).get(sid, "")
                # 代休・未割当の休日は休み、夜入夜明と平日は勤務
                if status == "代休":
                    break
                if not is_work_day(d) and status not in ("夜入", "夜明"):
                    break
                if is_work_day(d) and status == "":
                    count += 1  # 通常平日勤務
                elif status in ("夜入", "夜明"):
                    count += 1
                else:
                    break
                d += timedelta(days=direction)
            return count

        # 夜入・夜明け自体の連続日数（土日含む）
        # 夜入前の連続勤務日数
        days_before = consecutive_work_days(d_in, result, sid, -1)
        # 夜明け後の連続勤務日数
        days_after  = consecutive_work_days(d_off, result, sid, 1)
        # 夜入+夜明け自体で2日
        total_streak = days_before + 2 + days_after

        # 代休配置: 連勤が6日を超えないよう前後に分散
        # kyukei=1: 前後どちらか連勤が多い側に配置
        # kyukei=2(土入+日明): 前に1日・後に1日に分散して連勤を分断

        # 夜明けが翌月の場合、後方の代休は翌月に配置してよい
        off_in_next_month = (d_off.month != month)

        def place_daykyu(start_date, direction, n, allow_next=False):
            """代休をn日配置して配置できた日数を返す（placed はローカル管理）"""
            _placed = 0
            # 希望休の日（当月平日）を代休優先候補として収集（6連勤チェックは簡易版）
            req_days_sorted = sorted(
                [dk for dk in req_off_days.get(sid, set())],
                reverse=(direction < 0)
            )
            for req_dk in req_days_sorted:
                if _placed >= n: break
                req_d = date(int(req_dk[:4]), int(req_dk[5:7]), int(req_dk[8:10]))
                if not is_work_day(req_d): continue
                cur = result.get(req_dk, {}).get(sid, "")
                if cur in ("夜入","夜明","代休","ICU代休","透析代休"): continue
                # 簡易6連勤チェック
                def _consec_n(direction2):
                    cnt2 = 0
                    d2 = req_d + timedelta(days=direction2)
                    for _ in range(7):
                        dks2 = d2.strftime("%Y-%m-%d")
                        st2 = result.get(dks2, {}).get(sid, "")
                        if st2 in ("代休","ICU代休","透析代休"): break
                        if not is_work_day(d2) and st2 not in ("夜入","夜明"): break
                        if is_work_day(d2) and st2 == "": cnt2 += 1
                        elif st2 in ("夜入","夜明"): cnt2 += 1
                        else: break
                        d2 += timedelta(days=direction2)
                    return cnt2
                if (_consec_n(-1) + _consec_n(1)) <= 5:
                    result.setdefault(req_dk, {})[sid] = "代休"
                    _placed += 1
            if _placed >= n:
                return _placed
            # 通常の代休配置（希望休で足りない分）
            # 2パス: 1回目は当番不足期間を避ける、2回目は無制限
            # ope2スタッフ（ope1スキルなし）の代休は月後半に優先配置
            # → 月前半にope2が代休で稼働不可になるのを防ぐ
            _is_ope2_only = (
                any(sk in ["ope1","ope2"] for sk in staff[sid].get("duty_skills",[]))
                and "ope1" not in staff[sid].get("duty_skills",[])
            )
            _days_before = (d_in - date(year, month, 1)).days
            _days_after  = (date(year, month, calendar.monthrange(year, month)[1]) - d_in).days
            if _is_ope2_only:
                # ope2は月後半（前向き）に代休を優先配置
                _primary_dir   = 1
                _secondary_dir = -1
            else:
                # それ以外は分散配置（前半→後ろ、後半→前）
                _primary_dir   = 1 if _days_before <= _days_after else -1
                _secondary_dir = -_primary_dir
            for _pass_d in range(2):
                if _placed >= n: break
                for _dir_d in [_primary_dir, _secondary_dir]:
                    if _placed >= n: break
                    check = start_date + timedelta(days=_dir_d)
                    _checked = 0
                    while _placed < n and _checked < 20:
                        if _dir_d < 0 and check.month != month: break
                        if _dir_d > 0 and not allow_next and check.month != month: break
                        if is_work_day(check):
                            dkc = check.strftime("%Y-%m-%d")
                            if result.get(dkc, {}).get(sid) not in ("夜入", "夜明", "代休"):
                                if _pass_d == 0 and sid in _protect_from_night.get(dkc, set()):
                                    check += timedelta(days=_dir_d); _checked += 1; continue
                                result.setdefault(dkc, {})[sid] = "代休"
                                _placed += 1
                        check += timedelta(days=_dir_d)
                        _checked += 1
            return _placed

        if kyukei == 2:
            placed_before = place_daykyu(d_in, -1, 1)
            if placed_before < 1:
                place_daykyu(d_off, 1, 2 - placed_before, allow_next=off_in_next_month)
            else:
                placed_after = place_daykyu(d_off, 1, 1, allow_next=off_in_next_month)
                if placed_after < 1:
                    place_daykyu(d_in, -1, 1)
        else:
            # 夜明けが翌月の土日祝の場合 → 代休は翌月に優先配置
            if off_in_next_month and not is_work_day(d_off):
                p = place_daykyu(d_off, 1, 1, allow_next=True)
                if p < 1:
                    place_daykyu(d_in, -1, 1)
            elif days_before >= days_after:
                p = place_daykyu(d_in, -1, 1)
                if p < 1:
                    place_daykyu(d_off, 1, 1, allow_next=off_in_next_month)
            else:
                p = place_daykyu(d_off, 1, 1, allow_next=off_in_next_month)
                if p < 1:
                    place_daykyu(d_in, -1, 1)

    return result, _protect_from_night

# ─────────────────────────────────────────────
# 月次自動割り当て（均等化グリーディ + 夜勤考慮）
# ─────────────────────────────────────────────
def auto_assign_month(year: int, month: int, data: dict) -> dict:
    _, num_days = calendar.monthrange(year, month)
    staff    = data["staff"]
    dept_cfg = data["dept_config"]

    work_days = [
        date(year, month, d) for d in range(1, num_days + 1)
        if is_work_day(date(year, month, d))
    ]
    if not work_days or not staff:
        return {}

    # ── 前月末シフトを参照（月またぎ夜勤・代休・連勤チェック用）──────
    prev_month_year  = year if month > 1 else year - 1
    prev_month_month = month - 1 if month > 1 else 12
    _, prev_nd = calendar.monthrange(prev_month_year, prev_month_month)

    # 前月の保存済みシフトから末尾7日分を取得
    prev_shifts = {}  # {date_key: {sid: status}}
    for _pd in range(max(1, prev_nd - 6), prev_nd + 1):
        _pdk = date(prev_month_year, prev_month_month, _pd).strftime("%Y-%m-%d")
        _pday = data["shifts"].get(_pdk, {})
        if _pday:
            prev_shifts[_pdk] = {k: v for k, v in _pday.items() if k != "_duty"}

    # 前月末の夜勤状態を確認 → 当月頭に影響するものを抽出
    # 「前月末日が夜入」→ 当月1日が夜明け（代休が必要）
    # 「前月末日が夜明」→ 当月1日は休（代休は前月で処理済みのはず）
    _prev_last_dk  = date(prev_month_year, prev_month_month, prev_nd).strftime("%Y-%m-%d")
    _prev_last_day = prev_shifts.get(_prev_last_dk, {})

    # 当月1日・2日のシフトに前月末の夜明を反映（シフト生成前に注入）
    _carry_over = {}  # {sid: status} 前月末夜入→当月1日夜明け
    for _sid, _st in _prev_last_day.items():
        if _st == "夜入":
            # 前月末夜入 → 当月1日は夜明け
            _carry_over[_sid] = "夜明"

    # 前月シフトをresultsの初期値として注入（連勤チェック参照用）
    # ※ 実際のシフト表には書き込まない（前月分は表示しない）
    prev_results_ref = dict(prev_shifts)  # 連勤チェック専用参照

    # ── 希望・当番不可情報を展開 ─────────────────────────────────
    # requests[sid][date_key] = "off_duty"|"no_duty"|"off_only"
    requests = data.get("requests", {})
    month_str = f"{year}-{month:02d}-"

    def req_off(sid, dk):
        """その日がその人の希望休（休暇希望あり）かどうか"""
        return requests.get(sid, {}).get(dk) in ("off_duty", "off_only")

    def req_no_duty(sid, dk):
        """その日がその人の当番不可かどうか"""
        return requests.get(sid, {}).get(dk) in ("off_duty", "no_duty")

    def req_any(sid, dk):
        """何らかの希望が入っているか"""
        return requests.get(sid, {}).get(dk) is not None

    # スタッフ別の希望休日セット（当月分）
    req_off_days   = {}   # {sid: set of date_key}
    req_no_duty_days = {} # {sid: set of date_key}
    for sid in staff:
        req_off_days[sid]     = {dk for dk,v in requests.get(sid,{}).items()
                                   if dk.startswith(month_str)
                                   and v == "off_duty"}
                                   # off_only（当番可）は代休優先配置の対象外
        req_no_duty_days[sid] = {dk for dk,v in requests.get(sid,{}).items()
                                   if dk.startswith(month_str)
                                   and v in ("off_duty","no_duty")}

    def would_cause_6consec(sid, dk_rest, results_so_far):
        """dk_restを休みにすると前後合計6連勤以下になるか（True=OK、False=NG）
        勤務扱い: 平日（未割当 or 日勤）・夜入・夜明・B（ICU土日）
        休み扱い: 代休系・希望休・土日祝（夜勤なし）
        前月末のシフトも参照して月またぎ連勤を正確に判定"""
        from datetime import timedelta as _tdx
        d_rest = date(int(dk_rest[:4]), int(dk_rest[5:7]), int(dk_rest[8:10]))
        def _get_status(d_):
            dks_ = d_.strftime("%Y-%m-%d")
            # 当月シフト優先、前月データは参照用
            st_ = results_so_far.get(dks_, {}).get(sid, "")
            if not st_:
                st_ = prev_results_ref.get(dks_, {}).get(sid, "")
            return st_
        def count_side(direction):
            cnt = 0
            d = d_rest + _tdx(days=direction)
            for _ in range(14):
                st_ = _get_status(d)
                if st_ in ("代休","ICU代休","透析代休","希望休"): break
                if not is_work_day(d) and st_ not in ("夜入","夜明","B"): break
                if is_work_day(d):
                    cnt += 1
                elif st_ in ("夜入","夜明","B"):
                    cnt += 1
                else:
                    break
                d += _tdx(days=direction)
            return cnt
        before = count_side(-1)
        after  = count_side(1)
        return (before + after) <= 5

    # ── カテ当番を先行して計算（夜勤の前に確定・連日禁止・完全均等化） ──
    _, _pre_nd = calendar.monthrange(year, month)
    _pre_days  = [date(year, month, d) for d in range(1, _pre_nd + 1)]
    _cate_sids = [sid for sid, s in staff.items() if s.get("duty_skills") == ["C"]]
    _cate_multi= [sid for sid, s in staff.items()
                  if "C" in s.get("duty_skills", [])
                  and any(sk in ["ope1","ope2"] for sk in s.get("duty_skills", []))]
    _multi_limit = 0  # 複数スキル持ち（濱浦・新堀）はバックアップ専用（通常は0回）
    _all_cate  = _cate_sids + _cate_multi
    _cate_count = {sid: 0 for sid in _all_cate}
    cate_duty_plan = {}  # {date_key: sid}

    # 全日31日を均等に配分:
    # 専任3名×9回=27 + 複数2名×2回=4 → 合計31 (目標)
    # グリーディ: 毎日「累積回数が最少かつ上限内かつ前日でない人」を選ぶ
    _dt = __import__("datetime")
    for _d in _pre_days:
        _dk      = _d.strftime("%Y-%m-%d")
        _prev_dk = (_d - _dt.timedelta(days=1)).strftime("%Y-%m-%d")
        _prev_sid = cate_duty_plan.get(_prev_dk)

        # 候補: 前日でない・上限内・当番不可でない
        def _eligible(s):
            if s == _prev_sid: return False
            if s in _cate_multi and _cate_count[s] >= _multi_limit: return False
            if req_no_duty(s, _dk): return False  # 当番不可希望を除外
            return True

        # 専任優先候補
        _single_ok = [s for s in _cate_sids if _eligible(s)]
        # 複数スキル候補（専任が全員連日の時のみ）
        _multi_ok  = [s for s in _cate_multi if _eligible(s)]

        # 専任が誰か使えれば専任のみで選ぶ
        if _single_ok:
            _cands = _single_ok
        elif _multi_ok:
            _cands = _multi_ok
        else:
            # 連日やむなし（専任から最少回数）
            _cands = sorted(_cate_sids, key=lambda s: _cate_count[s])

        # 累積最少順で選択（同回数はランダムにして偏りを防ぐ）
        random.shuffle(_cands)
        _cands.sort(key=lambda s: _cate_count[s])
        _chosen = _cands[0]
        cate_duty_plan[_dk] = _chosen
        _cate_count[_chosen] += 1

    # 複数スキル持ちが_multi_limit未満なら補完（専任の多い日を置き換え）
    for _msid in _cate_multi:
        _remaining = _multi_limit - _cate_count[_msid]
        if _remaining <= 0:
            continue
        # 専任で最も回数が多い日を複数スキル持ちに変更（連日にならない日）
        _sorted_days = sorted(
            [(dk, sid) for dk, sid in cate_duty_plan.items() if sid in _cate_sids],
            key=lambda x: -_cate_count[x[1]]
        )
        for _dk_c, _sid_c in _sorted_days:
            if _remaining <= 0: break
            _dc = date(int(_dk_c[:4]), int(_dk_c[5:7]), int(_dk_c[8:10]))
            _prev = (_dc - _dt.timedelta(1)).strftime("%Y-%m-%d")
            _next = (_dc + _dt.timedelta(1)).strftime("%Y-%m-%d")
            # 前後に同じ人がいない（連日禁止）・当番不可でない
            if (cate_duty_plan.get(_prev) != _msid
                    and cate_duty_plan.get(_next) != _msid
                    and not req_no_duty(_msid, _dk_c)):
                _cate_count[_sid_c] -= 1
                _cate_count[_msid]  += 1
                cate_duty_plan[_dk_c] = _msid
                _remaining -= 1

    # 専任間の均等化: 最多と最少の差が2以上なら置き換えで調整
    for _ in range(20):  # 最大20回試行
        _single_counts = {s: _cate_count[s] for s in _cate_sids}
        _max_s = max(_single_counts, key=_single_counts.get)
        _min_s = min(_single_counts, key=_single_counts.get)
        if _single_counts[_max_s] - _single_counts[_min_s] <= 1:
            break
        # 最多専任が担当する日の中で、前後が最少専任でない日を最少専任に置き換え
        _replaced = False
        for _dk_e, _sid_e in list(cate_duty_plan.items()):
            if _sid_e != _max_s: continue
            _de   = date(int(_dk_e[:4]), int(_dk_e[5:7]), int(_dk_e[8:10]))
            _prev_e = (_de - _dt.timedelta(1)).strftime("%Y-%m-%d")
            _next_e = (_de + _dt.timedelta(1)).strftime("%Y-%m-%d")
            if (cate_duty_plan.get(_prev_e) != _min_s
                    and cate_duty_plan.get(_next_e) != _min_s
                    and not req_no_duty(_min_s, _dk_e)):
                cate_duty_plan[_dk_e] = _min_s
                _cate_count[_max_s] -= 1
                _cate_count[_min_s] += 1
                _replaced = True
                break
        if not _replaced:
            break

    # ── ope当番不足期間の事前割り当て計算（夜勤計画より先に実施）──
    # opeスキル持ちが3名以上当番不可の連続期間を検出し、
    # 当番可能スタッフのペアを決定 → 夜勤計画に「この日この人は夜入NG」を通知
    _pre_ope_lock = {}  # {dk: [sid1, sid2]} 事前確定するope当番

    def _calc_pre_ope():
        _ope1_s = [s for s,v in data["staff"].items() if "ope1" in v.get("duty_skills",[])]
        _ope2_s = [s for s,v in data["staff"].items() if "ope2" in v.get("duty_skills",[])
                   and "ope1" not in v.get("duty_skills",[])]
        _ope_all_s = _ope1_s + _ope2_s
        _, _nd_s = calendar.monthrange(year, month)

        def _req_no(sid, dk):
            return requests.get(sid, {}).get(dk, "") in ("off_duty", "no_duty")

        # 不足期間を検出
        _periods = []
        _in, _start = False, None
        for _d in range(1, _nd_s + 1):
            _dk = date(year, month, _d).strftime("%Y-%m-%d")
            _no = [s for s in _ope_all_s if _req_no(s, _dk)]
            if len(_no) >= 3:
                if not _in: _in, _start = True, _d
            else:
                if _in: _periods.append((_start, _d - 1)); _in = False
        if _in: _periods.append((_start, _nd_s))

        for (_ps, _pe) in _periods:
            # 期間中ずっと当番可能なスタッフ
            _capable = set(_ope_all_s)
            for _d in range(_ps, _pe + 1):
                _dk = date(year, month, _d).strftime("%Y-%m-%d")
                _capable = {s for s in _capable if not _req_no(s, _dk)}
            _cap = sorted(_capable)
            if len(_cap) < 2: continue

            # ope1優先でペアを編成
            _c1 = [s for s in _cap if "ope1" in data["staff"][s].get("duty_skills",[])]
            _c2 = [s for s in _cap if s not in _c1]

            if len(_c1) >= 2 and len(_c2) >= 1:
                _pA = [_c1[0], _c2[0]]
                _pB = [_c1[1], _c2[1]] if len(_c2) > 1 else [_c1[1], _c2[0]]
            elif len(_c1) >= 2:
                _pA = [_c1[0], _c1[1]]
                _pB = [_c1[2], _c1[3]] if len(_c1) >= 4 else [_c1[0], _c1[1]]
            elif len(_c1) == 1 and len(_c2) >= 1:
                _pA = [_c1[0], _c2[0]]
                _pB = [_c2[1], _c1[0]] if len(_c2) > 1 else [_c1[0], _c2[0]]
            else:
                _pA = [_cap[0], _cap[1]]
                _pB = [_cap[0], _cap[1]]

            # 稼働日に隔日で割り当て
            _wdays = [date(year, month, _d)
                      for _d in range(_ps, _pe + 1)
                      if is_work_day(date(year, month, _d))]
            for _i, _pd in enumerate(_wdays):
                _pdk = _pd.strftime("%Y-%m-%d")
                _pre_ope_lock[_pdk] = list(_pA if _i % 2 == 0 else _pB)

    _calc_pre_ope()

    # 夜勤計画（カテ当番確定後に計算）
    night_plan, _shortage_protect = plan_night_shifts(year, month, data,
                                  cate_duty_plan=cate_duty_plan,
                                  requests=requests,
                                  prev_shifts=prev_results_ref,
                                  duty_locked=_pre_ope_lock)

    # ── 月またぎ処理: 前月末夜入→当月1日夜明けを注入 ──────────────
    _month1_dk = date(year, month, 1).strftime("%Y-%m-%d")
    for _sid, _st in _carry_over.items():
        # 当月1日に夜明けを注入（plan_night_shiftsが未割当の場合のみ）
        if night_plan.get(_month1_dk, {}).get(_sid) is None:
            night_plan.setdefault(_month1_dk, {})[_sid] = _st

    # 前月末夜入→当月1日夜明けの場合、代休が必要か判定して追加
    # （前月末が土日祝の夜入なら当月内に代休が必要）
    from datetime import timedelta as _td_co
    _prev_last_d = date(prev_month_year, prev_month_month, prev_nd)
    for _sid, _carry_st in _carry_over.items():
        # 前月末(夜入)と当月1日(夜明)が土日祝にかかるかチェック
        _kyukei_co = 0
        if not is_work_day(_prev_last_d):    _kyukei_co += 1  # 夜入が土日祝
        _d1 = date(year, month, 1)
        if not is_work_day(_d1):             _kyukei_co += 1  # 夜明が土日祝
        # 代休を当月内に配置
        for _ in range(_kyukei_co):
            _check = _d1 + _td_co(days=1)
            while _check.month == month:
                if is_work_day(_check):
                    _dkc = _check.strftime("%Y-%m-%d")
                    # まだ代休が入っていない平日に配置
                    if night_plan.get(_dkc, {}).get(_sid) not in ("夜入","夜明","代休"):
                        night_plan.setdefault(_dkc, {})[_sid] = "代休"
                        break
                _check += _td_co(days=1)

    dept_mains = {
        did: [sid for sid, s in staff.items() if s.get("main_dept") == did]
        for did in DEPT_IDS
    }
    main_count = {sid: 0 for sid in staff}
    results: dict[str, dict[str, str]] = {}

    # ── 管理者固定シフトの事前読み込み ───────────────────────────
    # data["shifts"][dk]["_locked"] に含まれるスタッフのシフトは上書き禁止
    # data["shifts"][dk]["_skip"]   に含まれるスタッフはその日の自動割り当てをスキップ
    # ただし requests（休暇・当番不可希望）は固定シフトより常に優先
    _locked_shifts: dict[str, dict[str, str]] = {}  # {dk: {sid: status}}
    _skip_shifts:   dict[str, set]            = {}  # {dk: set of sid}
    for _dk_l, _day_l in data.get("shifts", {}).items():
        _locked_sids = set(_day_l.get("_locked", []))
        _skip_sids   = set(_day_l.get("_skip",   []))
        if _locked_sids:
            _locked_shifts[_dk_l] = {s: _day_l[s] for s in _locked_sids if s in _day_l}
        if _skip_sids:
            _skip_shifts[_dk_l] = _skip_sids

    for work_date in work_days:
        dk         = work_date.strftime("%Y-%m-%d")
        assignment: dict[str, str] = {}
        dept_count = {d: 0 for d in DEPT_IDS}

        # 固定シフトを適用（requests が優先 — off_duty/off_only なら固定を無視）
        for sid, status in _locked_shifts.get(dk, {}).items():
            req_val = requests.get(sid, {}).get(dk, "")
            if req_val not in ("off_duty", "off_only", "no_duty"):
                assignment[sid] = status
        # _skip スタッフは assignment に追加しない（自動割り当ても後でスキップ）

        # 夜勤スタッフを先に確定（夜入・夜明・休）※固定シフト未指定の人のみ
        night_today = night_plan.get(dk, {})
        for sid, night_status in night_today.items():
            if sid not in assignment:  # 固定シフトがあれば夜勤計画に優先
                assignment[sid] = night_status

        # 夜勤スタッフを除いたリスト（固定・スキップも除外）
        _skip_today = _skip_shifts.get(dk, set())
        available = [sid for sid in staff
                     if sid not in assignment and sid not in _skip_today]

        # Step A: 各部署 min_staff 人をメインに割り当て（夜勤除外・累積少ない順）
        for did in DEPT_IDS:
            mains = [s for s in dept_mains[did] if s in available]
            min_s = dept_cfg[did]["min_staff"]
            if not mains:
                continue
            shuffled = mains[:]
            random.shuffle(shuffled)
            shuffled.sort(key=lambda s: main_count[s])
            for sid in shuffled[:min(min_s, len(mains))]:
                assignment[sid] = did
                dept_count[did] += 1

        # Step B: 未割当（夜勤でもメインでもない人）
        unassigned = [sid for sid in available if sid not in assignment]

        # Step C: 不足部署をサブで補完
        for did in DEPT_IDS:
            needed = dept_cfg[did]["min_staff"] - dept_count[did]
            if needed <= 0:
                continue
            candidates = [
                s for s in unassigned
                if did in staff[s].get("sub_depts", []) and s not in assignment
            ]
            random.shuffle(candidates)
            for sid in candidates:
                if needed <= 0:
                    break
                assignment[sid] = did
                dept_count[did] += 1
                needed -= 1

        # Step D: 残った unassigned → メイン部署へ
        for sid in unassigned:
            if sid not in assignment:
                md = staff[sid].get("main_dept", DEPT_IDS[0])
                assignment[sid] = md
                dept_count[md] += 1

        # 累積カウント更新（夜勤以外でメイン部署にいた人）
        for sid, dept in assignment.items():
            if dept == staff[sid].get("main_dept"):
                main_count[sid] += 1

        results[dk] = assignment

    # 土日祝・翌月またがりデータ（夜明け・代休）も results にマージ
    for dk, night_day in night_plan.items():
        d = date(int(dk[:4]), int(dk[5:7]), int(dk[8:10]))
        in_this_month = (d.month == month)
        _locked_on_day = set(_locked_shifts.get(dk, {}).keys())
        _skip_on_day   = _skip_shifts.get(dk, set())
        if not is_work_day(d):
            results.setdefault(dk, {})
            for sid, status in night_day.items():
                req_val = requests.get(sid, {}).get(dk, "")
                if sid not in _locked_on_day and sid not in _skip_on_day \
                        and req_val not in ("off_duty", "off_only"):
                    results[dk][sid] = status
        elif in_this_month:
            for sid, status in night_day.items():
                req_val = requests.get(sid, {}).get(dk, "")
                if dk in results and sid not in _locked_on_day \
                        and sid not in _skip_on_day \
                        and req_val not in ("off_duty", "off_only"):
                    results[dk][sid] = status
        else:
            results.setdefault(dk, {})
            for sid, status in night_day.items():
                req_val = requests.get(sid, {}).get(dk, "")
                if sid not in _locked_on_day and sid not in _skip_on_day \
                        and req_val not in ("off_duty", "off_only"):
                    results[dk][sid] = status

    # ── ICU土日祝日勤を割り当て ──────────────────────────────
    # 夜勤可能スタッフから均等に、土日祝1名をICUに配置し代休を付与
    from datetime import timedelta as td
    # ICU土日日勤候補: 夜勤可能かつ透析メインでないスタッフ
    # （透析スタッフは土曜に透析日勤・日曜に透析当番があるため除外）
    night_capable = [sid for sid, s in staff.items()
                     if s.get("night_shift", 0) == 1
                     and s.get("main_dept") != "D"]
    icu_weekend_count = {sid: 0 for sid in night_capable}

    _, num_days = calendar.monthrange(year, month)
    weekend_days = [
        date(year, month, d) for d in range(1, num_days + 1)
        if not is_work_day(date(year, month, d))
    ]

    # ── ICU代休配置ヘルパー（sid を引数で明示してクロージャ問題を回避） ──
    def icu_consec(sid_, base_d, direction):
        count = 0
        d = base_d + td(days=direction)
        for _ in range(14):
            dks    = d.strftime("%Y-%m-%d")
            status = results.get(dks, {}).get(sid_, "")
            if status in ("夜入","夜明","代休","ICU代休","透析代休"):
                break
            if not is_work_day(d) and status not in ("夜入","夜明","B"):
                break
            count += 1
            d += td(days=direction)
        return count

    # カテメインスタッフの代休: 6連勤未満・最小人数を下回らない平日に散らす
    _cate_daykyu_count = {}  # {date_key: 代休配置人数}

    def try_place_spread_for_cate(sid_):
        """カテスタッフの代休を散らして配置（6連勤チェック・最小人数制約）"""
        if staff.get(sid_, {}).get("main_dept") != "C":
            return False
        cate_min = data["dept_config"].get("C", {}).get("min_staff", 4)
        cate_sids_all = [s for s,si in staff.items() if si.get("main_dept") == "C"]
        _, _nd_c = calendar.monthrange(year, month)
        # 候補日: 当月平日で代休配置可能な日を収集、負荷（配置済み代休数）が少ない順
        candidates_c = []
        for _day_c in range(1, _nd_c+1):
            _d_c = date(year, month, _day_c)
            if not is_work_day(_d_c): continue
            _dk_c = _d_c.strftime("%Y-%m-%d")
            _cur_c = results.get(_dk_c, {}).get(sid_, "")
            if _cur_c in ("夜入","夜明","代休","ICU代休","透析代休","希望休"): continue
            if not would_cause_6consec(sid_, _dk_c, results): continue
            # その日のカテ部門稼働人数チェック（代休配置後も最小人数を下回らないか）
            _active = sum(
                1 for s in cate_sids_all
                if results.get(_dk_c, {}).get(s, "") not in
                   ("夜入","夜明","代休","ICU代休","透析代休","希望休")
                and s != sid_  # 対象者本人は除外
            )
            if _active < cate_min: continue  # 最小人数を下回る
            _load = _cate_daykyu_count.get(_dk_c, 0)
            candidates_c.append((_load, _dk_c))
        if not candidates_c:
            return False
        candidates_c.sort()
        _best_dk = candidates_c[0][1]
        results.setdefault(_best_dk, {})[sid_] = "ICU代休"
        _cate_daykyu_count[_best_dk] = _cate_daykyu_count.get(_best_dk, 0) + 1
        return True

    def icu_try_place(sid_, start_d, direction, allow_next=False, skip_shortage=True):
        check = start_d + td(days=direction)
        for _pass in range(2):  # 1回目: 当番不足期間を避ける / 2回目: 避けずに配置
            _check = start_d + td(days=direction)
            for _ in range(62):
                if direction < 0 and _check.month != month:
                    break
                if direction > 0 and not allow_next and _check.month != month:
                    break
                if is_work_day(_check):
                    dkc = _check.strftime("%Y-%m-%d")
                    cur = results.get(dkc, {}).get(sid_, "")
                    if cur in ("夜入","夜明","代休","ICU代休","透析代休"):
                        _check += td(days=direction); continue
                    # 1回目: 当番不足期間（保護スタッフの代休）は避ける
                    if _pass == 0 and skip_shortage:
                        if sid_ in _shortage_protect.get(dkc, set()):
                            _check += td(days=direction); continue
                    results.setdefault(dkc, {})[sid_] = "ICU代休"
                    return True
                _check += td(days=direction)
            # 1回目で見つからない場合は2回目（期間を無視）へ
        return False

    def place_icu_daykyu(sid_, wd_):
        """ICU代休を当月内に必ず配置。当月内不可の場合のみ翌月。前月遡り禁止"""
        days_before = icu_consec(sid_, wd_, -1)
        days_after  = icu_consec(sid_, wd_,  1)
        if days_before >= days_after:
            # 前方優先（当月内）→ 後方当月内 → 後方翌月
            if not icu_try_place(sid_, wd_, -1):
                if not icu_try_place(sid_, wd_, 1):
                    icu_try_place(sid_, wd_, 1, allow_next=True)
        else:
            # 後方優先（当月内）→ 前方当月内 → 後方翌月
            if not icu_try_place(sid_, wd_, 1):
                if not icu_try_place(sid_, wd_, -1):
                    icu_try_place(sid_, wd_, 1, allow_next=True)


    for wd in weekend_days:
        dk = wd.strftime("%Y-%m-%d")
        day_result = results.get(dk, {})

        candidates = [
            s for s in night_capable
            if day_result.get(s, "") not in ("夜入","夜明","代休","ICU代休")
        ]
        if not candidates:
            continue

        random.shuffle(candidates)
        # 当番不足期間の前2日：当番可能スタッフをICU日勤からも保護
        _icu_protected = _shortage_protect.get(dk, set())
        _icu_safe    = [s for s in candidates if s not in _icu_protected]
        _icu_protect = [s for s in candidates if s in _icu_protected]
        _icu_safe.sort(key=lambda s: icu_weekend_count[s])
        _icu_protect.sort(key=lambda s: icu_weekend_count[s])
        ordered = _icu_safe + _icu_protect
        if not ordered: continue
        chosen = ordered[0]
        results.setdefault(dk, {})[chosen] = "B"
        icu_weekend_count[chosen] += 1

        # 代休配置: 希望休の日を優先（6連勤チェック付き）
        _icu_placed = False
        for _req_dk in sorted(req_off_days.get(chosen, set())):
            if not _req_dk.startswith(f"{year}-{month:02d}-"): continue
            _req_d = date(int(_req_dk[:4]), int(_req_dk[5:7]), int(_req_dk[8:10]))
            if not is_work_day(_req_d): continue
            _cur = results.get(_req_dk, {}).get(chosen, "")
            if _cur in ("夜入","夜明","代休","ICU代休","透析代休"): continue
            if would_cause_6consec(chosen, _req_dk, results):
                results.setdefault(_req_dk, {})[chosen] = "ICU代休"
                _icu_placed = True
                break
        if not _icu_placed:
            # カテメインスタッフは散らし配置、それ以外は通常配置
            if not try_place_spread_for_cate(chosen):
                place_icu_daykyu(chosen, wd)

    # ── 透析土曜・祝日（日曜除く）日勤を割り当て ────────────────
    # 透析メインスタッフから均等に2名配置、代休必須、7連勤防止
    dialysis_staff = [sid for sid, s in staff.items() if s.get("main_dept") == "D"]
    dialysis_weekend_count = {sid: 0 for sid in dialysis_staff}

    # 土曜・祝日（日曜除く）→ 透析日勤+当番
    # 日曜 → 透析日勤なし・当番のみ
    dialysis_days = [
        date(year, month, d) for d in range(1, num_days + 1)
        if (lambda dd: (
            dd.weekday() == 5 or  # 土曜
            (HAS_JPHOLIDAY and jpholiday.is_holiday(dd) and dd.weekday() != 6)  # 日曜以外の祝日
        ))(date(year, month, d))
    ]
    dialysis_sunday = [
        date(year, month, d) for d in range(1, num_days + 1)
        if date(year, month, d).weekday() == 6  # 日曜のみ
    ]

    def consec_d(base_d, direction, sid):
        count = 0
        d = base_d + td(days=direction)
        for _ in range(14):
            dks = d.strftime("%Y-%m-%d")
            status = results.get(dks, {}).get(sid, "")
            if status in ("代休", "ICU代休", "透析代休"):
                break
            if not is_work_day(d) and status not in ("夜入", "夜明", "B", "D"):
                break
            count += 1
            d += td(days=direction)
        return count

    def place_dialysis_daykyu(sid, wd):
        """透析代休を「透析スタッフ全体の稼働人数が少ない日」に優先配置
        同じ人数なら連勤分散（前後どちらか連勤の少ない方）"""
        def _count_working_dialysis(dkc_):
            """その日に透析スタッフが何人稼働中か（代休・夜勤除く）
            透析代休が既に多い日を避けるため、代休数も加重して評価"""
            dr_ = results.get(dkc_, {})
            working = 0
            daykyu_count = 0
            for s_ in dialysis_staff:
                st_ = dr_.get(s_, "")
                if st_ in ("透析代休","代休","ICU代休"): daykyu_count += 1
                elif st_ not in ("夜入","夜明"):
                    working += 1  # 通常稼働
            # 代休が多い日は「実質稼働」が少ないのでスコアを下げる（代休日を避ける）
            return (daykyu_count * 10) - working  # スコアが低い日を優先

        days_before = consec_d(wd, -1, sid)
        days_after  = consec_d(wd,  1, sid)

        # 候補日: 当月の平日で代休配置可能な日を収集、稼働人数で優先順
        all_days_this_month = [
            date(year, month, d) for d in range(1, calendar.monthrange(year, month)[1]+1)
        ]
        # 前方候補（連勤が多い側を先に）
        def find_best_day(direction):
            check = wd + td(days=direction)
            candidates_ = []
            for _ in range(62):
                if direction < 0 and check.month != month: break
                if direction > 0 and check.month != month: break
                if is_work_day(check):
                    dkc = check.strftime("%Y-%m-%d")
                    cur = results.get(dkc, {}).get(sid, "")
                    if cur not in ("夜入","夜明","代休","ICU代休","透析代休","B","希望休"):
                        working_cnt = _count_working_dialysis(dkc)
                        candidates_.append((working_cnt, dkc))
                check += td(days=direction)
            if candidates_:
                candidates_.sort()  # 稼働人数が少ない日を優先
                return candidates_[0][1]
            return None

        placed = False
        if days_before >= days_after:
            best = find_best_day(-1) or find_best_day(1)
        else:
            best = find_best_day(1) or find_best_day(-1)
        if best:
            results.setdefault(best, {})[sid] = "透析代休"
            placed = True
        return placed

    for dd in dialysis_days:
        dk = dd.strftime("%Y-%m-%d")
        day_result = results.get(dk, {})

        # 透析日勤候補: duty_skills に "D" を持つスタッフで稼働可能・当番不可でない人
        _dial_skilled_sat = [s for s in dialysis_staff if "D" in staff[s].get("duty_skills", [])]
        d_candidates = [
            s for s in _dial_skilled_sat
            if day_result.get(s, "") not in ("夜入","夜明","代休","ICU代休","透析代休","希望休")
            and not req_no_duty(s, dk)
        ]
        if not d_candidates:
            # フォールバック: 当番不可を無視（人数不足）
            d_candidates = [
                s for s in _dial_skilled_sat
                if day_result.get(s, "") not in ("夜入","夜明","代休","ICU代休","透析代休","希望休")
            ]
        if not d_candidates:
            continue

        # 累積が少ない順に2名選んで透析日勤を割り当て
        random.shuffle(d_candidates)
        d_candidates.sort(key=lambda s: dialysis_weekend_count[s])
        chosen = d_candidates[:2]

        for sid in chosen:
            results.setdefault(dk, {})[sid] = "D"  # 透析日勤先に確定
            dialysis_weekend_count[sid] += 1
            # 透析代休: 希望休の日を優先
            _dial_placed = False
            for _req_dk in sorted(req_off_days.get(sid, set())):
                if not _req_dk.startswith(f"{year}-{month:02d}-"): continue
                _req_d = date(int(_req_dk[:4]), int(_req_dk[5:7]), int(_req_dk[8:10]))
                if not is_work_day(_req_d): continue
                _cur = results.get(_req_dk, {}).get(sid, "")
                if _cur in ("夜入","夜明","代休","ICU代休","透析代休"): continue
                if would_cause_6consec(sid, _req_dk, results):
                    results.setdefault(_req_dk, {})[sid] = "透析代休"
                    _dial_placed = True
                    break
            if not _dial_placed:
                place_dialysis_daykyu(sid, dd)

    # ── 平日当番割り当て（ICU・カテ・透析）──────────────────────
    # 全当番可能スタッフの合計当番回数を均等化。
    # 原則メイン部門の当番に入れる。均等が崩れる場合は他部門スキルも利用。
    DUTY_DEPTS = ["B", "C", "D"]  # ICU・カテ・透析

    # 当番スキル保持者（いずれかの部門スキルを持つ全スタッフ）
    all_duty_skills = set()
    for s in staff.values():
        all_duty_skills.update(s.get("duty_skills", []))
    duty_capable = [sid for sid, s in staff.items()
                    if any(d in s.get("duty_skills", []) for d in DUTY_DEPTS)]

    # 全体合計当番回数（部門またいで・オペ含む）
    ope_duty_capable = [sid for sid, s in staff.items()
                        if any(sk in s.get("duty_skills", []) for sk in ["ope1","ope2"])]
    all_capable = list(set(duty_capable + ope_duty_capable))
    total_duty_count = {sid: 0 for sid in all_capable}
    # 部門ごとの複数スキル持ちの当番回数上限を計算
    multi_dept_count  = {sid: 0 for sid in all_capable}
    cate_actual_count = {}  # カテ当番実績カウント（均等化維持用）

    # 部門ごとの複数スキル持ちの当番上限を事前計算
    _, _nd = calendar.monthrange(year, month)
    dept_multi_limit = {}
    for _did in DUTY_DEPTS:
        _single_cnt = sum(1 for s,si in staff.items()
                          if _did in si.get("duty_skills",[])
                          and not any(sk in ["ope1","ope2"] for sk in si.get("duty_skills",[])))
        _multi_cnt  = sum(1 for s,si in staff.items()
                          if _did in si.get("duty_skills",[])
                          and any(sk in ["ope1","ope2"] for sk in si.get("duty_skills",[])))
        if _single_cnt > 0 and _multi_cnt > 0:
            _target_single = round(_nd * 0.75 / _single_cnt)
            _remaining     = max(0, _nd - _target_single * _single_cnt)
            dept_multi_limit[_did] = max(1, (_remaining + _multi_cnt - 1) // _multi_cnt)
        else:
            dept_multi_limit[_did] = _nd

    duty_shifts = {}  # {date_key: {dept_id: sid}}

    # カテ(C)・ICU(B)・透析(D)・オペは土日祝も当番対象
    DUTY_ALL_DAYS = ["C", "B", "D"]  # 土日祝も当番
    DUTY_WEEKDAY  = []               # 平日のみ（現在なし）

    from datetime import timedelta as _td
    _, num_days_all = calendar.monthrange(year, month)
    all_days_list = [date(year, month, d) for d in range(1, num_days_all + 1)]

    for target_date in all_days_list:
        dk = target_date.strftime("%Y-%m-%d")
        dk_prev = (target_date - _td(days=1)).strftime("%Y-%m-%d")
        day_assign = results.get(dk, {})
        if dk not in duty_shifts:
            duty_shifts[dk] = {}
        busy = ("夜入","夜明","代休","ICU代休","透析代休","希望休")
        is_wd = is_work_day(target_date)

        # 土日祝のICU日勤者は全当番から除外
        icu_weekend_busy = set()
        if not is_wd:
            icu_weekend_busy = {s for s, v in day_assign.items()
                                if isinstance(v, str) and v == "B"}

        for did in DUTY_DEPTS:
            # 平日のみ対象の部門は土日祝スキップ
            if did in DUTY_WEEKDAY and not is_wd:
                continue
            # その日すでにオペ当番に入っている人を除外（二重当番防止）
            already_ope = set(duty_shifts.get(dk, {}).get("ope", []))
            # このdidの当番スキルを持つ全スタッフ（稼働中・ICU土日日勤除く・当番不可除く）
            skilled = [
                sid for sid, s in staff.items()
                if did in s.get("duty_skills", [])
                and day_assign.get(sid) not in busy
                and sid not in icu_weekend_busy
                and sid not in already_ope
                and not req_no_duty(sid, dk)  # 当番不可希望を除外
            ]
            # 透析(D)当番は土日祝のみ、その日透析日勤(D)の人に限定
            if did == "D" and not is_wd:
                _d_workers = {s for s, v in day_assign.items() if v == "D" and s != "_duty"}
                skilled = [s for s in skilled if s in _d_workers]
            if not skilled:
                continue

            # 前日に何らかの当番をしたスタッフを取得（全部門またいで連日回避）
            prev_duty_all = set()
            prev_duties = duty_shifts.get(dk_prev, {})
            for _d, _v in prev_duties.items():
                if _d == "ope":
                    prev_duty_all.update(_v)
                elif isinstance(_v, str):
                    prev_duty_all.add(_v)

            # 複数スキル持ち（opeスキルも持つ）はカテ等の部門当番を最後の手段にする
            def is_single_skill(sid_):
                skills = staff[sid_].get("duty_skills", [])
                ope_skills = {"ope1", "ope2"}
                has_ope = any(sk in ope_skills for sk in skills)
                non_ope = [sk for sk in skills if sk not in ope_skills]
                return not has_ope or len(non_ope) == 0

            # カテ(C)当番は事前計画から直接取得
            if did == "C":
                _pre_chosen = cate_duty_plan.get(dk)
                _prev_cate  = duty_shifts.get(dk_prev, {}).get("C")
                if (_pre_chosen
                        and day_assign.get(_pre_chosen) not in busy
                        and _pre_chosen not in icu_weekend_busy
                        and _pre_chosen != _prev_cate
                        and not req_no_duty(_pre_chosen, dk)):
                    chosen = _pre_chosen
                else:
                    # フォールバック: カテ専任を最優先、全員稼働不可時のみ多スキル持ちを使用
                    _cate_single_skilled = [s for s in skilled
                                            if is_single_skill(s)
                                            and day_assign.get(s) not in busy
                                            and s not in icu_weekend_busy
                                            and s != _prev_cate
                                            and not req_no_duty(s, dk)]
                    _cate_multi_skilled  = [s for s in skilled
                                            if not is_single_skill(s)
                                            and day_assign.get(s) not in busy
                                            and s not in icu_weekend_busy
                                            and s != _prev_cate
                                            and not req_no_duty(s, dk)]
                    # 専任連日もやむなし
                    _cate_single_any = [s for s in skilled if is_single_skill(s)
                                        and day_assign.get(s) not in busy
                                        and s not in icu_weekend_busy
                                        and not req_no_duty(s, dk)]
                    # 優先順位: 専任（連日なし）→ 専任（連日あり）→ 多スキル
                    _fb = (_cate_single_skilled or _cate_single_any or _cate_multi_skilled
                           or skilled[:])
                    _fb.sort(key=lambda s: cate_actual_count.get(s, 0))
                    chosen = _fb[0] if _fb else None
                if chosen:
                    duty_shifts[dk][did] = chosen
                    total_duty_count[chosen] += 1
                    cate_actual_count[chosen] = cate_actual_count.get(chosen, 0) + 1
                continue

            # C以外の部門: 専任優先・均等化
            single_avail = [s for s in skilled
                            if is_single_skill(s) and s not in prev_duty_all]
            if not single_avail:
                single_avail = [s for s in skilled if is_single_skill(s)]
            multi_avail = [s for s in skilled
                           if not is_single_skill(s) and s not in prev_duty_all]
            multi_over  = [s for s in skilled
                           if not is_single_skill(s)
                           and multi_dept_count.get(s, 0) >= dept_multi_limit.get(did, num_days_all)]
            fallback    = skilled[:]

            if single_avail:
                candidates = single_avail
            else:
                candidates = multi_avail or multi_over or fallback

            random.shuffle(candidates)
            candidates.sort(key=lambda s: total_duty_count[s])
            chosen = candidates[0]
            if not is_single_skill(chosen):
                multi_dept_count[chosen] = multi_dept_count.get(chosen, 0) + 1

            duty_shifts[dk][did] = chosen
            total_duty_count[chosen] += 1

    # ── オペ当番割り当て（平日のみ・オペ1必須1名+もう1名） ──────
    ope1_staff = [sid for sid, s in staff.items()
                  if "ope1" in s.get("duty_skills", [])]
    ope2_staff = [sid for sid, s in staff.items()
                  if "ope2" in s.get("duty_skills", [])]
    # オペ当番候補 = ope1 or ope2 スキル持ち
    ope_all    = list(set(ope1_staff + ope2_staff))

    ope_count  = {sid: 0 for sid in ope_all}  # 当番回数
    ope_prev   = set()  # 前日に当番したsidのセット
    prev_work_date = None  # 前日

    # オペ当番は土日祝も対象
    _, num_days_ope = calendar.monthrange(year, month)
    ope_target_days = [date(year, month, d) for d in range(1, num_days_ope + 1)]

    # ── ope当番不足期間の事前割り当て（_pre_ope_lockを転記）────────
    # 夜勤計画前に計算した_pre_ope_lockをduty_shiftsに反映
    _pre_assigned = {}
    for _pdk, _ppair in _pre_ope_lock.items():
        _pre_assigned[_pdk] = list(_ppair)
        duty_shifts.setdefault(_pdk, {})["ope"] = list(_ppair)
        for _ps in _ppair:
            ope_count[_ps] = ope_count.get(_ps, 0) + 1
            total_duty_count[_ps] = total_duty_count.get(_ps, 0) + 1

    # ── メインope当番ループ ───────────────────────────────────────
    for work_date in ope_target_days:
        dk = work_date.strftime("%Y-%m-%d")

        # 事前割り当て済みの日はスキップ
        if dk in _pre_assigned:
            ope_prev = set(_pre_assigned[dk])
            prev_work_date = work_date
            continue

        day_assign = results.get(dk, {})
        busy = ("夜入","夜明","代休","ICU代休","透析代休","希望休")

        # その日当番可能なope1・ope2
        # 土日祝のICU日勤者は全当番（オペ含む）から除外
        icu_wd_busy = set()
        day_assign_ope = results.get(dk, {})
        if not is_work_day(work_date):
            icu_wd_busy = {s for s, v in day_assign_ope.items()
                           if isinstance(v, str) and v == "B"}
        # その日すでにICU/カテ/透析当番に入っている人はオペ当番から除外（二重当番防止）
        already_duty = {v for k,v in duty_shifts.get(dk,{}).items()
                        if k in ["B","C","D"] and isinstance(v,str)}
        avail_ope1 = [s for s in ope1_staff
                      if day_assign.get(s) not in busy
                      and s not in icu_wd_busy
                      and s not in already_duty
                      and not req_no_duty(s, dk)
                      and not (not is_work_day(work_date) and day_assign.get(s) == "B")]
        avail_all  = [s for s in ope_all
                      if day_assign.get(s) not in busy
                      and s not in icu_wd_busy
                      and s not in already_duty
                      and not req_no_duty(s, dk)
                      and not (not is_work_day(work_date) and day_assign.get(s) == "B")]

        # 前日にopeを担当したスタッフのみを除外（B/C/D当番は連日対象外）
        from datetime import timedelta as _tdd
        prev_dk = (work_date - _tdd(days=1)).strftime("%Y-%m-%d")
        cur_prev = set(duty_shifts.get(prev_dk, {}).get("ope", []))

        # 翌日の当番不可者を先読みして今日の選択を調整
        from datetime import timedelta as _tdd_look
        _tomorrow_dk = (work_date + _tdd_look(days=1)).strftime("%Y-%m-%d")
        _tomorrow_no_duty = {s for s in ope_all if req_no_duty(s, _tomorrow_dk)}
        _tomorrow_capable_count = len(ope_all) - len(_tomorrow_no_duty)

        # 翌日の当番可能者が少ない場合 → 翌日も当番可能な人を今日は後回しにする
        def _sort_key(s, prefer_shift=True):
            tomorrow_capable = s not in _tomorrow_no_duty
            return (
                s in cur_prev,  # 連日は最悪
                # 翌日候補が4名以下なら、翌日可能な人は今日後回し
                (1 if tomorrow_capable and _tomorrow_capable_count <= 4 else 0) if prefer_shift else 0,
                total_duty_count.get(s, 0)
            )

        # ope当番は「ope1から1名 + ope2から1名」の固定ペア構成
        # ope1候補（当番数最少・連日なし・稼働可）
        _avail_o1 = [s for s in avail_ope1 if s not in cur_prev] or avail_ope1[:]
        _avail_o1.sort(key=lambda s: total_duty_count.get(s, 0))

        # ope2専任候補（ope1スキルなし・当番数最少・連日なし優先・稼働可）
        _avail_o2_nc = [s for s in avail_all
                        if "ope1" not in staff.get(s,{}).get("duty_skills",[])
                        and s not in cur_prev]  # 連日なし優先
        _avail_o2_fb = [s for s in avail_all
                        if "ope1" not in staff.get(s,{}).get("duty_skills",[])]  # フォールバック
        _avail_o2 = _avail_o2_nc if _avail_o2_nc else _avail_o2_fb
        _avail_o2.sort(key=lambda s: total_duty_count.get(s, 0))

        chosen1 = _avail_o1[0] if _avail_o1 else None  # ope1枠
        chosen2 = _avail_o2[0] if _avail_o2 else None  # ope2枠

        # フォールバック: ope2が全員稼働不可 → ope1から2名
        if not chosen2 and _avail_o1:
            _alts = [s for s in _avail_o1 if s != chosen1]
            chosen2 = _alts[0] if _alts else None

        # フォールバック: ope1が全員稼働不可 → ope2を1人目にしope1を緊急確保
        if not chosen1:
            chosen1 = chosen2; chosen2 = None
            _o1_emg = sorted(
                [s for s in ope1_staff
                 if s != chosen1
                 and results.get(dk,{}).get(s,"") not in
                    ("夜入","夜明","代休","ICU代休","透析代休","希望休")
                 and not req_no_duty(s, dk)],
                key=lambda s: total_duty_count.get(s, 0))
            if _o1_emg:
                chosen2 = chosen1; chosen1 = _o1_emg[0]

        if not chosen1:
            ope_prev = set()
            prev_work_date = work_date
            continue

        if not chosen2:
            duty_shifts.setdefault(dk, {})["ope"] = [chosen1]
            ope_count[chosen1] += 1
            if chosen1 in total_duty_count: total_duty_count[chosen1] += 1
            ope_prev = {chosen1}
            prev_work_date = work_date
            continue

        duty_shifts.setdefault(dk, {})["ope"] = [chosen1, chosen2]
        ope_count[chosen1] += 1
        ope_count[chosen2] += 1
        if chosen1 in total_duty_count: total_duty_count[chosen1] += 1
        if chosen2 in total_duty_count: total_duty_count[chosen2] += 1
        ope_prev = {chosen1, chosen2}
        prev_work_date = work_date

    # ── 日曜の透析当番（日勤なし・当番のみ） ─────────────────────
    # 日曜透析当番: duty_skills に "D" を持つスタッフのみ対象
    _dial_duty_skilled = [s for s in dialysis_staff if "D" in staff[s].get("duty_skills", [])]
    _dial_duty_count_sun = {sid: 0 for sid in _dial_duty_skilled}
    from datetime import timedelta as _tde_sun
    for _sun in dialysis_sunday:
        _sun_dk   = _sun.strftime("%Y-%m-%d")
        _sun_prev = (_sun - _tde_sun(days=1)).strftime("%Y-%m-%d")
        _sun_res  = results.get(_sun_dk, {})
        # 前日に当番していた人を除外（連日回避）
        _prev_duty_sun = set()
        for _k, _v in duty_shifts.get(_sun_prev, {}).items():
            if _k == "ope": _prev_duty_sun.update(_v)
            elif isinstance(_v, str): _prev_duty_sun.add(_v)
        _sun_cands = [
            s for s in _dial_duty_skilled
            if _sun_res.get(s, "") not in ("夜入","夜明","代休","ICU代休","透析代休","希望休")
            and s not in _prev_duty_sun
            and not req_no_duty(s, _sun_dk)
        ]
        if not _sun_cands:
            # フォールバック: 連日制約を外すが当番スキル・当番不可は維持
            _sun_cands = [
                s for s in _dial_duty_skilled
                if _sun_res.get(s, "") not in ("夜入","夜明","代休","ICU代休","透析代休","希望休")
                and not req_no_duty(s, _sun_dk)
            ]
        if not _sun_cands:
            # 最終フォールバック: 当番不可も無視（人数不足時のみ）
            _sun_cands = [
                s for s in _dial_duty_skilled
                if _sun_res.get(s, "") not in ("夜入","夜明","代休","ICU代休","透析代休","希望休")
            ]
        if not _sun_cands:
            continue
        random.shuffle(_sun_cands)
        _sun_cands.sort(key=lambda s: _dial_duty_count_sun[s])
        _sun_chosen = _sun_cands[0]
        duty_shifts.setdefault(_sun_dk, {})["D"] = _sun_chosen
        total_duty_count[_sun_chosen] = total_duty_count.get(_sun_chosen, 0) + 1
        _dial_duty_count_sun[_sun_chosen] += 1

    # ── 後処理均等化 ────────────────────────────────────────────
    # 共通チェック: 前後連日チェック（全部門またいで）
    def _duty_set(dk_):
        s = set()
        for k,v in duty_shifts.get(dk_,{}).items():
            if k=="ope": s.update(v)
            elif isinstance(v,str): s.add(v)
        return s

    def _ok_to_place(sid_, dk_):
        """sid_をdk_に当番配置できるか（稼働・連日・二重・ICU土日日勤・当番不可チェック）"""
        from datetime import timedelta as _tdd2
        d_ = date(int(dk_[:4]),int(dk_[5:7]),int(dk_[8:10]))
        pr = (d_-_tdd2(1)).strftime("%Y-%m-%d")
        nx = (d_+_tdd2(1)).strftime("%Y-%m-%d")
        if results.get(dk_,{}).get(sid_) in ("夜入","夜明","代休","ICU代休","透析代休"):
            return False
        # 当番不可希望を除外
        if req_no_duty(sid_, dk_):
            return False
        # 土日祝のICU日勤者は当番不可
        if not is_work_day(d_) and results.get(dk_,{}).get(sid_) == "B":
            return False
        if sid_ in _duty_set(pr): return False
        if sid_ in _duty_set(nx): return False
        return True

    def _ok_to_swap_ope(sid_, dk_, require_ope1_preserved=False):
        """opeスワップ用（連日・稼働・当番不可・ICU日勤・ope1必須チェック）"""
        from datetime import timedelta as _tdd2
        d_ = date(int(dk_[:4]),int(dk_[5:7]),int(dk_[8:10]))
        pr = (d_-_tdd2(1)).strftime("%Y-%m-%d")
        nx = (d_+_tdd2(1)).strftime("%Y-%m-%d")
        _st = results.get(dk_,{}).get(sid_,"")
        # 稼働不可シフトは絶対NG
        if _st in ("夜入","夜明","代休","ICU代休","透析代休"):
            return False
        # 土日祝のICU日勤（B）も当番と重複不可
        if not is_work_day(d_) and _st == "B":
            return False
        if req_no_duty(sid_, dk_): return False
        if sid_ in _duty_set(pr): return False
        if sid_ in _duty_set(nx): return False
        dept_today = {v for k,v in duty_shifts.get(dk_,{}).items()
                      if k in ["B","C","D"] and isinstance(v,str)}
        if sid_ in dept_today: return False
        # ope1必須チェック: スワップ後もope1が1名以上いるか
        if require_ope1_preserved:
            _cur_ope = set(duty_shifts.get(dk_,{}).get("ope",[]))
            _ope1_cur = [s for s in _cur_ope if "ope1" in staff.get(s,{}).get("duty_skills",[])]
            # スワップ先がope2のみで、現在ope1が1名しかいない場合はNG
            if (len(_ope1_cur) <= 1
                    and "ope1" not in staff.get(sid_,{}).get("duty_skills",[])
                    and all("ope1" in staff.get(s,{}).get("duty_skills",[]) for s in _ope1_cur)):
                return False
        return True

    def _has_consec_ope():
        """duty_shiftsに連日ope当番が存在するか確認"""
        from datetime import timedelta as _tdd3
        _dks = sorted(duty_shifts.keys())
        for _i in range(len(_dks)-1):
            _d1, _d2 = _dks[_i], _dks[_i+1]
            _dd1 = date(int(_d1[:4]),int(_d1[5:7]),int(_d1[8:10]))
            _dd2 = date(int(_d2[:4]),int(_d2[5:7]),int(_d2[8:10]))
            if (_dd2 - _dd1).days != 1:
                continueontinue
            _o1 = set(duty_shifts[_d1].get("ope",[]))
            _o2 = set(duty_shifts[_d2].get("ope",[]))
            if _o1 & _o2:
                return True, _d1, _o1 & _o2
        return False, None, None

    _single_sids = [sid for sid, s in staff.items() if s.get("duty_skills") == ["C"]]
    _multi_cate  = [sid for sid, s in staff.items()
                    if "C" in s.get("duty_skills", [])
                    and any(sk in ["ope1","ope2"] for sk in s.get("duty_skills", []))]
    _ope_skilled = [sid for sid, s in staff.items()
                    if any(sk in ["ope1","ope2"] for sk in s.get("duty_skills", []))]

    def _cate_counts():
        return _Counter(v.get("C") for v in duty_shifts.values() if "C" in v)
    def _ope_counts():
        cnt = _Counter()
        for dv in duty_shifts.values():
            for s in dv.get("ope",[]): cnt[s]+=1
        return cnt
    def _total_counts():
        cnt = _Counter()
        for dv in duty_shifts.values():
            for did,v in dv.items():
                if did=="ope":
                    for s in v: cnt[s]+=1
                elif isinstance(v,str): cnt[v]+=1
        return cnt

    from collections import Counter as _Counter

    # Step1: カテ専任間の均等化（差1以内）
    for _iter in range(50):
        _cr = _cate_counts()
        if not _single_sids: break
        _max_s = max(_single_sids, key=lambda s: _cr.get(s,0))
        _min_s = min(_single_sids, key=lambda s: _cr.get(s,0))
        if _cr.get(_max_s,0) - _cr.get(_min_s,0) <= 1: break
        _swapped = False
        for _dk_sw, _dv_sw in sorted(duty_shifts.items()):
            if _dv_sw.get("C") != _max_s: continue
            if _ok_to_place(_min_s, _dk_sw):
                duty_shifts[_dk_sw]["C"] = _min_s
                _swapped = True; break
        if not _swapped: break

    # Step2: ICU当番均等化（専任10回目標、ジョーカーで調整）
    _icu_single = [sid for sid,s in staff.items() if s.get("duty_skills")==["B"]]
    _icu_joker  = [sid for sid,s in staff.items()
                   if "B" in s.get("duty_skills",[])
                   and any(sk in ["ope1","ope2"] for sk in s.get("duty_skills",[]))]
    for _iter in range(50):
        _ic = _Counter()
        for dv in duty_shifts.values():
            v=dv.get("B")
            if v: _ic[v]+=1
        _single_over  = [s for s in _icu_single if _ic.get(s,0) > 10]
        _single_under = [s for s in _icu_single if _ic.get(s,0) < 10]
        if not _single_over: break
        _icu_max = max(_single_over, key=lambda s: _ic.get(s,0))
        _candidates = _single_under or _icu_joker
        if not _candidates: break
        _icu_min = min(_candidates, key=lambda s: _ic.get(s,0))
        _swapped = False
        _all_dk_ic = list(duty_shifts.keys()); random.shuffle(_all_dk_ic)
        for _dk_ic in _all_dk_ic:
            if duty_shifts[_dk_ic].get("B") != _icu_max: continue
            _already_ope = set(duty_shifts.get(_dk_ic,{}).get("ope",[]))
            if _icu_min in _already_ope: continue
            # ICU土日日勤者への当番割り当て禁止
            _dk_ic_d = date(int(_dk_ic[:4]),int(_dk_ic[5:7]),int(_dk_ic[8:10]))
            _is_wd_ic = is_work_day(_dk_ic_d)
            if not _is_wd_ic:
                _icu_day_shift = results.get(_dk_ic,{}).get(_icu_min,"")
                if _icu_day_shift == "B": continue  # ICU日勤者は当番不可
            if _ok_to_place(_icu_min, _dk_ic):
                duty_shifts[_dk_ic]["B"] = _icu_min
                _swapped = True; break
        if not _swapped: break

    # Step3: ope1グループ内の均等化・ope2グループ内の均等化（差2以内）
    _ope1_group = [s for s in _ope_skilled if "ope1" in staff[s].get("duty_skills",[])]
    _ope2_group = [s for s in _ope_skilled if "ope1" not in staff[s].get("duty_skills",[])]

    for _grp in [_ope1_group, _ope2_group]:
        if len(_grp) < 2: continue
        for _iter in range(200):
            _tc = _total_counts()
            _gv = {s: _tc.get(s,0) for s in _grp}
            _gmax = max(_gv, key=_gv.get)
            _gmin = min(_gv, key=_gv.get)
            if _gv[_gmax] - _gv[_gmin] <= 2: break
            _all_dk = list(duty_shifts.keys()); random.shuffle(_all_dk)
            _swapped = False
            for _tgt in sorted(_grp, key=lambda s: _gv[s]):
                if _gv[_tgt] >= _gv[_gmax] - 1: continue
                for _dk_op in _all_dk:
                    if _gmax not in duty_shifts[_dk_op].get("ope",[]): continue
                    if _ok_to_swap_ope(_tgt, _dk_op):
                        _ope_list = duty_shifts[_dk_op]["ope"]
                        duty_shifts[_dk_op]["ope"] = [_tgt if s==_gmax else s for s in _ope_list]
                        _swapped = True; break
                if _swapped: break
            if not _swapped: break

    # Step3b: カテ複数スキル→opeへ移譲、その分カテを専任へ
    for _iter in range(20):
        _oc = _ope_counts(); _cr = _cate_counts()
        if not _multi_cate: break
        _ope_max_s = max(_oc, key=_oc.get) if _oc else None
        _multi_min_ope = min(_multi_cate, key=lambda s: _oc.get(s,0))
        if not _ope_max_s: break
        if _oc.get(_ope_max_s,0) - _oc.get(_multi_min_ope,0) <= 1: break
        _all_dk = list(duty_shifts.keys()); random.shuffle(_all_dk)
        _swapped = False
        for _dk_op in _all_dk:
            if _ope_max_s not in duty_shifts[_dk_op].get("ope",[]): continue
            if _ok_to_swap_ope(_multi_min_ope, _dk_op):
                _ope_list = duty_shifts[_dk_op]["ope"]
                duty_shifts[_dk_op]["ope"] = [_multi_min_ope if s==_ope_max_s else s for s in _ope_list]
                # カテ削減: 複数スキルのカテを専任最少者へ
                _cr2 = _cate_counts()
                _cate_min = min(_single_sids, key=lambda s: _cr2.get(s,0)) if _single_sids else None
                _cate_multi_max = max(_multi_cate, key=lambda s: _cr2.get(s,0)) if _multi_cate else None
                if _cate_min and _cate_multi_max and _cr2.get(_cate_multi_max,0) > 1:
                    for _dk_ct, _dv_ct in sorted(duty_shifts.items()):
                        if _dv_ct.get("C") != _cate_multi_max: continue
                        if _ok_to_place(_cate_min, _dk_ct):
                            duty_shifts[_dk_ct]["C"] = _cate_min; break
                _swapped = True; break
        if not _swapped: break

    # Step4: 透析当番均等化（差2以内）
    _dial_single = [sid for sid,s in staff.items() if s.get("duty_skills")==["D"]]
    for _iter in range(100):
        _dc = _Counter()
        for dv in duty_shifts.values():
            v=dv.get("D")
            if v: _dc[v]+=1
        if not _dial_single: break
        _d_max = max(_dial_single, key=lambda s: _dc.get(s,0))
        _d_min = min(_dial_single, key=lambda s: _dc.get(s,0))
        if _dc.get(_d_max,0) - _dc.get(_d_min,0) <= 2: break
        _swapped = False
        _all_dk_d = list(duty_shifts.keys()); random.shuffle(_all_dk_d)
        for _dk_d in _all_dk_d:
            if duty_shifts[_dk_d].get("D") != _d_max: continue
            _d_d = date(int(_dk_d[:4]),int(_dk_d[5:7]),int(_dk_d[8:10]))
            if not is_work_day(_d_d) and _d_d.weekday() != 6:
                _d_workers_eq = {s for s,v in results.get(_dk_d,{}).items()
                                 if v == "D" and s != "_duty"}
                if _d_min not in _d_workers_eq: continue
            if _ok_to_place(_d_min, _dk_d):
                duty_shifts[_dk_d]["D"] = _d_min
                _swapped = True; break
        if not _swapped: break

    # Step5: 最終ope再均等化（グループ別・差2以内）
    for _grp5 in [_ope1_group, _ope2_group]:
        if len(_grp5) < 2: continue
        for _iter in range(200):
            _tc = _total_counts()
            _gv5 = {s: _tc.get(s,0) for s in _grp5}
            _gmax5 = max(_gv5, key=_gv5.get)
            _gmin5 = min(_gv5, key=_gv5.get)
            if _gv5[_gmax5] - _gv5[_gmin5] <= 2: break
            _all_dk = list(duty_shifts.keys()); random.shuffle(_all_dk)
            _swapped = False
            for _tgt in sorted(_grp5, key=lambda s: _gv5[s]):
                if _gv5[_tgt] >= _gv5[_gmax5] - 1: continue
                for _dk_op in _all_dk:
                    if _gmax5 not in duty_shifts[_dk_op].get("ope",[]): continue
                    if _ok_to_swap_ope(_tgt, _dk_op):
                        _ope_list = duty_shifts[_dk_op]["ope"]
                        duty_shifts[_dk_op]["ope"] = [_tgt if s==_gmax5 else s for s in _ope_list]
                        _swapped = True; break
                if _swapped: break
            if not _swapped: break

    # Step5b: ope1スキルなしの日を修正（ope2のみの日にope1を投入）
    for _dk_5b in sorted(duty_shifts.keys()):
        _ope_5b = duty_shifts[_dk_5b].get("ope", [])
        if not _ope_5b: continue
        if any("ope1" in staff.get(s,{}).get("duty_skills",[]) for s in _ope_5b):
            continue  # すでにope1がいる
        # ope1を投入できる候補を探す（当番数最少・稼働可能・連日なし）
        _tc5b = _total_counts()
        _ope1_cands = sorted(
            [s for s in _ope_skilled if "ope1" in staff[s].get("duty_skills",[])],
            key=lambda s: _tc5b.get(s, 0)
        )
        for _ope2_sid in _ope_5b:  # ope2のどちらを置き換えるか
            for _c1 in _ope1_cands:
                if _c1 in _ope_5b: continue
                if _ok_to_swap_ope(_c1, _dk_5b):
                    duty_shifts[_dk_5b]["ope"] = [
                        _c1 if s == _ope2_sid else s for s in _ope_5b
                    ]
                    break
            else:
                continue
            break

    # Step6: 連日ope当番の強制修復
    from datetime import timedelta as _tdd6
    for _repair_iter in range(100):
        _has_c, _c_d1, _c_sids = _has_consec_ope()
        if not _has_c: break
        _c_d2 = (date(int(_c_d1[:4]),int(_c_d1[5:7]),int(_c_d1[8:10]))
                 + _tdd6(days=1)).strftime("%Y-%m-%d")
        _ope_d1 = list(duty_shifts.get(_c_d1,{}).get("ope",[]))
        _ope_d2 = list(duty_shifts.get(_c_d2,{}).get("ope",[]))
        _fixed = False

        def _find_alt(dk_target_, ope_target_):
            """連日の代替候補を探す（ope1/ope2の役割を維持）"""
            _d_t = date(int(dk_target_[:4]),int(dk_target_[5:7]),int(dk_target_[8:10]))
            _pr_ = (_d_t - _tdd6(1)).strftime("%Y-%m-%d")
            _nx_ = (_d_t + _tdd6(1)).strftime("%Y-%m-%d")
            _is_hol_t = not is_work_day(_d_t)

            for _strict in [True, False]:
                for _s in sorted(_ope_skilled,
                                  key=lambda s: sum(1 for dv in duty_shifts.values()
                                                    if s in dv.get("ope",[]))):
                    if _s in ope_target_: continue
                    if req_no_duty(_s, dk_target_): continue
                    _st = results.get(dk_target_,{}).get(_s,"")
                    if _st in ("夜入","夜明","代休","ICU代休","透析代休","希望休"): continue
                    # 土日祝のICU日勤者は除外
                    if _is_hol_t and _st == "B": continue
                    if _strict:
                        if _s in _duty_set(_pr_): continue
                        if _s in _duty_set(_nx_): continue
                    return _s
            return None

        for _bad_sid in list(_c_sids):
            if _bad_sid in _ope_d2:
                _new = _find_alt(_c_d2, _ope_d2)
                if _new:
                    duty_shifts[_c_d2]["ope"] = [_new if x==_bad_sid else x for x in _ope_d2]
                    _fixed = True; break
            if not _fixed and _bad_sid in _ope_d1:
                _new = _find_alt(_c_d1, _ope_d1)
                if _new:
                    duty_shifts[_c_d1]["ope"] = [_new if x==_bad_sid else x for x in _ope_d1]
                    _fixed = True; break
        if not _fixed: break

    # duty_shifts を results に埋め込む
    for dk, duties in duty_shifts.items():
        if duties:
            results.setdefault(dk, {})["_duty"] = duties

    # ── Step7: ope連日を results 確定後に最終修復 ──────────────────
    # duty_shifts 埋め込み後に全情報を使って連日を解消する
    _ope_skilled_f = [sid for sid, s in staff.items()
                      if any(sk in ["ope1","ope2"] for sk in s.get("duty_skills",[]))]
    _all_dks_sorted = sorted(results.keys())
    for _f_iter in range(100):
        _found = False
        for _fi in range(len(_all_dks_sorted)-1):
            _fd1 = _all_dks_sorted[_fi]
            _fd2 = _all_dks_sorted[_fi+1]
            _fdd1 = date(int(_fd1[:4]),int(_fd1[5:7]),int(_fd1[8:10]))
            _fdd2 = date(int(_fd2[:4]),int(_fd2[5:7]),int(_fd2[8:10]))
            if (_fdd2-_fdd1).days != 1: continue
            _fo1 = set(results[_fd1].get("_duty",{}).get("ope",[]))
            _fo2 = set(results[_fd2].get("_duty",{}).get("ope",[]))
            _fbad = _fo1 & _fo2
            if not _fbad: continue
            _found = True
            _flist2 = list(results[_fd2]["_duty"]["ope"])

            # d2側を修復（稼働可能・当番不可でない・d2に入っていない人で当番数最少）
            for _fbs in list(_fbad):
                _falts = [s for s in sorted(_ope_skilled_f,
                          key=lambda x: sum(1 for dv in results.values()
                                            if x in dv.get("_duty",{}).get("ope",[])))
                          if s not in _fo2
                          and not req_no_duty(s, _fd2)
                          and results.get(_fd2,{}).get(s,"") not in
                              ("夜入","夜明","代休","ICU代休","透析代休","希望休")]
                if _falts:
                    results[_fd2]["_duty"]["ope"] = [_falts[0] if x==_fbs else x for x in _flist2]
                    duty_shifts[_fd2]["ope"] = results[_fd2]["_duty"]["ope"]
                    break
                # d1側でも試みる
                _flist1 = list(results[_fd1].get("_duty",{}).get("ope",[]))
                _falts1 = [s for s in sorted(_ope_skilled_f,
                           key=lambda x: sum(1 for dv in results.values()
                                             if x in dv.get("_duty",{}).get("ope",[])))
                           if s not in _fo1 and s not in _fo2
                           and not req_no_duty(s, _fd1)
                           and results.get(_fd1,{}).get(s,"") not in
                               ("夜入","夜明","代休","ICU代休","透析代休","希望休")]
                if _falts1:
                    results[_fd1]["_duty"]["ope"] = [_falts1[0] if x==_fbs else x for x in _flist1]
                    duty_shifts[_fd1]["ope"] = results[_fd1]["_duty"]["ope"]
                    break
            break
        if not _found: break

    # ── Step8: ope1なし・重複・稼働不可との重複を最終修正 ────────
    _BUSY_ST = {"夜入","夜明","代休","ICU代休","透析代休","希望休"}
    _ope1_staff_f = [s for s,v in staff.items() if "ope1" in v.get("duty_skills",[])]
    _ope_all_f    = [s for s,v in staff.items() if any(sk in ["ope1","ope2"]
                     for sk in v.get("duty_skills",[]))]
    _, _nd8 = calendar.monthrange(year, month)
    _all_days8 = [date(year, month, d) for d in range(1, _nd8+1)]

    for _d8 in _all_days8:
        _dk8 = _d8.strftime("%Y-%m-%d")
        _duty8 = results.get(_dk8, {}).get("_duty", {})
        if not _duty8: continue

        # ── 8a: ope当番の稼働不可・希望休・重複を修正（全日対象）
        _ope8 = list(_duty8.get("ope", []))
        if _ope8:
            # 稼働不可の人を除去して代替を探す
            _clean8 = []
            _replaced = False
            for _s8 in _ope8:
                _st8 = results.get(_dk8,{}).get(_s8,"")
                _rq8 = requests.get(_s8,{}).get(_dk8,"")
                if _st8 in _BUSY_ST or _rq8 in ("off_duty","no_duty"):
                    # 除去 → 代替を探す
                    _alts8 = [s for s in _ope_all_f
                               if s not in _ope8 and s not in _clean8
                               and not req_no_duty(s, _dk8)
                               and results.get(_dk8,{}).get(s,"") not in _BUSY_ST]
                    _alts8.sort(key=lambda s: sum(1 for dv in results.values()
                                                   if s in dv.get("_duty",{}).get("ope",[])))
                    if _alts8:
                        _clean8.append(_alts8[0]); _replaced = True
                    # 代替なし → その枠は空に（後でope1なし修正が対応）
                else:
                    _clean8.append(_s8)
            # 重複除去
            _seen8 = set()
            _dedup8 = []
            for _s8 in _clean8:
                if _s8 not in _seen8:
                    _seen8.add(_s8); _dedup8.append(_s8)
            if _dedup8 != _ope8 or _replaced:
                results[_dk8]["_duty"]["ope"] = _dedup8
                if _dk8 in duty_shifts: duty_shifts[_dk8]["ope"] = _dedup8
                _ope8 = _dedup8

        # ── 8b: ope1なし修正
        if _ope8 and not any("ope1" in staff.get(s,{}).get("duty_skills",[]) for s in _ope8):
            for _i8, _s8 in enumerate(_ope8):
                _alts8 = [s for s in _ope1_staff_f
                           if s not in _ope8
                           and not req_no_duty(s, _dk8)
                           and results.get(_dk8,{}).get(s,"") not in _BUSY_ST]
                _alts8.sort(key=lambda s: sum(1 for dv in results.values()
                                               if s in dv.get("_duty",{}).get("ope",[])))
                if _alts8:
                    _ope8[_i8] = _alts8[0]
                    results[_dk8]["_duty"]["ope"] = _ope8
                    if _dk8 in duty_shifts: duty_shifts[_dk8]["ope"] = _ope8
                    break

        # ── 8c: B/C/D当番の稼働不可チェック（平日のみ）
        if is_work_day(_d8):
            for _dept8 in ["B","C","D"]:
                _dsid8 = _duty8.get(_dept8,"")
                if not _dsid8: continue
                _st8 = results.get(_dk8,{}).get(_dsid8,"")
                _rq8 = requests.get(_dsid8,{}).get(_dk8,"")
                if _st8 in _BUSY_ST or _rq8 in ("off_duty","no_duty"):
                    # 当番者が稼働不可 → エラーとして記録（自動修正は困難なためそのまま）
                    # シフト作成ロジック側で防ぐべき問題
                    pass  # 検閲で検出される

    # ── 希望休をシフト結果に反映 ─────────────────────────────────
    # 休暇希望（off_duty / off_only）がある平日を「希望休」として記録
    # 代休で充てられた日はすでに代休になっているのでスキップ
    for sid in staff:
        for dk_req, req_type in requests.get(sid, {}).items():
            if not dk_req.startswith(f"{year}-{month:02d}-"): continue
            if req_type not in ("off_duty", "off_only"): continue
            try:
                d_req = date(int(dk_req[:4]), int(dk_req[5:7]), int(dk_req[8:10]))
            except ValueError: continue
            if not is_work_day(d_req): continue
            cur = results.get(dk_req, {}).get(sid, "")
            # 代休・夜勤等が入っていない平日に「希望休」をセット
            if cur not in ("夜入","夜明","代休","ICU代休","透析代休","希望休"):
                results.setdefault(dk_req, {})[sid] = "希望休"

    return results

# ─────────────────────────────────────────────
# シフト一覧 HTML（行=スタッフ、列=日付）
# ─────────────────────────────────────────────
# 夜勤セルのスタイル定義
NIGHT_CELL = {
    "夜入":    ("#1A1A2E", "🌙夜入"),
    "夜明":    ("#16213E", "🌅夜明"),
    "代休":    ("#E74C3C", "代休"),
    "ICU代休": ("#E74C3C", "代休"),
    "透析代休": ("#E74C3C", "代休"),
    "希望休":  ("#9B59B6", "🌴希望"),
}

def build_shift_table_html(year: int, month: int, data: dict) -> str:
    _, num_days = calendar.monthrange(year, month)

    header_date = ""
    header_wday = ""
    col_styles  = []

    for day in range(1, num_days + 1):
        d  = date(year, month, day)
        dt = day_type(d)
        if dt == "holiday":
            bg, fc = "#FFCCCC", "#CC0000"
        elif dt == "saturday":
            bg, fc = "#CCE5FF", "#0055AA"
        else:
            bg, fc = "#F0F0F0", "#333333"
        col_styles.append((bg, fc))
        th = (f"background:{bg};color:{fc};text-align:center;padding:4px 6px;"
              f"border:1px solid #ccc;min-width:42px;font-weight:bold;")
        header_date += f"<th style='{th}'>{month}/{day}</th>"
        header_wday += f"<th style='{th}'>{WEEKDAY_JP[d.weekday()]}</th>"

    rows_html = ""
    for sid, sinfo in data["staff"].items():
        staff_name = sinfo["name"]

        row = (
            f"<td style='padding:6px 10px;border:1px solid #ccc;white-space:nowrap;"
            f"background:#fafafa;font-weight:bold;min-width:120px;width:120px;"
            f"font-size:0.95em;'>{staff_name}</td>"
        )
        for day in range(1, num_days + 1):
            dk        = f"{year}-{month:02d}-{day:02d}"
            day_data  = data["shifts"].get(dk, {})
            dept      = day_data.get(sid, "")
            bg, _     = col_styles[day - 1]

            # 当番マーク確認・部門色取得
            duties   = day_data.get("_duty", {})
            # どの部門の当番かを特定
            duty_dept = next((d for d, v in duties.items()
                              if d != "ope" and isinstance(v, str) and v == sid), None)
            ope_list  = duties.get("ope", [])
            is_ope1   = len(ope_list) > 0 and ope_list[0] == sid
            is_ope2   = len(ope_list) > 1 and ope_list[1] == sid
            is_ope    = sid in ope_list
            # 当番色: その部門のDEPT_COLORSに合わせる。オペはA色
            if duty_dept:
                duty_color = DEPT_COLORS.get(duty_dept, "#E67E22")
            elif is_ope:
                duty_color = DEPT_COLORS.get("A", "#4A90D9")
            else:
                duty_color = None
            # オペ1=★ オペ2=☆ その他当番=★
            if duty_dept:
                duty_lbl = "★"
            elif is_ope1:
                duty_lbl = "★"
            elif is_ope2:
                duty_lbl = "☆"
            else:
                duty_lbl = ""

            if dept in NIGHT_CELL:
                nc, nlbl = NIGHT_CELL[dept]
                if dept in ("夜入","夜明"):
                    cell = (
                        f"<td style='text-align:center;padding:2px 3px;border:1px solid #ccc;background:{nc};white-space:nowrap;'>"
                        f"<span style='color:white;font-size:0.8em;font-weight:bold;'>{nlbl}</span></td>"
                    )
                else:
                    cell = (
                        f"<td style='text-align:center;padding:2px 3px;border:1px solid #ccc;background:{bg};white-space:nowrap;'>"
                        f"<span style='background:{nc};color:white;border-radius:3px;"
                        f"padding:1px 4px;font-size:0.78em;font-weight:bold;'>{nlbl}</span></td>"
                    )
            elif dept:
                dc  = DEPT_COLORS.get(dept, "#888")
                lbl = dept_label(data, dept)
                star = (f"<span style='font-size:0.8em;color:{duty_color};"
                        f"font-weight:bold;'>{duty_lbl}</span>" if duty_lbl else "")
                cell = (
                    f"<td style='text-align:center;padding:2px 3px;border:1px solid #ccc;background:{bg};white-space:nowrap;'>"
                    f"<span style='background:{dc};color:white;border-radius:3px;"
                    f"padding:1px 4px;font-size:0.78em;font-weight:bold;'>{lbl}</span>"
                    f"{star}</td>"
                )
            elif duty_lbl:
                # 土日祝で当番のみのセル
                cell = (
                    f"<td style='text-align:center;padding:4px;border:1px solid #ccc;background:{bg};'>"
                    f"<span style='font-size:0.78em;color:{duty_color};font-weight:bold;'>{duty_lbl}</span></td>"
                )
            else:
                cell = (
                    f"<td style='text-align:center;padding:2px 3px;border:1px solid #ccc;"
                    f"background:{bg};color:#bbb;white-space:nowrap;'>—</td>"
                )
            row += cell
        rows_html += f"<tr>{row}</tr>"

    nth = "padding:4px 8px;border:1px solid #ccc;background:#E8E8E8;text-align:left;white-space:nowrap;"
    return f"""
    <div style='overflow-x:auto;'>
    <table style='border-collapse:collapse;font-size:0.9em;'>
      <thead>
        <tr><th style='{nth}' rowspan='2'>スタッフ</th>{header_date}</tr>
        <tr>{header_wday}</tr>
      </thead>
      <tbody>{rows_html}</tbody>
    </table>
    </div>"""

# ─────────────────────────────────────────────
# UI
# ─────────────────────────────────────────────
def main():
    st.set_page_config(page_title="シフト作成アプリ", page_icon="📅", layout="wide")
    if not HAS_JPHOLIDAY:
        st.warning("⚠️ jpholidayが未インストールです。`pip install jpholiday` で祝日判定が有効になります。")

    # ── session_state 初期化（初回のみ）──
    if "data" not in st.session_state:
        st.session_state.data = load_data()
        save_data(st.session_state.data, to_github=True)   # 移行処理後すぐに保存

    data = st.session_state.data

    # ── パスワード管理 ──────────────────────────────────────────
    CE_PASSWORD_CORRECT = str(len(data["staff"]))  # CE人数（スタッフ数と自動連動）

    def _check_password(input_str: str) -> bool:
        # 全角数字→半角に変換して比較
        normalized = input_str.translate(str.maketrans("０１２３４５６７８９", "0123456789"))
        return normalized.strip() == CE_PASSWORD_CORRECT

    if "unlocked" not in st.session_state:
        st.session_state.unlocked = False

    # ── タイトルとロック/解除ボタンを横並び ──────────────────────
    _title_col, _lock_spacer, _lock_area = st.columns([4, 1, 2])
    with _title_col:
        st.title("📅 シフト作成アプリ")
    with _lock_area:
        st.write("")  # タイトルと高さを合わせる
        st.write("")
        _req_locked_top = data.get("request_lock", False)
        if st.session_state.unlocked:
            # 管理者ログイン中: ロック操作ボタン
            if _req_locked_top:
                if st.button("🔓 希望入力ロック解除", key="top_unlock_btn",
                             use_container_width=True):
                    data["request_lock"] = False
                    save_data(data, to_github=True)
                    st.rerun()
            else:
                if st.button("🔒 希望入力をロック", key="top_lock_btn",
                             use_container_width=True):
                    data["request_lock"] = True
                    save_data(data, to_github=True)
                    st.rerun()
            if st.button("🔓 管理者ログアウト", key="top_logout_btn",
                         use_container_width=True):
                st.session_state.unlocked = False
                st.rerun()
        else:
            # 未ログイン: 管理者ログインフォーム（折り畳み）
            with st.expander("🔑 管理者ログイン", expanded=False):
                _top_pw = st.text_input("パスワード", type="password",
                                        key="top_pw_input",
                                        placeholder="半角または全角数字")
                if st.button("ログイン", key="top_login_btn",
                             use_container_width=True):
                    if _check_password(_top_pw):
                        st.session_state.unlocked = True
                        st.rerun()
                    else:
                        st.error("パスワードが違います。")

    tab0, tab1, tab2, tab3, tab4, tab5 = st.tabs([
        "🗓️ 休暇・当番不可希望入力",
        "👥 スタッフ管理", "🏢 部門設定",
        "📆 シフト作成", "📊 シフト一覧", "⚖️ バランス"
    ])

    # ══════════════════════════════════════════
    # タブ0: 休暇・当番不可希望入力
    # ══════════════════════════════════════════
    with tab0:
        # タイトルと確定ボタンを横並び
        _t0_title_col, _t0_btn_col = st.columns([5, 1])
        with _t0_title_col:
            st.subheader("🗓️ 休暇・当番不可希望入力")
        with _t0_btn_col:
            st.write("")
            if st.button("✅ 確定", key="req_save_btn",
                         type="primary", use_container_width=True):
                ok, msg = save_data(data, to_github=True)
                if msg:
                    if ok:
                        st.success(msg)
                    else:
                        st.warning(msg)

        # ── 希望入力ロック管理 ──────────────────────────────────────
        if "request_lock" not in data:
            data["request_lock"] = False

        _req_locked = data.get("request_lock", False)

        if not st.session_state.unlocked and _req_locked:
            # 一般スタッフ: ロック中
            st.warning("🔒 希望入力は締め切られています。")
            st.stop()

        st.caption("1クリック：休暇＋当番不可 🔴 ／ 2クリック：当番のみ不可 🟡 ／ 3クリック：休暇のみ（当番可）🟢 ／ 4クリック：解除")

        staff_names = {sid: s["name"] for sid, s in data["staff"].items()}
        req_sid = st.selectbox("スタッフを選択", list(staff_names.keys()),
                                format_func=lambda x: staff_names[x],
                                key="req_sid")

        col_y, col_m = st.columns(2)
        with col_y:
            req_year  = int(st.number_input("年", 2025, 2100, date.today().year, key="req_year"))
        with col_m:
            req_month = int(st.number_input("月", 1, 12, date.today().month, key="req_month"))

        # requests構造: data["requests"][sid][date_key] = state
        # state: "off_duty" (休暇+当番不可), "no_duty" (当番不可のみ), "off_only" (休暇のみ・当番可)
        if "requests" not in data:
            data["requests"] = {}
        if req_sid not in data["requests"]:
            data["requests"][req_sid] = {}

        req_data = data["requests"][req_sid]
        STATE_CYCLE = [None, "off_duty", "no_duty", "off_only"]
        STATE_LABELS = {
            None:        ("　", "#f0f0f0", "#333"),
            "off_duty":  ("🔴 休暇+当番不可",  "#ffe0e0", "#c00"),
            "no_duty":   ("🟡 当番不可",       "#fff8e0", "#a60"),
            "off_only":  ("🟢 休暇のみ",       "#e0f0e0", "#060"),
        }

        # 管理者向け: シフト固定サイクル
        SHIFT_CYCLE  = [None, "夜入", "夜明", "B", "D"]
        SHIFT_LABELS = {
            None:  ("―",        "#f0f0f0", "#888"),
            "夜入": ("🌙 夜入",  "#1a1a2e", "#aad4ff"),
            "夜明": ("🌅 夜明",  "#2d1b00", "#ffcc88"),
            "B":   ("🏥 ICU日勤","#e8f5e9", "#2e7d32"),
            "D":   ("💉 透析日勤","#f3e5f5", "#6a1b9a"),
        }

        _, cal_days = calendar.monthrange(req_year, req_month)
        first_weekday = date(req_year, req_month, 1).weekday()  # 0=月

        # カレンダーをweek行ごとに描画（曜日ヘッダも同じcolumnsで揃える）
        weeks = []
        week = [None] * first_weekday
        for day in range(1, cal_days + 1):
            week.append(day)
            if len(week) == 7:
                weeks.append(week); week = []
        if week:
            week += [None] * (7 - len(week)); weeks.append(week)

        # 管理者モード: 希望入力列 + シフト固定列を並べる
        is_admin = st.session_state.unlocked
        if is_admin:
            st.caption("左列: 希望入力（全員）／右列: シフト固定（管理者専用・夜入→夜明→ICU日勤→透析日勤）")

        # 曜日ヘッダ
        if is_admin:
            hdr_cols = st.columns([3, 2, 2, 3, 2, 2, 3, 2, 2, 3, 2, 2, 3, 2, 2, 3, 2, 2, 3, 2, 2])
            # 7曜日 × (日付+希望+固定) = 21列
            for i, wd in enumerate(["月","火","水","木","金","土","日"]):
                color = "#0055aa" if i==5 else "#aa0000" if i==6 else "#444"
                hdr_cols[i*3].markdown(
                    f"<div style='text-align:center;font-weight:bold;"
                    f"font-size:0.85em;color:{color}'>{wd}</div>",
                    unsafe_allow_html=True
                )
        else:
            hdr_cols = st.columns(7)
            for i, wd in enumerate(["月","火","水","木","金","土","日"]):
                color = "#0055aa" if i==5 else "#aa0000" if i==6 else "#444"
                hdr_cols[i].markdown(
                    f"<div style='text-align:center;font-weight:bold;"
                    f"font-size:0.85em;color:{color};padding:2px 0'>{wd}</div>",
                    unsafe_allow_html=True
                )

        for w in weeks:
            if is_admin:
                cols = st.columns([3, 2, 2] * 7)
            else:
                cols = st.columns(7)

            for i, day in enumerate(w):
                if day is None:
                    if is_admin:
                        cols[i*3].markdown(" ")
                    else:
                        cols[i].markdown(" ")
                    continue

                dk = f"{req_year}-{req_month:02d}-{day:02d}"
                d_obj = date(req_year, req_month, day)
                wday = d_obj.weekday()
                state = req_data.get(dk)
                lbl, bg, fg = STATE_LABELS[state]
                is_sat = (wday == 5)
                is_sun = (wday == 6)
                try:
                    import jpholiday; is_hol = jpholiday.is_holiday(d_obj)
                except Exception: is_hol = False

                icon = " 🔴" if state=="off_duty" else " 🟡" if state=="no_duty" else " 🟢" if state=="off_only" else ""

                # ── 希望入力ボタン ──
                btn_col = cols[i*3] if is_admin else cols[i]
                if btn_col.button(
                    f"{day}{icon}",
                    key=f"req_{req_sid}_{dk}",
                    use_container_width=True,
                    help=lbl if state else "クリックして希望を入力"
                ):
                    idx = STATE_CYCLE.index(state)
                    next_state = STATE_CYCLE[(idx + 1) % len(STATE_CYCLE)]
                    if next_state is None:
                        req_data.pop(dk, None)
                    else:
                        req_data[dk] = next_state
                    data["requests"][req_sid] = req_data
                    save_data(data)
                    st.rerun()

                # ── シフト固定ボタン（管理者のみ）──
                if is_admin:
                    shift_dict = data["shifts"].setdefault(dk, {})
                    cur_shift = shift_dict.get(req_sid)
                    # _skipに入っていたら「なし固定」状態
                    _is_skipped = req_sid in shift_dict.get("_skip", [])
                    if _is_skipped:
                        cur_shift = None  # 表示上はなし
                    s_lbl, s_bg, s_fg = SHIFT_LABELS.get(cur_shift, SHIFT_LABELS[None])
                    btn_label = "🚫 除外" if _is_skipped else s_lbl

                    if cols[i*3+1].button(
                        btn_label,
                        key=f"fix_{req_sid}_{dk}",
                        use_container_width=True,
                        help="シフト自動割り当てから除外中" if _is_skipped else
                             (f"シフト固定: {s_lbl}" if cur_shift else "クリックでシフト固定")
                    ):
                        locked = set(shift_dict.get("_locked", []))
                        skip   = set(shift_dict.get("_skip",   []))

                        if _is_skipped:
                            # 🚫除外 → なし（完全リセット）
                            skip.discard(req_sid)
                            shift_dict.pop(req_sid, None)
                        elif cur_shift is None:
                            # なし → 夜入
                            shift_dict[req_sid] = "夜入"
                            locked.add(req_sid)
                            skip.discard(req_sid)
                        else:
                            idx_s = SHIFT_CYCLE.index(cur_shift) if cur_shift in SHIFT_CYCLE else 1
                            next_s = SHIFT_CYCLE[(idx_s + 1) % len(SHIFT_CYCLE)]
                            if next_s is None:
                                # 最後 → 🚫除外（シフト自動割り当てをスキップ）
                                shift_dict.pop(req_sid, None)
                                locked.discard(req_sid)
                                skip.add(req_sid)
                            else:
                                shift_dict[req_sid] = next_s
                                locked.add(req_sid)
                                skip.discard(req_sid)

                        shift_dict["_locked"] = list(locked)
                        shift_dict["_skip"]   = list(skip)
                        data["shifts"][dk] = shift_dict
                        save_data(data)
                        st.rerun()

        # 凡例と現在の希望一覧
        st.divider()
        st.markdown("#### 入力済み希望")
        month_reqs = {k: v for k, v in req_data.items()
                      if k.startswith(f"{req_year}-{req_month:02d}-")}
        if month_reqs:
            for dk_r, st_r in sorted(month_reqs.items()):
                d_r = date(int(dk_r[:4]), int(dk_r[5:7]), int(dk_r[8:10]))
                wday_r = ["月","火","水","木","金","土","日"][d_r.weekday()]
                lbl_r, _, _ = STATE_LABELS[st_r]
                st.markdown(f"- **{d_r.day}日（{wday_r}）** {lbl_r}")
        else:
            st.info("この月の希望はまだ入力されていません。")

    # ══════════════════════════════════════════
    # パスワードロック（タブ1〜5）
    # ══════════════════════════════════════════
    if not st.session_state.unlocked:
        with tab1:
            st.info("🔒 管理者専用エリアです。画面右上の「管理者ログイン」からパスワードを入力してください。")
        for _t in [tab2, tab3, tab4, tab5]:
            with _t:
                st.info("🔒 管理者専用エリアです。画面右上の「管理者ログイン」からパスワードを入力してください。")

    # ══════════════════════════════════════════
    # タブ1: スタッフ管理
    # ══════════════════════════════════════════
    with tab1:
      if st.session_state.unlocked:
        st.subheader("スタッフ登録・管理")
        col1, col2 = st.columns([1, 1])

        with col1:
            st.markdown("#### 新規スタッフ登録")
            new_name  = st.text_input("氏名", key="new_name")
            new_main  = st.selectbox("メイン部署", DEPT_IDS,
                                     format_func=lambda d: dept_display(data, d),
                                     key="new_main")
            new_subs  = st.multiselect("サブ部署（人手不足時に配置）",
                                       [d for d in DEPT_IDS if d != new_main],
                                       format_func=lambda d: dept_display(data, d),
                                       key="new_subs")
            new_night = st.radio("夜勤", options=[1, 0],
                                 format_func=lambda x: "🌙 可能" if x == 1 else "❌ 不可",
                                 horizontal=True, key="new_night")
            st.markdown("**当番スキル**")
            nc1, nc2 = st.columns(2)
            with nc1:
                new_duty_b = st.checkbox(dept_display(data,"B")+"当番", key="new_duty_b")
                new_duty_c = st.checkbox(dept_display(data,"C")+"当番", key="new_duty_c")
                new_duty_d = st.checkbox(dept_display(data,"D")+"当番", key="new_duty_d")
            with nc2:
                new_duty_op1 = st.checkbox("オペ1当番", key="new_duty_op1",
                    disabled=(new_main != "A"))
                new_duty_op2 = st.checkbox("オペ2当番", key="new_duty_op2")
            if st.button("➕ 登録", use_container_width=True):
                if new_name.strip():
                    existing_ids = set(data["staff"].keys())
                    i, sid = 1, "0001"
                    while sid in existing_ids:
                        i += 1; sid = str(i).zfill(4)
                    duty = [d for d, f in [("B", new_duty_b), ("C", new_duty_c), ("D", new_duty_d)] if f]
                    if new_duty_op1 and new_main == "A": duty.append("ope1")
                    if new_duty_op2: duty.append("ope2")
                    st.session_state.data["staff"][sid] = {
                        "name":        new_name.strip(),
                        "main_dept":   new_main,
                        "sub_depts":   new_subs,
                        "night_shift": new_night,
                        "duty_skills": duty
                    }
                    save_data(st.session_state.data, to_github=True)
                    st.success(f"✅ {new_name} を登録しました")
                    st.rerun()
                else:
                    st.warning("氏名を入力してください")

        with col2:
            st.markdown("#### 登録済みスタッフ")
            if not data["staff"]:
                st.info("スタッフが登録されていません")
            else:
                # 一括保存ボタン
                if st.button("💾 全スタッフ一括保存", type="primary", use_container_width=True):
                    for sid in list(st.session_state.data["staff"].keys()):
                        mk = f"edit_main_{sid}"
                        sk = f"edit_sub_{sid}"
                        nk = f"edit_night_{sid}"
                        if mk in st.session_state:
                            duty = [d for d in ["B","C","D"]
                                    if st.session_state.get(f"edit_duty_{d}_{sid}", False)]
                            if st.session_state.get(f"edit_duty_op1_{sid}", False):
                                duty.append("ope1")
                            if st.session_state.get(f"edit_duty_op2_{sid}", False):
                                duty.append("ope2")
                            st.session_state.data["staff"][sid].update({
                                "main_dept":   st.session_state[mk],
                                "sub_depts":   st.session_state.get(sk, []),
                                "night_shift": st.session_state.get(nk, 0),
                                "duty_skills": duty
                            })
                    save_data(st.session_state.data, to_github=True)
                    st.success("✅ 保存しました")

                st.markdown("---")
                for sid, sinfo in list(data["staff"].items()):
                    night_lbl = "🌙可" if sinfo.get("night_shift", 0) else "❌不可"
                    ml  = dept_display(data, sinfo["main_dept"])
                    sls = ", ".join(dept_display(data, d) for d in sinfo["sub_depts"]) or "なし"
                    with st.expander(f"**{sinfo['name']}**　{ml}　{sls}　{night_lbl}"):
                        # session_stateに保存済みの値があればそれを初期値にする
                        cur_main = st.session_state.get(f"edit_main_{sid}", sinfo["main_dept"])
                        cur_main_idx = DEPT_IDS.index(cur_main) if cur_main in DEPT_IDS else 0
                        cur_subs = st.session_state.get(f"edit_sub_{sid}", sinfo["sub_depts"])
                        cur_night_idx = 0 if st.session_state.get(f"edit_night_{sid}", sinfo.get("night_shift", 0)) == 1 else 1

                        st.selectbox("メイン部署", DEPT_IDS,
                            index=cur_main_idx,
                            format_func=lambda d: dept_display(data, d),
                            key=f"edit_main_{sid}")
                        st.multiselect("サブ部署",
                            [d for d in DEPT_IDS if d != st.session_state.get(f"edit_main_{sid}", sinfo["main_dept"])],
                            default=[d for d in cur_subs if d in DEPT_IDS],
                            format_func=lambda d: dept_display(data, d),
                            key=f"edit_sub_{sid}")
                        st.radio("夜勤", options=[1, 0],
                            format_func=lambda x: "🌙 可能" if x == 1 else "❌ 不可",
                            index=cur_night_idx,
                            horizontal=True, key=f"edit_night_{sid}")
                        st.markdown("**当番スキル**")
                        dc1, dc2 = st.columns(2)
                        cur_duty = sinfo.get("duty_skills", [])
                        cur_main_now = st.session_state.get(f"edit_main_{sid}", sinfo["main_dept"])
                        # session_stateに初期値がなければJSONから設定
                        for _dk, _dv in [("B", "B" in cur_duty), ("C", "C" in cur_duty),
                                         ("D", "D" in cur_duty), ("op1", "ope1" in cur_duty),
                                         ("op2", "ope2" in cur_duty)]:
                            if f"edit_duty_{_dk}_{sid}" not in st.session_state:
                                st.session_state[f"edit_duty_{_dk}_{sid}"] = _dv
                        with dc1:
                            st.checkbox(dept_display(data,"B")+"当番", key=f"edit_duty_B_{sid}")
                            st.checkbox(dept_display(data,"C")+"当番", key=f"edit_duty_C_{sid}")
                            st.checkbox(dept_display(data,"D")+"当番", key=f"edit_duty_D_{sid}")
                        with dc2:
                            st.checkbox("オペ1当番", key=f"edit_duty_op1_{sid}",
                                disabled=(cur_main_now != "A"))
                            st.checkbox("オペ2当番", key=f"edit_duty_op2_{sid}")
                        if st.button("🗑️ 削除", key=f"del_{sid}"):
                            del st.session_state.data["staff"][sid]
                            save_data(st.session_state.data, to_github=True)
                            st.rerun()

    # ══════════════════════════════════════════
    # タブ2: 部門設定
    # ══════════════════════════════════════════
    with tab2:
      if st.session_state.unlocked:
        st.subheader("部門設定")
        st.caption("表示名・最小人数を編集して「💾 保存」を押してください。")

        cols = st.columns(4)
        for i, did in enumerate(DEPT_IDS):
            cfg = data["dept_config"][did]
            with cols[i]:
                st.markdown(
                    f"<div style='background:{DEPT_COLORS[did]};padding:8px;border-radius:8px;"
                    f"color:white;text-align:center;font-weight:bold;font-size:1.1em'>"
                    f"部門 {did}</div>", unsafe_allow_html=True
                )
                # value を明示せずキーのみ指定 → session_state が自動的に値を保持
                if f"lbl_{did}" not in st.session_state:
                    st.session_state[f"lbl_{did}"] = cfg.get("label", did)
                if f"min_{did}" not in st.session_state:
                    st.session_state[f"min_{did}"] = cfg["min_staff"]
                st.text_input("表示名", key=f"lbl_{did}")
                st.number_input("最小人数", 0, 20, key=f"min_{did}")
                mains = [s["name"] for s in data["staff"].values() if s["main_dept"] == did]
                subs  = [s["name"] for s in data["staff"].values() if did in s.get("sub_depts", [])]
                st.markdown(f"**メイン:** {', '.join(mains) or 'なし'}")
                st.markdown(f"**サブ可:** {', '.join(subs) or 'なし'}")

        st.markdown("")
        if st.button("💾 部門設定を保存", type="primary"):
            for did in DEPT_IDS:
                st.session_state.data["dept_config"][did]["label"]     = st.session_state[f"lbl_{did}"]
                st.session_state.data["dept_config"][did]["min_staff"] = st.session_state[f"min_{did}"]
            save_data(st.session_state.data, to_github=True)
            st.success("✅ 部門設定を保存しました")

    # ══════════════════════════════════════════
    # タブ3: シフト作成（月次）
    # ══════════════════════════════════════════
    with tab3:
      if st.session_state.unlocked:
        st.subheader("月次シフト自動作成")
        st.caption("月を選択して自動割り当て。土日祝は休みです。メイン部署勤務回数は月内で均等化されます。")

        col_l, col_r = st.columns([1, 2])
        with col_l:
            sel_year  = int(st.number_input("年", 2020, 2100, date.today().year,  key="shift_year"))
            sel_month = int(st.number_input("月", 1,    12,   date.today().month, key="shift_month"))

            st.markdown("---")
            if st.button("🤖 月次自動割り当て", use_container_width=True, type="primary"):
                if not data["staff"]:
                    st.warning("スタッフを登録してください")
                else:
                    with st.spinner("最良のシフトを探索中... (最大30回試行)"):
                        import random as _rnd

                        def _score_shifts(shifts_, data_):
                            """シフトのスコアを計算（低いほど良い）
                            - 当番不可違反 × 10000
                            - ope1スキルなし日数 × 5000
                            - ope当番2名未満 × 3000
                            - ICU日勤+ope重複 × 3000
                            - 連日ope当番 × 500
                            - ope1グループ内当番差² × 50
                            - ope2グループ内当番差² × 50
                            """
                            score = 0
                            _, _nd = calendar.monthrange(sel_year, sel_month)
                            _all_days = [date(sel_year, sel_month, d) for d in range(1, _nd+1)]
                            _ope1_g = [s for s,v in data_["staff"].items()
                                       if "ope1" in v.get("duty_skills",[])]
                            _ope2_g = [s for s,v in data_["staff"].items()
                                       if any(sk in ["ope1","ope2"] for sk in v.get("duty_skills",[]))
                                       and "ope1" not in v.get("duty_skills",[])]
                            _dc = {s: 0 for s in _ope1_g + _ope2_g}
                            _prev_ope = set()

                            for _d in _all_days:
                                _dk = _d.strftime("%Y-%m-%d")
                                _day_s = shifts_.get(_dk, {})
                                _duty  = _day_s.get("_duty", {})
                                _ope   = _duty.get("ope", [])
                                _cur   = set(_ope)

                                # 連日
                                if _cur & _prev_ope:
                                    score += 500 * len(_cur & _prev_ope)
                                _prev_ope = _cur

                                # ope1なし
                                if _ope and not any(
                                    "ope1" in data_["staff"].get(s,{}).get("duty_skills",[])
                                    for s in _ope): score += 5000

                                # ope不足
                                if is_work_day(_d) and len(_ope) < 2:
                                    score += 3000

                                for s in _ope:
                                    _dc[s] = _dc.get(s, 0) + 1
                                    # ICU日勤+ope重複
                                    if not is_work_day(_d) and _day_s.get(s,"") == "B":
                                        score += 3000
                                    # 当番不可違反
                                    req = data_.get("requests",{}).get(s,{}).get(_dk,"")
                                    if req in ("off_duty","no_duty"):
                                        score += 10000

                                # 部門不足（平日のみ）
                                if is_work_day(_d):
                                    for _did in ["B","C","D"]:
                                        _mn = data_["dept_config"][_did]["min_staff"]
                                        if not _duty.get(_did):
                                            score += _mn * 1000

                            # グループ別当番差
                            for _grp in [_ope1_g, _ope2_g]:
                                _gc = [_dc.get(s,0) for s in _grp if _dc.get(s,0)>0]
                                if len(_gc) >= 2:
                                    _gap = max(_gc) - min(_gc)
                                    score += _gap * _gap * 50

                            return score

                        best_shifts = None
                        best_score  = float("inf")
                        _tries = 30
                        _prog = st.progress(0, text="試行中...")
                        for _i in range(_tries):
                            _rnd.seed(_i * 7 + 13)
                            _candidate = auto_assign_month(sel_year, sel_month, data)
                            _s = _score_shifts(_candidate, data)
                            if _s < best_score:
                                best_score  = _s
                                best_shifts = _candidate
                            _prog.progress((_i + 1) / _tries,
                                           text=f"試行 {_i+1}/{_tries}  現在最良スコア: {best_score}")
                            if best_score == 0:
                                break  # 完璧なシフトが見つかったら終了
                        _prog.empty()

                    month_shifts = best_shifts
                    for dk, assignment in month_shifts.items():
                        # _locked・_skip フラグを保持したままマージ
                        existing = st.session_state.data["shifts"].get(dk, {})
                        merged = dict(assignment)  # auto_assign結果を基本に
                        if "_locked" in existing:
                            merged["_locked"] = existing["_locked"]
                        if "_skip" in existing:
                            merged["_skip"] = existing["_skip"]
                        st.session_state.data["shifts"][dk] = merged
                    save_data(st.session_state.data, to_github=True)
                    if best_score == 0:
                        st.success(f"✅ {sel_year}年{sel_month}月のシフトを作成しました（部門不足ゼロ）")
                    else:
                        st.warning(f"⚠️ {sel_year}年{sel_month}月のシフトを作成しました（スコア: {best_score} — 一部不足あり）")
                    st.rerun()

            if st.button("🗑️ この月のシフトをリセット", use_container_width=True):
                _, num_days = calendar.monthrange(sel_year, sel_month)
                deleted = sum(
                    1 for day in range(1, num_days + 1)
                    if st.session_state.data["shifts"].pop(
                        f"{sel_year}-{sel_month:02d}-{day:02d}", None
                    ) is not None
                )
                save_data(st.session_state.data, to_github=True)
                st.warning(f"{sel_year}年{sel_month}月のシフトを削除しました（{deleted}日）")
                st.rerun()

        with col_r:
            st.markdown(f"#### {sel_year}年{sel_month}月　メイン部署勤務回数")
            if not data["staff"]:
                st.info("スタッフを登録してください")
            else:
                _, num_days = calendar.monthrange(sel_year, sel_month)
                counts = {sid: 0 for sid in data["staff"]}
                for day in range(1, num_days + 1):
                    dk = f"{sel_year}-{sel_month:02d}-{day:02d}"
                    for sid, dept in data["shifts"].get(dk, {}).items():
                        if sid in data["staff"] and dept == data["staff"][sid].get("main_dept"):
                            counts[sid] += 1

                rows = [
                    {
                        "スタッフ":       sinfo["name"],
                        "メイン部署":      dept_display(data, sinfo["main_dept"]),
                        "メイン勤務日数":  counts.get(sid, 0)
                    }
                    for sid, sinfo in data["staff"].items()
                ]
                st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

                # 充足チェック
                st.markdown("#### 人員充足チェック")
                warn_days = []
                any_shift = False
                for day in range(1, num_days + 1):
                    d  = date(sel_year, sel_month, day)
                    if not is_work_day(d):
                        continue
                    dk = f"{sel_year}-{sel_month:02d}-{day:02d}"
                    day_shift = data["shifts"].get(dk, {})
                    if not day_shift:
                        continue
                    any_shift = True
                    dc = {did: 0 for did in DEPT_IDS}
                    for dept in day_shift.values():
                        if isinstance(dept, str) and dept in dc:
                            dc[dept] += 1
                    for did in DEPT_IDS:
                        if dc[did] < data["dept_config"][did]["min_staff"]:
                            lbl = dept_display(data, did)
                            warn_days.append(
                                f"{sel_month}/{day}({WEEKDAY_JP[d.weekday()]}) 部門{lbl}不足"
                            )
                if warn_days:
                    st.warning(
                        "⚠️ 人員不足の日:\n" + "\n".join(warn_days[:10])
                        + (f"\n...他{len(warn_days)-10}件" if len(warn_days) > 10 else "")
                    )
                elif any_shift:
                    st.success("✅ 全平日の人員が充足しています")

    # ══════════════════════════════════════════
    # タブ4: シフト一覧
    # ══════════════════════════════════════════
    with tab4:
      if st.session_state.unlocked:
        st.subheader("月次シフト一覧")
        st.caption("行＝スタッフ、列＝日付　🌙＝夜勤可　土:青 / 日・祝:赤　— は休日")

        col_y, col_m, _ = st.columns([1, 1, 2])
        with col_y:
            view_year  = int(st.number_input("年", 2020, 2100, date.today().year,  key="tab4_year"))
        with col_m:
            view_month = int(st.number_input("月", 1,    12,   date.today().month, key="tab4_month"))

        if not data["staff"]:
            st.info("スタッフを登録してください")
        else:
            st.markdown(
                build_shift_table_html(view_year, view_month, data),
                unsafe_allow_html=True
            )

            _, num_days = calendar.monthrange(view_year, view_month)
            csv_rows = []
            for sid, sinfo in data["staff"].items():
                row = {
                    "スタッフ": sinfo["name"],
                    "夜勤":    sinfo.get("night_shift", 0),
                    "メイン":  dept_label(data, sinfo["main_dept"])
                }
                for day in range(1, num_days + 1):
                    d    = date(view_year, view_month, day)
                    dk   = f"{view_year}-{view_month:02d}-{day:02d}"
                    col  = f"{view_month}/{day}({WEEKDAY_JP[d.weekday()]})"
                    dept = data["shifts"].get(dk, {}).get(sid, "")
                    row[col] = dept_label(data, dept) if dept else ""
                csv_rows.append(row)

            csv_bytes = pd.DataFrame(csv_rows).to_csv(index=False, encoding="utf-8-sig")
            dl_col1, dl_col2 = st.columns(2)
            with dl_col1:
                st.download_button(
                    "📥 CSVダウンロード", csv_bytes,
                    file_name=f"shift_{view_year}_{view_month:02d}.csv",
                    mime="text/csv", use_container_width=True
                )
            with dl_col2:
                # HTMLダウンロード → ブラウザで印刷→PDF保存
                _html_table = build_shift_table_html(view_year, view_month, data)
                _full_html = f"""<!DOCTYPE html>
<html><head>
<meta charset="utf-8">
<title>{view_year}年{view_month}月 シフト表</title>
<style>
  @page {{ size: A4 landscape; margin: 8mm; }}
  @media print {{
    body {{ -webkit-print-color-adjust: exact; print-color-adjust: exact; }}
    .no-print {{ display: none; }}
  }}
  body {{
    font-family: "Meiryo","Hiragino Kaku Gothic Pro","Noto Sans JP",sans-serif;
    font-size: 7pt;
    margin: 0;
    padding: 5mm;
  }}
  h2 {{ font-size: 11pt; margin: 0 0 3mm 0; }}
  table {{ border-collapse: collapse; width: 100%; table-layout: auto; }}
  td, th {{
    border: 1px solid #ccc;
    padding: 1px 3px;
    text-align: center;
    white-space: nowrap;
    font-size: 6.5pt;
  }}
  th {{ background: #4A4A4A !important; color: white !important; font-size: 6pt; }}
  /* スタッフ名列（各行の1列目）を広めに固定 */
  td:first-child, th:first-child {{
    min-width: 120px;
    width: 120px;
    text-align: left;
    font-weight: bold;
    background: #fafafa;
    white-space: nowrap;
    overflow: visible;
  }}
  .no-print {{
    margin-bottom: 5mm;
    padding: 5px;
    background: #e8f4fd;
    border-radius: 4px;
    font-size: 10pt;
  }}
  button {{
    padding: 6px 16px;
    background: #1f77b4;
    color: white;
    border: none;
    border-radius: 4px;
    cursor: pointer;
    font-size: 10pt;
  }}
</style>
</head><body>
<div class="no-print">
  <strong>📄 PDFとして保存する方法：</strong>
  右上の <button onclick="window.print()">🖨️ 印刷</button> ボタン（またはCtrl+P）→
  送信先を「<strong>PDFに保存</strong>」に変更 → 保存
</div>
<h2>{view_year}年{view_month}月 シフト表</h2>
{_html_table}
</body></html>"""
                _html_bytes = _full_html.encode("utf-8")
                st.download_button(
                    "📄 印刷用HTML出力",
                    _html_bytes,
                    file_name=f"shift_{view_year}_{view_month:02d}.html",
                    mime="text/html",
                    use_container_width=True,
                    help="ダウンロード後ブラウザで開き、Ctrl+P→PDFに保存"
                )

            # ── シフト検閲 ──────────────────────────────────────────
            st.divider()
            st.subheader("🔍 シフト検閲")
            st.caption("シフト表の記載漏れ・条件違反を自動チェックします")

            if st.button("🔍 検閲を実行", type="primary", key="inspect_btn"):
                from datetime import timedelta as _itd
                _, _nd = calendar.monthrange(view_year, view_month)
                _all_days = [date(view_year, view_month, d) for d in range(1, _nd + 1)]

                _shifts = data.get("shifts", {})
                _staff  = data["staff"]
                _reqs   = data.get("requests", {})

                # 稼働不可シフト（当番・夜勤と重複禁止）
                _BUSY = {"夜入","夜明","代休","ICU代休","透析代休","希望休"}
                # 希望休種別
                _OFF_REQS = {"off_duty", "off_only", "no_duty"}

                errors   = []
                warnings = []

                for _d in _all_days:
                    _dk  = _d.strftime("%Y-%m-%d")
                    _lbl = f"{view_month}/{_d.day}({['月','火','水','木','金','土','日'][_d.weekday()]})"
                    _day_data = _shifts.get(_dk, {})
                    _duty     = _day_data.get("_duty", {})
                    _is_work  = is_work_day(_d)

                    # ── 重複チェック: 当番 × シフト・希望休 ──────────────
                    # ope当番者
                    for _s in _duty.get("ope", []):
                        _st = _day_data.get(_s, "")
                        _rq = _reqs.get(_s, {}).get(_dk, "")
                        if _st in _BUSY:
                            errors.append(f"**{_lbl}** — {_staff[_s]['name']} がope当番 かつ {_st}")
                        if _rq in _OFF_REQS:
                            errors.append(f"**{_lbl}** — {_staff[_s]['name']} がope当番 かつ 希望休({_rq})")
                    # B/C/D当番者
                    for _dept in ["B","C","D"]:
                        _s = _duty.get(_dept, "")
                        if not _s: continue
                        _st = _day_data.get(_s, "")
                        _rq = _reqs.get(_s, {}).get(_dk, "")
                        if _st in _BUSY:
                            errors.append(f"**{_lbl}** — {_staff[_s]['name']} が{_dept}当番 かつ {_st}")
                        if _rq in _OFF_REQS:
                            errors.append(f"**{_lbl}** — {_staff[_s]['name']} が{_dept}当番 かつ 希望休({_rq})")

                    # ── 1. 夜勤（夜入・夜明け）が毎日いるか ──────────────
                    _yoru_in  = [s for s,v in _day_data.items() if v == "夜入" and s != "_duty"]
                    _yoru_ake = [s for s,v in _day_data.items() if v == "夜明" and s != "_duty"]
                    if not _yoru_in:
                        errors.append(f"**{_lbl}** — 夜入がいません")
                    if not _yoru_ake and _d.day > 2:
                        errors.append(f"**{_lbl}** — 夜明けがいません")

                    # ── 2. 全日: ope当番が実稼働2名・ope1が1名以上 ────────
                    # 夜入・希望休などを除いた実際に稼働可能なope当番者のみカウント
                    _ope_raw  = _duty.get("ope", [])
                    _ope_duty = [s for s in _ope_raw
                                 if _day_data.get(s,"") not in _BUSY
                                 and _reqs.get(s,{}).get(_dk,"") not in _OFF_REQS]
                    if len(_ope_duty) < 2:
                        _names = [_staff[s]['name'] for s in _ope_raw]
                        errors.append(
                            f"**{_lbl}** — オペ当番の実稼働が{len(_ope_duty)}名"
                            f"（登録: {_names}、夜入/希望休等で稼働不可の人を含む可能性）"
                        )
                    elif not any("ope1" in _staff.get(s,{}).get("duty_skills",[]) for s in _ope_duty):
                        errors.append(
                            f"**{_lbl}** — オペ当番にope1スキルがいません"
                            f"（{[_staff[s]['name'] for s in _ope_duty]}）"
                        )

                    if _is_work:
                        # ── 3. 平日: 各部門当番があるか ──────────────────
                        if not _duty.get("B"):
                            errors.append(f"**{_lbl}** — ICU当番（B☆）がいません")
                        if not _duty.get("C"):
                            errors.append(f"**{_lbl}** — カテ当番（C☆）がいません")
                        if not _duty.get("D"):
                            errors.append(f"**{_lbl}** — 透析当番（D☆）がいません")
                    else:
                        # ── 4. 土日祝: ICU日勤（☆なし）がいるか ─────────
                        _icu_b_duty_sid = _duty.get("B", "")
                        _icu_day_staff  = [s for s,v in _day_data.items()
                                           if v == "B" and s != "_duty" and s != _icu_b_duty_sid]
                        if not _icu_day_staff:
                            warnings.append(f"**{_lbl}** — 土日祝のICU日勤（☆なし）がいません")

                        # ── 5. 土曜・祝日: 透析日勤2名（日曜は休み）────────
                        if _d.weekday() != 6:
                            _dial_day_staff = [s for s,v in _day_data.items()
                                               if v in ("D","透析") and s != "_duty"]
                            if len(_dial_day_staff) < 2:
                                warnings.append(
                                    f"**{_lbl}** — 透析日勤が{len(_dial_day_staff)}名（土曜・祝日は2名推奨）"
                                )

                # 結果表示
                if not errors and not warnings:
                    st.success("✅ 問題は検出されませんでした！すべての条件を満たしています。")
                else:
                    if errors:
                        st.error(f"❌ エラー {len(errors)}件")
                        for e in errors:
                            st.markdown(f"- {e}")
                    if warnings:
                        st.warning(f"⚠️ 警告 {len(warnings)}件")
                        for w in warnings:
                            st.markdown(f"- {w}")
                    st.caption(f"チェック対象: {view_year}年{view_month}月（全{_nd}日）")


    # ══════════════════════════════════════════
    # タブ5: バランス
    # ══════════════════════════════════════════
    with tab5:
      if st.session_state.unlocked:
        st.subheader("スタッフ別バランス")

        col_y5, col_m5, _ = st.columns([1, 1, 2])
        with col_y5:
            bal_year  = int(st.number_input("年", 2020, 2100, date.today().year,  key="tab5_year"))
        with col_m5:
            bal_month = int(st.number_input("月", 1, 12, date.today().month, key="tab5_month"))

        if not data["staff"]:
            st.info("スタッフを登録してください")
        else:
            _, num_days = calendar.monthrange(bal_year, bal_month)
            all_days = [date(bal_year, bal_month, d) for d in range(1, num_days + 1)]

            rows = []
            for sid, sinfo in data["staff"].items():
                night_in    = 0  # 夜勤入り数
                night_dayk  = 0  # 夜勤代休
                icu_dayk    = 0  # ICU代休
                dial_dayk   = 0  # 透析代休
                hol_rest    = 0  # 純粋な休日（土日祝で勤務なし）
                req_rest    = 0  # 希望休（off_duty / off_only）日数
                hol_day     = 0  # 休日日勤数
                wd_duty     = 0  # 平日当番数
                sat_duty    = 0  # 土曜当番数
                hol_duty    = 0  # 日祝当番数

                req_map = data.get("requests", {}).get(sid, {})

                for d in all_days:
                    dk       = d.strftime("%Y-%m-%d")
                    dtype    = day_type(d)
                    is_wd    = (dtype == "weekday")
                    is_sat   = (dtype == "saturday")
                    is_hol   = (dtype == "holiday")

                    day_data = data["shifts"].get(dk, {})
                    status   = day_data.get(sid, "")
                    duties   = day_data.get("_duty", {})

                    req = req_map.get(dk, "")

                    # 代休の種別カウント
                    if status == "代休":          night_dayk += 1
                    elif status == "ICU代休":     icu_dayk   += 1
                    elif status == "透析代休":    dial_dayk  += 1
                    elif status == "夜入":        night_in   += 1

                    # 休日日勤（土日祝のA/B/C/D日勤）
                    if (is_sat or is_hol) and status in ("A","B","C","D"):
                        hol_day += 1

                    # 希望休カウント：平日の希望休のみカウント
                    # （土日祝は元々休日なので希望休と重なっても別カウントしない）
                    if is_wd and req in ("off_duty", "off_only"):
                        req_rest += 1

                    # 純休日：土日祝で勤務なし（代休・日勤・夜勤でない）
                    if (is_sat or is_hol) and status not in (
                            "夜入","夜明","A","B","C","D","代休","ICU代休","透析代休"):
                        hol_rest += 1

                    # 当番数
                    is_on_duty = (
                        any(v == sid for k, v in duties.items()
                            if k != "ope" and isinstance(v, str))
                        or sid in duties.get("ope", [])
                    )
                    if is_on_duty:
                        if is_wd:    wd_duty  += 1
                        elif is_sat: sat_duty += 1
                        else:        hol_duty += 1

                total_rest = hol_rest + night_dayk + icu_dayk + dial_dayk + req_rest
                rows.append({
                    "スタッフ":        sinfo["name"],
                    "メイン部署":      dept_display(data, sinfo["main_dept"]),
                    "休日数(合計)":    total_rest,
                    "　純休日":        hol_rest,
                    "　夜勤代休":      night_dayk,
                    "　ICU代休":       icu_dayk,
                    "　透析代休":      dial_dayk,
                    "　希望休":        req_rest,
                    "夜勤数":          night_in,
                    "休日日勤数":      hol_day,
                    "平日当番":        wd_duty,
                    "土曜当番":        sat_duty,
                    "日祝当番":        hol_duty,
                    "当番合計":        wd_duty + sat_duty + hol_duty,
                })

            df_bal = pd.DataFrame(rows)

            # 色付け: 休日数(合計)・当番合計=青、夜勤数・休日日勤数=緑、希望休=紫
            def style_balance(df):
                styles = pd.DataFrame("", index=df.index, columns=df.columns)
                for col in ["休日数(合計)", "当番合計"]:
                    if col in df.columns:
                        styles[col] = "background-color: rgba(74,144,217,0.15)"
                for col in ["夜勤数", "休日日勤数"]:
                    if col in df.columns:
                        styles[col] = "background-color: rgba(39,174,96,0.15)"
                if "　希望休" in df.columns:
                    styles["　希望休"] = "background-color: rgba(142,68,173,0.12)"
                return styles

            st.dataframe(
                df_bal.style.apply(style_balance, axis=None),
                use_container_width=True, hide_index=True
            )

            # CSV
            csv_b = df_bal.to_csv(index=False, encoding="utf-8-sig")
            st.download_button("📥 CSVダウンロード", csv_b,
                file_name=f"balance_{bal_year}_{bal_month:02d}.csv",
                mime="text/csv")


if __name__ == "__main__":
    main()
