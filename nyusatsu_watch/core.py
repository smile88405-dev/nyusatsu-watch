# -*- coding: utf-8 -*-
"""共通部分: データベース、差分レポート、出力"""
import csv, datetime as dt, json, os, re, sqlite3, unicodedata

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.environ.get("NW_DATA") or os.path.join(ROOT, "data")
OUT = os.environ.get("NW_OUT") or os.path.join(ROOT, "out")
DOCS = os.environ.get("NW_DOCS") or os.path.join(ROOT, "docs")
DB = os.path.join(DATA, "nyusatsu.sqlite")
RAW = os.environ.get("NW_RAW") or os.path.join(DATA, "raw")   # 取得した詳細HTMLの置き場
WATCH = os.path.join(ROOT, "watchlist.txt")
REPO_URL = "https://github.com/smile88405-dev/nyusatsu-watch"   # 公開先（閲覧画面のフッターに出る）

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs(id INTEGER PRIMARY KEY, started TEXT, note TEXT, srcs TEXT);
CREATE TABLE IF NOT EXISTS documents(            -- 入札結果PDFなど、原本1ファイル = 1行
  src TEXT, url TEXT, label TEXT, bid_date TEXT, fy INTEGER, size_label TEXT, sha256 TEXT,
  first_run INTEGER, last_changed_run INTEGER, n_tenders INTEGER, PRIMARY KEY(src,url));
CREATE TABLE IF NOT EXISTS tenders(              -- 案件 1件 = 1行
  id INTEGER PRIMARY KEY, src TEXT, ext_id TEXT, doc_url TEXT, number TEXT, title TEXT, category TEXT,
  place TEXT, org TEXT, bid_date TEXT, fy INTEGER, method TEXT, yotei NUMERIC, yotei_label TEXT,
  floor_label TEXT, floor NUMERIC, n_declared INTEGER, n_parsed INTEGER, status TEXT, winner TEXT,
  win_amount NUMERIC, rate REAL, lottery INTEGER, warn TEXT, sig TEXT,
  first_run INTEGER, updated_run INTEGER, prev_status TEXT, UNIQUE(src,ext_id));
CREATE TABLE IF NOT EXISTS bids(                 -- 入札者 1社 = 1行
  tender_id INTEGER, no INTEGER, company TEXT, company_key TEXT, branch TEXT,
  amounts TEXT, final_amount NUMERIC, remark TEXT, result TEXT);
CREATE TABLE IF NOT EXISTS notices(              -- 入札公告ページ
  src TEXT, url TEXT, title TEXT, notice_date TEXT, first_run INTEGER, last_seen_run INTEGER,
  gone_run INTEGER, PRIMARY KEY(src,url));
CREATE INDEX IF NOT EXISTS i_bids_key ON bids(company_key);
CREATE INDEX IF NOT EXISTS i_bids_t ON bids(tender_id);
CREATE INDEX IF NOT EXISTS i_t_doc ON tenders(src,doc_url);
"""


def connect():
    os.makedirs(DATA, exist_ok=True)
    con = sqlite3.connect(DB); con.executescript(SCHEMA)
    return con


# ---------------------------------------------------------------- 小道具
def to_num(x):
    if x in (None, ""): return None
    x = str(x).replace(",", "")
    return float(x) if "." in x else int(x)


def wareki(s):
    """'令和8年10月7日' → '2026-10-07'"""
    m = re.search(r"令和\s*(\d+|元)年\s*(\d+)月\s*(\d+)日", unicodedata.normalize("NFKC", s))
    if not m: return None
    y = 1 if m.group(1) == "元" else int(m.group(1))
    return "%04d-%02d-%02d" % (2018 + y, int(m.group(2)), int(m.group(3)))


def fiscal_year(d):
    return int(d[:4]) if int(d[5:7]) >= 4 else int(d[:4]) - 1


def this_fy():
    return fiscal_year(dt.date.today().isoformat())


def nkey(name):
    """業者名の照合キー: 全半角・空白・法人格表記のゆれを吸収"""
    k = re.sub(r"\s+", "", unicodedata.normalize("NFKC", name or ""))
    return k.replace("株式会社", "(株)").replace("有限会社", "(有)")


def finish(t, status, winners, warn):
    """落札者・落札率など、どの自治体でも同じ計算をまとめて行う"""
    for b in t["bids"]:
        b["final"] = b["amounts"][-1] if b["amounts"] else None
    w = winners[0] if len(winners) == 1 and status == "落札" else None
    t["status"], t["n_parsed"] = status, len(t["bids"])
    t["winner"] = w["company"] if w else None
    t["win_amount"] = w["final"] if w else None
    t["lottery"] = 1 if w and re.search("くじ|抽選", w.get("remark") or "") else 0
    t["rate"] = round(w["final"] / t["yotei"] * 100, 2) if w and w["final"] and t.get("yotei") else None
    if t["rate"] and not (30 <= t["rate"] <= 100.5): warn.append("落札率が範囲外")
    t["warn"] = "; ".join(warn)
    return t


COLS = ("doc_url number title category place org bid_date fy method yotei yotei_label floor_label floor n_declared "
        "n_parsed status winner win_amount rate lottery warn sig").split()


def upsert(con, src, run_id, t):
    """案件を登録する。戻り値: 'new'（新規） / 'updated'（内容が変わった） / 'same'"""
    t.setdefault("fy", fiscal_year(t["bid_date"]) if t.get("bid_date") else None)
    t.setdefault("yotei_label", "予定価格"); t.setdefault("org", ""); t.setdefault("sig", "")
    row = con.execute("SELECT id,status,sig FROM tenders WHERE src=? AND ext_id=?", (src, t["ext_id"])).fetchone()
    vals = [t.get(c) for c in COLS]
    if row is None:
        cur = con.execute("INSERT INTO tenders(src,ext_id,%s,first_run) VALUES(?,?,%s,?)" %
                          (",".join(COLS), ",".join("?" * len(COLS))), [src, t["ext_id"]] + vals + [run_id])
        tid, kind = cur.lastrowid, "new"
    else:
        tid = row[0]
        if row[2] and row[2] == t["sig"]: return "same"
        con.execute("DELETE FROM bids WHERE tender_id=?", (tid,))
        con.execute("UPDATE tenders SET %s,updated_run=?,prev_status=? WHERE id=?" % ",".join(c + "=?" for c in COLS),
                    vals + [run_id, row[1], tid])
        kind = "updated"
    for b in t["bids"]:
        con.execute("INSERT INTO bids VALUES(?,?,?,?,?,?,?,?,?)",
                    (tid, b["no"], b["company"], nkey(b["company"]), b.get("branch", ""), json.dumps(b["amounts"]),
                     b.get("final", b["amounts"][-1] if b["amounts"] else None), b.get("remark", ""), b.get("result", "")))
    return kind


# ---------------------------------------------------------------- 見張りリスト・差分
def watchlist():
    if not os.path.exists(WATCH): return []
    return [l.strip() for l in open(WATCH, encoding="utf-8") if l.strip() and not l.startswith("#")]


def fmt(n):
    return "{:,}円".format(int(n)) if n is not None else "—"


def diff_report(con, run_id, results, first, sources):
    today = dt.date.today().isoformat()
    L = ["# 入札ウォッチ 差分レポート", "", "実行: %s (run #%d)" % (today, run_id), ""]
    for src, d in results.items():
        label = sources[src].LABEL
        L += ["## " + label, ""]
        if "error" in d:
            L += ["取得に失敗しました: `%s`" % d["error"], ""]; continue
        if first.get(src):
            n = con.execute("SELECT COUNT(*) FROM tenders WHERE src=?", (src,)).fetchone()[0]
            L += ["初回取込のため差分はありません。案件 %d件を登録しました。" % n, ""]; continue
        if not any(d.values()): L += ["前回から変化はありません。", ""]; continue
        if d.get("new_notices"):
            L += ["### 新しい入札公告 (%d件)" % len(d["new_notices"]), ""]
            for u in d["new_notices"]:
                L.append("- [%s](%s)" % (con.execute("SELECT title FROM notices WHERE src=? AND url=?", (src, u)).fetchone()[0], u))
            L.append("")
        if d.get("changed_docs"):
            L += ["### 差し替えられた原本 (%d本)" % len(d["changed_docs"]), ""] + ["- " + u for u in d["changed_docs"]] + [""]
        for key, head in (("first_run", "新しい案件"), ("updated_run", "状態が変わった案件")):
            rows = con.execute("SELECT bid_date,title,method,yotei,winner,win_amount,rate,n_parsed,status,lottery,prev_status "
                               "FROM tenders WHERE src=? AND %s=? %s ORDER BY bid_date DESC,id" %
                               (key, "AND first_run<>updated_run" if key == "updated_run" else ""), (src, run_id)).fetchall()
            if not rows: continue
            L += ["### %s (%d件)" % (head, len(rows)), "", "| 入札日 | 件名 | 結果 | 落札者 | 落札額 | 応札 |", "|---|---|---|---|--:|--:|"]
            for r in rows[:200]:
                st = r[8] if key == "first_run" else "%s → %s" % (r[10] or "?", r[8])
                L.append("| %s | %s | %s | %s | %s | %s |" % (r[0], r[1], st, (r[4] or "—") + ("（くじ）" if r[9] else ""), fmt(r[5]), r[7]))
            if len(rows) > 200: L.append("| … | ほか %d件 | | | | |" % (len(rows) - 200))
            L.append("")
        if d.get("gone_notices"):
            L += ["### 掲載が終わった公告 (%d件)" % len(d["gone_notices"]), ""]
            L += ["- " + con.execute("SELECT title FROM notices WHERE src=? AND url=?", (src, u)).fetchone()[0] for u in d["gone_notices"]] + [""]
    hits = []
    for w in watchlist():
        for r in con.execute("SELECT t.src,t.bid_date,t.title,b.company,b.final_amount,b.result FROM bids b JOIN tenders t ON t.id=b.tender_id "
                             "WHERE b.company_key LIKE ? AND (t.first_run=? OR t.updated_run=?) ORDER BY t.bid_date DESC",
                             ("%" + nkey(w) + "%", run_id, run_id)):
            hits.append("- **%s** [%s] %s 「%s」 %s %s %s" % (w, sources[r[0]].LABEL, r[1], r[2], r[3], fmt(r[4]), r[5] or "落札できず"))
    if not all(first.get(s) for s in results):
        L += ["## 見張りリストの該当 (%d件)" % len(hits), ""] + (hits or ["該当なし（見張り対象: %s）" % "、".join(watchlist())]) + [""]
    os.makedirs(OUT, exist_ok=True)
    p = os.path.join(OUT, "diff_%s_run%02d.md" % (today.replace("-", ""), run_id))
    open(p, "w", encoding="utf-8").write("\n".join(L))
    return p


# ---------------------------------------------------------------- 出力
def export(con, sources):
    os.makedirs(OUT, exist_ok=True); os.makedirs(DOCS, exist_ok=True)
    runs = [dict(id=r[0], at=r[1], note=r[2], srcs=r[3]) for r in con.execute("SELECT id,started,note,srcs FROM runs ORDER BY id")]
    blobs, meta = {}, []
    for src, mod in sources.items():
        mine = [r["id"] for r in runs if src in (r["srcs"] or "").split(",")]
        if not mine: continue
        last, first = mine[-1], mine[0]
        isnew = lambda x: x == last and last != first
        T = []
        for r in con.execute("SELECT id,doc_url,number,title,category,place,org,bid_date,fy,method,yotei,yotei_label,floor_label,floor,"
                             "n_parsed,status,winner,win_amount,rate,lottery,warn,first_run,updated_run,prev_status FROM tenders "
                             "WHERE src=? ORDER BY bid_date DESC,id", (src,)):
            bids = [dict(c=b[0], br=b[1], a=json.loads(b[2]), r=b[3], m=b[4]) for b in
                    con.execute("SELECT company,branch,amounts,result,remark FROM bids WHERE tender_id=? ORDER BY no", (r[0],))]
            T.append(dict(id=r[0], doc=r[1], no=r[2], title=r[3], cat=r[4], place=r[5], org=r[6], date=r[7], fy=r[8], method=r[9],
                          yotei=r[10], yl=r[11], floorLabel=r[12], floor=r[13], n=r[14], status=r[15], winner=r[16], win=r[17],
                          rate=r[18], lottery=r[19], warn=r[20], isNew=isnew(r[21]),
                          upd=(r[23] if isnew(r[22]) and not isnew(r[21]) else None), bids=bids))
        N = [dict(url=r[0], title=r[1], date=r[2], isNew=isnew(r[3]), gone=r[4] is not None) for r in
             con.execute("SELECT url,title,notice_date,first_run,gone_run FROM notices WHERE src=? ORDER BY notice_date DESC,url DESC", (src,))]
        D = [dict(url=r[0], label=r[1], date=r[2], n=r[3], isNew=isnew(r[4])) for r in
             con.execute("SELECT url,label,bid_date,n_tenders,first_run FROM documents WHERE src=? ORDER BY bid_date DESC", (src,))]
        blobs[src] = dict(tenders=T, notices=N, docs=D)
        meta.append(dict(src=src, label=mod.LABEL, scope=mod.SCOPE, portal=mod.PORTAL, n=len(T),
                         runs=[r for r in runs if r["id"] in mine], note=getattr(mod, "NOTE", "")))
        with open(os.path.join(OUT, "%s_anken.csv" % src), "w", encoding="utf-8-sig", newline="") as f:
            w = csv.writer(f)
            w.writerow(["入札日", "年度", "番号", "件名", "工種", "場所", "発注課所", "方式", "基準額", "基準額の種類", "下限種別", "下限価格",
                        "応札者数", "結果", "落札者", "落札額", "落札率", "くじ", "要確認", "出典"])
            for t in T:
                w.writerow([t["date"], t["fy"], t["no"], t["title"], t["cat"], t["place"], t["org"], t["method"], t["yotei"], t["yl"],
                            t["floorLabel"], t["floor"], t["n"], t["status"], t["winner"], t["win"], t["rate"],
                            "○" if t["lottery"] else "", t["warn"], t["doc"]])
        with open(os.path.join(OUT, "%s_nyusatsusha.csv" % src), "w", encoding="utf-8-sig", newline="") as f:
            w = csv.writer(f)
            w.writerow(["入札日", "年度", "番号", "件名", "業者名", "支店等", "入札額(最終)", "全回の入札額", "結果", "備考", "基準額", "出典"])
            for t in T:
                for b in t["bids"]:
                    w.writerow([t["date"], t["fy"], t["no"], t["title"], b["c"], b["br"], b["a"][-1] if b["a"] else "",
                                " → ".join(map(str, b["a"])), b["r"], b["m"], t["yotei"], t["doc"]])
    head = dict(generated=dt.datetime.now().strftime("%Y-%m-%d %H:%M"), watch=watchlist(), sources=meta, repo=os.environ.get("NW_REPO_URL", REPO_URL))
    tpl = os.path.join(os.path.dirname(os.path.abspath(__file__)), "viewer_template.html")
    enc = lambda o: json.dumps(o, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    html = open(tpl, encoding="utf-8").read().replace("__HEAD__", enc(head))
    html = html.replace("__BLOBS__", "\n".join('<script type="application/json" id="blob-%s">%s</script>' % (s, enc(b)) for s, b in blobs.items()))
    open(os.path.join(DOCS, "index.html"), "w", encoding="utf-8").write(html)
    return head
