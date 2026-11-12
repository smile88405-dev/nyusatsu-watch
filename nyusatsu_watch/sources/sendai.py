# -*- coding: utf-8 -*-
"""仙台市 入札情報公開システム（工事）: 入札・見積結果を一覧→詳細の順に取得する

一覧(100件/頁)を全頁読み、新規または一覧上の表示が変わった案件だけ詳細を取り直す。
詳細HTMLは data/raw/sendai/ に保存する（再解析用。リポジトリには含めない）。
"""
import os, re, sys
from ..http import Session
from .. import core

NAME, LABEL = "sendai", "仙台市"
SCOPE = "入札情報公開システムの「工事」入札・見積結果（令和8年度）"
PORTAL = "https://www.epi-cloud.fwd.ne.jp/koukai/do/logon?name1=0620060006800640"
B = "https://www.epi-cloud.fwd.ne.jp/koukai/do/"
STARS = re.compile(r"^\*+$")


def cell(x):
    x = re.sub(r"<script.*?</script>|<[^>]+>", "", x, flags=re.S | re.I)
    return re.sub(r"\s+", " ", x.replace("&nbsp;", " ").replace("&nbsp", " ").replace("&amp;", "&")).strip()


def login(s):
    s.get(B + "KF001ShowAction?name1=0620060006800640")
    s.get(B + "KK000ShowAction", {"bukyoku": "", "kakakari": "", "kasho_name": "", "supplytype": "00",
                                  "hachukikan_name": "仙台市", "hachukikan_hidden": "1042ZZZZZZ"})
    s.get(B + "koukai_menu"); s.get(B + "koukai_main")      # 画面と同じ順に開かないと検索が失敗する
    s.get(B + "KK401ShowAction")


def list_year(s, year):
    p = {"A094": "", "nendo": str(year), "A046": "", "gyosyu": "", "koujimei": "", "koujiNo": "", "koujibangou": "",
         "ITEM_1_CONTENT": "", "hizukeKubun": "010", "date_start": "", "date_end": "", "orderKey1": "010",
         "orderKey2": "020", "A300": "040", "perPage": "100", "curPage": "1", "recCount": "", "checkList": ""}
    h = s.get(B + "KK401SearchAction", p)
    total = int((re.search(r'name="recCount"\s+value="(\d+)"', h) or [0, "0"])[1])
    rows, page = [], 1
    while True:
        f = s.get(B + "KFK401FrameShow")
        for r in re.split(r"<tr>", f, flags=re.I)[1:]:
            m = re.search(r"doEdit030\('(\d+)'\)", r)
            if not m: continue                                    # 契約結果(040)は対象外
            c = [cell(x) for x in re.findall(r"<td[^>]*>(.*?)</td>", r, re.S | re.I)]
            money = (re.search(r"sMoney = '([^']*)'", r) or [0, ""])[1]
            reg = (re.search(r"regist_date' VALUE='([^']*)'", r) or [0, ""])[1]
            rows.append(dict(id=m.group(1), date=c[1].replace("/", "-")[:10], title=c[2], number=c[3], method=c[4],
                             winner=c[5], money=money, org=c[7], sig="|".join([reg, money, c[5]])))
        if page * 100 >= total: break
        page += 1
        s.get(B + "KK401SearchAction?page=%d" % page)
    return rows


def parse_detail(h, row):
    body = h[h.lower().find("<body"):]
    kv = {}
    for m in re.finditer(r"<td class=['\"]TableTitle['\"][^>]*>(.*?)</td>\s*<td[^>]*>(.*?)</td>", body, re.S | re.I):
        fm = re.search(r"tagFormatMoney\('([^']*)'\)", m.group(2))
        kv.setdefault(cell(m.group(1)), fm.group(1) if fm else cell(m.group(2)))
    val = lambda k: "" if STARS.match(kv.get(k, "")) else kv.get(k, "")
    money = lambda x: core.to_num(x) if re.fullmatch(r"[\d,.]+", x or "") else None
    t = dict(ext_id=row["id"], number=row.get("number") or val("契約管理番号"), title=row.get("title") or val("工事名"),
             method=row.get("method") or val("入札方式"), org=row.get("org") or re.sub(r"[ 　]+", " ", val("課所名")),
             bid_date=(val("開札日時") or row.get("date") or "").replace("/", "-")[:10], category=val("工種"), place=val("工事場所"),
             yotei=money(val("予定価格(税抜)")) or money(val("設計額又は見積額(税抜)")),
             yotei_label="予定価格" if money(val("予定価格(税抜)")) else "設計額", floor_label=val("調査基準価格/最低制限価格区分") or None,
             floor=money(val("調査基準価格(税抜) /最低制限価格(税抜)")), doc_url=PORTAL, sig=row["sig"], n_declared=None)
    state = val("開札結果") or row.get("money", "")
    t["_nendo"] = val("年度")[:4]
    bids = []
    i = body.find("入札経過")
    for r in re.split(r"<TR>", body[i:], flags=re.I)[3:] if i > 0 else []:
        tds = re.findall(r"<td[^>]*>(.*?)</td>", r, re.S | re.I)
        if len(tds) < 3: continue
        c = [cell(x) for x in tds]
        name = re.sub(r"[ 　]+", " ", c[0]).strip()
        if not name: continue
        amounts = [core.to_num(x) for x in c[1:-1] if re.fullmatch(r"[\d,.]+", x)]
        remark = c[-1]
        red = bool(re.search(r"<FONT[^>]*COLOR", tds[0], re.I))
        result = "落札" if (red or remark.startswith("落札")) else \
            next((k for k in ("失格", "辞退", "無効", "不参加", "棄権") if k in remark), "")
        bids.append(dict(no=len(bids) + 1, company=name, branch="", amounts=amounts, remark=remark, result=result))
    t["bids"] = bids
    winners = [b for b in bids if b["result"] == "落札"]
    warn = []
    if "取止" in state or "不調" in state or "中止" in state: status = "不調・取止め"
    elif winners: status = "落札"
    elif "選定中" in state or "保留" in state: status = "選定中"
    elif bids: status = "落札者なし"; warn.append("落札者を特定できず")
    else: status = state or "不明"
    if len(winners) > 1: warn.append("落札者が複数")
    core.finish(t, status, winners, warn)
    return t


def offline_rows(rawdir):
    """保存済みの詳細HTMLだけで作り直すときの一覧（通信しない）"""
    for f in sorted(os.listdir(rawdir)):
        if f.endswith(".html"):
            yield dict(id=f[:-5], sig=open(os.path.join(rawdir, f + ".sig"), encoding="utf-8").read())


def run(con, run_id, opts):
    years = opts.get("years") or [core.this_fy()]
    shard = opts.get("shard")                      # (k, N): 一覧のk番目/N だけ担当（並行取得用）
    rawdir = os.path.join(core.RAW, NAME); os.makedirs(rawdir, exist_ok=True)
    off = opts.get("offline")
    s = None
    if not off: s = Session("cp932"); login(s)
    new, upd = [], []
    known = dict(con.execute("SELECT ext_id,sig FROM tenders WHERE src=?", (NAME,)).fetchall())
    for y in ([None] if off else years):
        rows = list(offline_rows(rawdir)) if off else list_year(s, y)
        print("  %s %s: 一覧 %d件" % (LABEL, "保存済み" if off else "%d年度" % y, len(rows)), file=sys.stderr)
        if opts.get("limit"): rows = rows[:opts["limit"]]
        for k, row in enumerate(rows):
            if shard and k % shard[1] != shard[0]: continue
            if opts.get("as_of") and row.get("date", "") > opts["as_of"]: continue
            if known.get(row["id"]) == row["sig"]: continue
            path = os.path.join(rawdir, row["id"] + ".html")
            cached = os.path.exists(path) and (opts.get("use_cache") or off)
            if cached and open(path + ".sig", encoding="utf-8").read() != row["sig"]: cached = False
            if cached:
                h = open(path, encoding="utf-8").read()
            else:
                h = s.get(B + "KK402ShowAction?control_no=" + row["id"], {})
                if "入札・見積結果情報閲覧" not in h:               # セッション切れ → 入り直して再取得
                    s = Session("cp932"); login(s)
                    h = s.get(B + "KK402ShowAction?control_no=" + row["id"], {})
                open(path, "w", encoding="utf-8").write(h); open(path + ".sig", "w", encoding="utf-8").write(row["sig"])
            t = parse_detail(h, row)
            nendo = t.pop("_nendo", "")
            if off and (str(nendo) not in [str(x) for x in years] or (opts.get("as_of") and t["bid_date"] > opts["as_of"])): continue
            (new if core.upsert(con, NAME, run_id, t) == "new" else upd).append(row["id"])
            if k % 50 == 0: con.commit()
        con.commit()
    return dict(new_tenders=new, updated_tenders=upd)
