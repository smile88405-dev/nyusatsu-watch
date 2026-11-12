# -*- coding: utf-8 -*-
"""大崎市: 市サイトの入札結果PDF（財政課契約担当分）と入札公告ページ

年度ページからPDFのリンクをたどり、新しいPDF・差し替えられたPDFだけ取得して解析する。
PDFは data/pdf/osaki/ に保存する（リポジトリには含めない）。必要なもの: poppler の pdftotext。
"""
import hashlib, os, re, subprocess, sys, unicodedata
from ..http import Session
from .. import core

NAME, LABEL = "osaki", "大崎市"
SCOPE = "市サイト掲載の入札結果PDF（財政課契約担当分、令和6〜8年度）と入札公告"
BASE = "https://www.city.osaki.miyagi.jp"
NK = BASE + "/shisei/jigyoshamuke/nyusatsu_keiyakujoho"
PORTAL = NK + "/index.html"
RESULT_INDEX = NK + "/2/index.html"
NOTICE_INDEX = NK + "/6/index.html"


def links(html):
    for m in re.finditer(r'<a[^>]*href="([^"]+)"[^>]*>(.*?)</a>', html, re.S):
        href = m.group(1)
        if href.startswith("//"): href = "https:" + href
        elif href.startswith("/"): href = BASE + href
        yield href, re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", m.group(2))).strip()


AMT = re.compile(r"\d{1,3}(?:,\d{3})+|(?<![\d/])\d{4,}(?![\d/])")
HEAD = re.compile(r"入\s*札（見積り）調\s*書")
KEIKA = re.compile(r"入\s*札（見積り）経\s*過")
ROW = re.compile(r"^\s{0,12}(\d{1,3})\s+(\S.*)$")

def yen(s):
    if not s: return None
    s = s.replace(",", "")
    return float(s) if "." in s else int(s)

def segs(line):
    return [x.strip() for x in re.split(r"\s{2,}", line.strip()) if x.strip()]

def parse_head(lines):
    r = dict(number=None, kind_label=None, title="", category="", place="", bid_date=None,
             method="", yotei=None, floor_label=None, floor=None, n_declared=None)
    title_parts, cat_parts, places = [], [], []
    seen_number = seen_date = False
    for ln in lines:
        s = ln.strip()
        if not s: continue
        m = re.search(r"予定価格\s+([\d,.]+)円", s)
        if m: r["yotei"] = yen(m.group(1))
        m = re.search(r"(最低制限価格|調査基準価格)\s+([\d,.]+)円", s)
        if m: r["floor_label"], r["floor"] = m.group(1), yen(m.group(2))
        m = re.search(r"入札（見積り）日：\s*(令和.+?日)", s)
        if m and not r["bid_date"]: r["bid_date"] = core.wareki(m.group(1))
        m = re.search(r"(工\s*事\s*番\s*号|契\s*約\s*番\s*号)\s+(\d{6,})", s)
        if m:
            r["kind_label"] = "工事" if "工" in m.group(1) else "契約"
            r["number"] = m.group(2); seen_number = True; continue
        m = re.match(r"^(\d{10})\s", s)
        if m and not seen_number:
            r["kind_label"] = "契約"; r["number"] = m.group(1); seen_number = True; continue
        m = re.search(r"業者数\s+(\d+)", s)
        if m: r["n_declared"] = int(m.group(1)); continue
        if re.match(r"入\s*札\s*月\s*日", s): seen_date = True; continue
        if re.match(r"場\s+所", s):
            sg = segs(re.sub(r"^場\s+所", "", s))
            if sg: places.append(sg[0])
            if len(sg) > 1 and not seen_date: cat_parts += sg[1:]
            continue
        if seen_date:
            if re.search(r"入札|契約|見積", s) and not r["method"] and "業者数" not in s:
                r["method"] = segs(s)[0]
            continue
        if not seen_number: continue
        # 件名・工種ブロック
        if re.match(r"^分\s*類", s):
            cat_parts += segs(re.sub(r"^分\s*類", "", s)); continue
        has_name = re.search(r"(工\s*事\s*名|件\s+名)", s)
        has_cat = re.search(r"工\s+種", s)
        if has_name or has_cat:
            body = s
            if has_cat:
                body, tail = re.split(r"工\s+種", s, maxsplit=1)
                cat_parts += segs(tail)
            body = re.sub(r"(工\s*事\s*名|件\s+名)", "  ", body)
            title_parts += segs(body)
        else:
            sg = segs(s)
            indent = len(ln) - len(ln.lstrip())
            if len(sg) >= 2: title_parts += sg[:-1]; cat_parts.append(sg[-1])
            elif indent > 45: cat_parts += sg
            else: title_parts += sg
    r["title"] = " ".join(title_parts).strip()
    r["category"] = "".join(cat_parts).strip()
    r["place"] = places[0] if places else ""
    return r

NUM = r"\d[\d,]*(?:\.\d+)?"
AMTS = re.compile(r"\s{2,}(" + NUM + r"(?:\s+" + NUM + r")*)(?=\s|$)")
LEAD = re.compile(r"^(" + NUM + r"(?:\s+" + NUM + r")*)(?=\s|$)")

def num(x):
    x = x.replace(",", "")
    return float(x) if "." in x else int(x)

def parse_bids(lines):
    bids, cur, mark, notes, pending = [], None, False, [], ""
    # 業者名が長くて「名前の行 / 番号＋金額の行 / 名前の続きの行」に折り返した調書を1行に戻す
    src, lines = [l for l in lines if l.strip()], []
    for ln in src:
        m = ROW.match(ln)
        if m and LEAD.match(m.group(2).strip()) and lines and not ROW.match(lines[-1]) \
                and not re.search(r"落札決定|単位:円|№", lines[-1]) and lines[-1].strip() not in ("落", "札"):
            prev = lines.pop().strip()
            if re.search(r"\s+落$", prev):
                prev = re.sub(r"\s+落$", "", prev); lines.append("落")
            lines.append("    %s %s  %s  〔折返〕" % (m.group(1), prev, m.group(2).strip())); continue
        lines.append(ln)
    for ln in lines:
        s = ln.strip()
        wrapped = s.endswith("〔折返〕")
        if wrapped: ln = ln.replace("〔折返〕", "").rstrip(); s = ln.strip()
        if re.search(r"\s{2,}札$", s) and cur is not None:
            if mark: cur["win_mark"] = True
            mark = False; s = re.sub(r"\s{2,}札$", "", s); ln = s
        if not s or "単位:円" in s or s.startswith("№"): continue
        if "総合評価落札方式に関する評価調書" in s: break
        if s == "落": mark = True; continue
        if s == "札":
            if cur is not None and mark: cur["win_mark"] = True
            mark = False; continue
        m = ROW.match(ln)
        if m and not re.fullmatch(r"[\d,.\s]+", s):
            rest = m.group(2)
            if re.search(r"\s{2,}落$", rest):          # 行末に「落」(随意契約の縦書き)
                rest = re.sub(r"\s{2,}落$", "", rest); mark = True
            am = AMTS.search(rest)
            if am:
                name, amounts, remark = rest[:am.start()], [num(x) for x in am.group(1).split()], rest[am.end():]
            else:
                sg = segs(rest); name, amounts, remark = rest, [], ""
                for i, x in enumerate(sg):             # 金額なし(辞退・無効など)
                    if re.search(r"辞退|無効|失格|不参加|欠席|棄権|取消|不適格|落札", x):
                        name, remark = "  ".join(sg[:i]), " ".join(sg[i:]); break
            ns = segs(name)
            cur = dict(no=int(m.group(1)), company=ns[0] if ns else name.strip(), branch=" ".join(ns[1:]),
                       amounts=amounts, remark=(pending + " " + " ".join(remark.split())).strip(), win_mark=False, wrapped=wrapped)
            pending = ""
            if mark: cur["win_mark"] = True
            bids.append(cur); continue
        if "落札決定" in s and not AMTS.search("  " + s):   # 行の上にずれて印字された備考 → 次の行の業者に付ける
            pending = " ".join(s.split()); continue
        if cur is None:
            notes.append(" ".join(s.split())); continue
        if re.fullmatch(r"[\d,.\s]+", s):                    # 金額だけの行
            cur["amounts"] += [num(x) for x in s.split()]; continue
        if not cur["amounts"]:                               # 支店名＋金額が次の行に折り返したケース
            am = AMTS.search("  " + s) if not LEAD.match(s) else None
            if am:
                pre = s[:max(am.start() - 2, 0)].strip()
                cur["branch"] = (cur["branch"] + " " + pre).strip()
                cur["amounts"] += [num(x) for x in am.group(1).split()]
                s = ("  " + s)[am.end():].strip()
                if not s: continue
        if cur.get("wrapped") and len(s) <= 12 and not re.search(r"落札|失格|辞退|無効|※", s):
            cur["branch"] = (cur["branch"] + s).strip(); cur["wrapped"] = False; continue
        cur["remark"] = (cur["remark"] + " " + " ".join(s.split())).strip()
    if pending and cur is not None: cur["remark"] = (cur["remark"] + " " + pending).strip()
    flags = []
    for b in bids:
        r = b["remark"]
        if re.search(r"※\s*不\s*調", r): flags.append("不調"); r = re.sub(r"※\s*不\s*調", "", r).strip()
        b["remark"] = r
    return bids, " ".join(dict.fromkeys(notes + flags))

EVROW = re.compile(r"^\s*(\S.*?)\s{2,}(\d[\d,]{4,})\s+(\S.*)$")

def parse_eval(lines):
    """総合評価落札方式の評価調書: 業者ごとに 評価点・順位、または評価対象外('-')を拾う"""
    ev = {}
    for ln in lines:
        m = EVROW.match(ln)
        if not m or "工事" in m.group(1): continue
        tok = m.group(3).split()
        key = (core.nkey(segs(m.group(1))[0]), yen(m.group(2)))
        if tok[0] == "-": ev[key] = None
        elif len(tok) >= 7 and re.fullmatch(r"[\d.]+", tok[5]): ev[key] = (tok[5], tok[6])
    return ev

def classify(b):
    r = b["remark"]
    if re.search(r"落札(決定|入札)", r) or b["win_mark"]: return "落札"
    if "落札候補" in r: return "落札候補"
    if "失格" in r: return "失格"
    if "不適当" in r: return "落札不適当"
    if "辞退" in r: return "辞退"
    if "無効" in r: return "無効"
    if "達せず" in r or "超過" in r: return "予定価格超過"
    return ""

def parse_pdf(path):
    txt = subprocess.run(["pdftotext", "-layout", path, "-"], capture_output=True, check=True).stdout.decode("utf-8", "replace")
    lines = txt.replace("\f", "\n").split("\n")
    starts = [i for i, l in enumerate(lines) if HEAD.search(l)]
    raw = []
    for k, st in enumerate(starts):
        block = lines[st + 1: starts[k + 1] if k + 1 < len(starts) else len(lines)]
        cut = next((i for i, l in enumerate(block) if KEIKA.search(l)), len(block))
        t = parse_head(block[:cut])
        t["bids"], t["note"] = parse_bids(block[cut + 1:])
        evs = next((i for i, l in enumerate(block) if "総合評価落札方式に関する評価調書" in l), None)
        ev = parse_eval(block[evs + 1:]) if evs is not None else {}
        for b in t["bids"]:
            k = (core.nkey(b["company"]), b["amounts"][-1] if b["amounts"] else None)
            if k in ev:
                b["eval"] = ev[k]
        prev = raw[-1] if raw else None
        if (prev and t["number"] and prev["number"] == t["number"] and prev["n_declared"]
                and len(prev["bids"]) < prev["n_declared"]):        # 入札者が多くて次ページに続いた調書を結合
            prev["bids"] += t["bids"]; prev["note"] = (prev["note"] + " " + t["note"]).strip(); continue
        raw.append(t)
    out = []
    for k, t in enumerate(raw):
        bids, note = t["bids"], t.pop("note")
        for b in bids:
            b["final"] = b["amounts"][-1] if b["amounts"] else None
            b["result"] = classify(b)
            if "eval" in b:
                if b["eval"] is None:
                    over = b["final"] and t["yotei"] and b["final"] > t["yotei"]
                    if not b["result"]: b["result"] = "予定価格超過" if over else "落札不適当"
                    b["remark"] = (b["remark"] + " 評価調書で評価対象外" + ("" if over else "（数値的判断基準を下回る入札等）")).strip()
                else:
                    b["remark"] = (b["remark"] + " 総合評価点%s・順位%s" % b["eval"]).strip()
            if not b["result"] and b["final"] and t["yotei"] and b["final"] > t["yotei"]:
                b["result"] = "予定価格超過"
        winners = [b for b in bids if b["result"] == "落札"]
        if len(bids) == 1 and not winners and "随意契約" in t["method"] and bids[0]["final"]:
            bids[0]["result"] = "落札"; winners = bids[:1]
        warn, flat = [], note.replace(" ", "")
        if "中止" in flat: status = "中止"
        elif re.search(r"取止め|取りやめ|取り止め", flat): status = "取止め"
        elif "不調" in flat: status = "不調"
        elif winners: status = "落札"
        elif any(b["result"] == "落札候補" for b in bids): status = "落札候補（審査中）"
        elif bids: status = "落札者なし"; warn.append("落札者を特定できず")
        else: status = "不明"; warn.append("本文を読み取れず")
        if len(winners) > 1: warn.append("落札者が複数")
        if t["n_declared"] is not None and bids and t["n_declared"] != len(bids):
            warn.append("業者数%d≠読取%d" % (t["n_declared"], len(bids)))
        if not t["number"]: warn.append("番号なし")
        if not t["title"]: warn.append("件名なし")
        w = winners[0] if len(winners) == 1 and status == "落札" else None
        t.update(seq=k + 1, status=status, n_parsed=len(bids),
                 winner=w["company"] if w else None, win_amount=w["final"] if w else None,
                 lottery=1 if w and "くじ" in w["remark"] else 0)
        t["rate"] = round(w["final"] / t["yotei"] * 100, 2) if w and w["final"] and t["yotei"] else None
        if t["rate"] and not (30 <= t["rate"] <= 100.5): warn.append("落札率が範囲外")
        t["warn"] = "; ".join(warn)
        out.append(t)
    return out


def run(con, run_id, opts):
    s = Session("utf-8")
    as_of = opts.get("as_of")
    pdfdir = os.path.join(core.DATA, "pdf", NAME); os.makedirs(pdfdir, exist_ok=True)
    new_docs, changed, new_t, upd_t, new_n, gone_n = [], [], [], [], [], []
    # 1) 入札結果: 年度ページ → PDF
    listing = []
    for yu, yt in [(u, t) for u, t in links(s.get(RESULT_INDEX)) if re.search(r"/2/\d+\.html$", u) and "入札結果" in t]:
        for u, t in links(s.get(yu)):
            if u.lower().endswith(".pdf") and re.search(r"入札結果|中止|取止め", t):
                d = core.wareki(t)
                size = (re.search(r"PDFファイル:\s*([\d.]+\s*[KM]B)", t) or [None, ""])[1]
                if d and not (as_of and d > as_of): listing.append((u, re.sub(r"\s*\(PDF.*", "", t), d, size))
    listing.sort(key=lambda x: x[2])
    known = dict(con.execute("SELECT url,size_label FROM documents WHERE src=?", (NAME,)).fetchall())
    for u, label, d, size in listing:
        is_new = u not in known
        if not is_new and known[u] == size: continue           # 既知で容量表示も同じ → 取りに行かない
        path = os.path.join(pdfdir, os.path.basename(u))
        b = open(path, "rb").read() if (opts.get("use_cache") and is_new and os.path.exists(path)) else s.get(u, binary=True)
        sha = hashlib.sha256(b).hexdigest()
        if not is_new and con.execute("SELECT sha256 FROM documents WHERE src=? AND url=?", (NAME, u)).fetchone()[0] == sha:
            con.execute("UPDATE documents SET size_label=? WHERE src=? AND url=?", (size, NAME, u)); continue
        open(path, "wb").write(b)
        tenders = parse_pdf(path)
        if is_new:
            con.execute("INSERT INTO documents VALUES(?,?,?,?,?,?,?,?,?,?)",
                        (NAME, u, label, d, core.fiscal_year(d), size, sha, run_id, None, len(tenders)))
            new_docs.append(u)
        else:                                                   # 差し替え: その原本の案件を入れ直す
            for (tid,) in con.execute("SELECT id FROM tenders WHERE src=? AND doc_url=?", (NAME, u)).fetchall():
                con.execute("DELETE FROM bids WHERE tender_id=?", (tid,))
            con.execute("DELETE FROM tenders WHERE src=? AND doc_url=?", (NAME, u))
            con.execute("UPDATE documents SET size_label=?,sha256=?,last_changed_run=?,n_tenders=? WHERE src=? AND url=?",
                        (size, sha, run_id, len(tenders), NAME, u))
            changed.append(u)
        for t in tenders:
            t.update(ext_id="%s#%d" % (os.path.basename(u), t["seq"]), doc_url=u, bid_date=t["bid_date"] or d, sig=sha, org="財政課契約担当")
            for b in t["bids"]: b.setdefault("final", b["amounts"][-1] if b["amounts"] else None)
            (new_t if is_new else upd_t).append(t["ext_id"])
            core.upsert(con, NAME, run_id, t)
        con.commit()
        print("  %s 取込: %s → %d件" % (LABEL, label, len(tenders)), file=sys.stderr)
    # 2) 入札公告
    cur = [(u, t) for u, t in links(s.get(NOTICE_INDEX)) if re.search(r"/6/\d+\.html$", u) and "公告" in t]
    if as_of: cur = [n for n in cur if (core.wareki(n[1]) or "") <= as_of]
    known_n = {r[0] for r in con.execute("SELECT url FROM notices WHERE src=?", (NAME,))}
    for u, t in cur:
        if u not in known_n:
            con.execute("INSERT INTO notices VALUES(?,?,?,?,?,?,NULL)", (NAME, u, t, core.wareki(t), run_id, run_id)); new_n.append(u)
        else:
            con.execute("UPDATE notices SET last_seen_run=?,gone_run=NULL,title=? WHERE src=? AND url=?", (run_id, t, NAME, u))
    live = {u for u, _ in cur}
    for (u,) in con.execute("SELECT url FROM notices WHERE src=? AND gone_run IS NULL", (NAME,)).fetchall():
        if u not in live:
            con.execute("UPDATE notices SET gone_run=? WHERE src=? AND url=?", (run_id, NAME, u)); gone_n.append(u)
    con.commit()
    return dict(new_tenders=new_t, updated_tenders=upd_t, new_notices=new_n, gone_notices=gone_n, changed_docs=changed)
