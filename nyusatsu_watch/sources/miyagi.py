# -*- coding: utf-8 -*-
"""宮城県 入札情報サービス（建設工事）: 入札結果を月ごとに一覧→詳細の順に取得する

公開範囲は県の定めによる（建設工事は予定価格400万円超、入札保留中を除く）。
予定価格は詳細画面では多くが「事前公開なし」で、金額は添付の入札調書PDFにある（本版では未読）。
"""
import datetime as dt, html, os, random, re, sys
from ..http import Session
from .. import core

NAME, LABEL = "miyagi", "宮城県"
SCOPE = "入札情報サービスの「建設工事」入札結果（令和7〜8年度、予定価格400万円超）"
PORTAL = "https://miyagi.efftis.jp/04000/PPI/Public/Server"
NOTE = "予定価格が画面に載っていない案件（事前公開なし等）は落札率を出していません。金額の根拠は添付の入札調書PDFにあります（本版では未読）。"
U = PORTAL


def cell(x):
    x = html.unescape(re.sub(r"<[^>]+>", " ", x))
    return re.sub(r"[\s　]+", " ", x).strip()


def post(s, rid, **kw):
    d = {"RandomRequestNumber": str(random.random()), "RandomRequestKey": "", "RequestID": rid, "supply_Classification_CD": "00"}
    d.update(kw)
    return s.get(U, d)


def login(s):
    s.get(U); post(s, "BIDRESULTSEARCH", mainMenu="1")


def list_month(s, ym):
    rows, off = [], 0
    while True:
        h = post(s, "BIDRESULTSEARCHOVER", public_Organization_ID="", wto_CD="", public_Department_ID="", bid_Type_ID="",
                 public_Section_ID="", classification_ID="", open_Bid_Execute_Date_From=ym + "01", open_Bid_Execute_Date_To=ym + "01",
                 project_No="", project_Name="", showCount="50", result_Open_CD="2", showOffset=str(off), processType="1", searchType="1")
        got = 0
        for r in re.split(r"<TR>", h, flags=re.I):
            m = re.search(r"onDetail\('([^']+)'\)", r)
            if not m: continue
            c = [cell(x) for x in re.findall(r'<TD class="ListData0?\d?"[^>]*>(.*?)</TD>', r, re.S | re.I)]
            if len(c) < 8: continue
            got += 1
            rows.append(dict(id=m.group(1), number=c[1], title=c[2], org=c[3], date=c[4], money=c[5], winner=c[6], sig="|".join(c[1:8])))
        if got < 50: break
        off += 50
    return rows


def parse_detail(h, row):
    h = re.sub(r"<!--.*?-->", "", h, flags=re.S)
    kv = {}
    for m in re.finditer(r'<TD class="FieldLabel"[^>]*>(.*?)</TD>\s*<TD class="FieldData"[^>]*>(.*?)</TD>\s*</TR>', h, re.S | re.I):
        kv.setdefault(cell(m.group(1)), cell(m.group(2)))
    money = lambda x: core.to_num(x.replace("円", "")) if re.fullmatch(r"[\d,.]+円?", x or "") and x else None
    t = dict(ext_id=row["id"], number=kv.get("調達案件番号") or row.get("number"), title=kv.get("調達案件名称") or row.get("title"),
             org=kv.get("工事担当") or row.get("org", ""), method=kv.get("入札方式", "") + ("（総合評価）" if "総合評価" in kv.get("落札方式", "") else ""),
             category=kv.get("工種(等級)", ""), place="", bid_date=core.wareki(kv.get("開札日", "")), doc_url=PORTAL, sig=row["sig"],
             yotei=money(kv.get("予定価格", "")), yotei_label="予定価格", n_declared=None,
             floor=money(kv.get("調査基準価格", "")) or money(kv.get("最低制限価格", "")),
             floor_label="調査基準価格" if money(kv.get("調査基準価格", "")) else ("最低制限価格" if money(kv.get("最低制限価格", "")) else None))
    bids = []
    i = h.find("業者名称")
    seg = h[i:h.find("</TABLE>", i)] if i > 0 else ""
    for r in re.split(r"<TR>", seg, flags=re.I)[1:]:
        c = [cell(x) for x in re.findall(r'<TD class="ListData"[^>]*>(.*?)</TD>', r, re.S | re.I)]
        if len(c) < 5 or not c[2]: continue
        rounds = c[4:]                                   # (結果, 金額) の繰り返し
        amounts = [core.to_num(x.replace("円", "")) for x in rounds[1::2] if re.fullmatch(r"[\d,.]+円", x)]
        remark = " ".join(x for x in rounds[0::2] if x)
        result = "落札" if c[3] in ("○", "◯", "〇") else next((v for k, v in (("失格", "失格"), ("不適当", "落札不適当"), ("辞退", "辞退"), ("無効", "無効"), ("不参加", "不参加"),
                                                                  ("棄権", "棄権"), ("取消", "取消")) if k in remark), "")
        bids.append(dict(no=len(bids) + 1, company=c[2], branch="", amounts=amounts, remark=remark, result=result))
    t["bids"] = bids
    state = kv.get("入札結果", "")
    winners = [b for b in bids if b["result"] == "落札"]
    warn = []
    if re.search(r"不調|不落|中止|取止|取りやめ", state): status = state
    elif winners: status = "落札"
    elif bids: status = state or "落札者なし"; warn.append("落札者を特定できず")
    else: status = state or "不明"
    if len(winners) > 1: warn.append("落札者が複数")
    core.finish(t, status, winners, warn)
    if status == "落札" and t["win_amount"] is None and money(kv.get("落札金額", "")):
        t["win_amount"] = money(kv["落札金額"])
    return t


def months(fy):
    now = dt.date.today()
    out = []
    for k in range(12):
        y, m = (fy, 4 + k) if k < 9 else (fy + 1, k - 8)
        if (y, m) <= (now.year, now.month): out.append("%04d%02d" % (y, m))
    return out


def run(con, run_id, opts):
    years = opts.get("years") or [core.this_fy()]
    shard = opts.get("shard")                      # (k, N): 一覧のk番目/N だけ担当（並行取得用）
    rawdir = os.path.join(core.RAW, NAME); os.makedirs(rawdir, exist_ok=True)
    off = opts.get("offline")                      # 保存済みの詳細HTMLだけで作り直す（通信しない）
    s = None
    if not off: s = Session("cp932"); login(s)
    new, upd, n = [], [], 0
    known = dict(con.execute("SELECT ext_id,sig FROM tenders WHERE src=?", (NAME,)).fetchall())
    for y in ([None] if off else years):
        for ym in ([None] if off else months(y)):
            if off:
                rows = [dict(id=f[:-5], date="", sig=open(os.path.join(rawdir, f + ".sig"), encoding="utf-8").read())
                        for f in sorted(os.listdir(rawdir)) if f.endswith(".html")]
            else:
                rows = list_month(s, ym)
            print("  %s %s: 一覧 %d件" % (LABEL, ym or "保存済み", len(rows)), file=sys.stderr)
            for k, row in enumerate(rows):
                if shard and k % shard[1] != shard[0]: continue
                d = "20%02d-%s-%s" % (int(row["date"][1:3]) + 18, row["date"][4:6], row["date"][7:9]) if re.match(r"R\d\d/\d\d/\d\d", row["date"]) else ""
                if opts.get("as_of") and d > opts["as_of"]: continue
                if known.get(row["id"]) == row["sig"]: continue
                if opts.get("limit") and n >= opts["limit"]: continue
                n += 1
                path = os.path.join(rawdir, row["id"] + ".html")
                cached = (opts.get("use_cache") or off) and os.path.exists(path) and open(path + ".sig", encoding="utf-8").read() == row["sig"]
                if cached:
                    h = open(path, encoding="utf-8").read()
                else:
                    h = post(s, "BIDRESULTDETAIL", processType="2", searchType="1", showOffset="", project_ID=row["id"])
                    if "調達案件番号" not in h:                    # セッション切れ → 入り直して再取得
                        s = Session("cp932"); login(s)
                        h = post(s, "BIDRESULTDETAIL", processType="2", searchType="1", showOffset="", project_ID=row["id"])
                    open(path, "w", encoding="utf-8").write(h); open(path + ".sig", "w", encoding="utf-8").write(row["sig"])
                t = parse_detail(h, row)
                if not t["bid_date"]: t["bid_date"] = d or None
                if off and (not t["bid_date"] or core.fiscal_year(t["bid_date"]) not in years
                            or (opts.get("as_of") and t["bid_date"] > opts["as_of"])): continue
                (new if core.upsert(con, NAME, run_id, t) == "new" else upd).append(row["id"])
            con.commit()
    return dict(new_tenders=new, updated_tenders=upd)
