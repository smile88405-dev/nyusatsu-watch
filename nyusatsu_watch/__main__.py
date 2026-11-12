# -*- coding: utf-8 -*-
"""
入札ウォッチ

  python -m nyusatsu_watch run                    # 全自治体: 収集 → 解析 → 差分レポート → 出力
  python -m nyusatsu_watch run --src osaki        # 自治体を指定（カンマ区切り: osaki,sendai,miyagi）
  python -m nyusatsu_watch run --years 2025,2026  # 年度を指定（既定は今年度。大崎市は常に掲載中の全年度）
  python -m nyusatsu_watch run --offline --years 2026   # 読み取り方を直したあと、保存済みの原本から作り直す
  python -m nyusatsu_watch export                 # DBから out/ と docs/ を再生成
  python -m nyusatsu_watch company 古川土地        # 業者名で入札履歴を表示
"""
import argparse, datetime as dt, importlib, sys, traceback
from . import core

SOURCE_NAMES = ["osaki", "sendai", "miyagi"]


def load_sources():
    out = {}
    for n in SOURCE_NAMES:
        try: out[n] = importlib.import_module("nyusatsu_watch.sources." + n)
        except ModuleNotFoundError: pass
    return out


def cmd_run(a):
    S = load_sources()
    names = [n for n in (a.src.split(",") if a.src else S) if n in S]
    con = core.connect()
    cur = con.execute("INSERT INTO runs(started,note,srcs) VALUES(?,?,?)",
                      (dt.datetime.now().isoformat(timespec="seconds"), a.note or "", ",".join(names)))
    run_id = cur.lastrowid; con.commit()
    opts = dict(years=[int(y) for y in a.years.split(",")] if a.years else None, as_of=a.as_of, use_cache=a.use_cache, limit=a.limit, offline=a.offline,
                shard=tuple(int(x) for x in a.shard.split("/")) if a.shard else None)
    results, first = {}, {}
    for n in names:
        first[n] = con.execute("SELECT COUNT(*) FROM runs WHERE id<? AND ','||srcs||',' LIKE ?", (run_id, "%," + n + ",%")).fetchone()[0] == 0
        try:
            results[n] = S[n].run(con, run_id, opts)
        except Exception as e:                       # 1自治体の失敗で全体を止めない
            traceback.print_exc(); con.rollback(); results[n] = dict(error=repr(e))
        con.commit()
        print("%s: %s" % (S[n].LABEL, {k: (len(v) if isinstance(v, list) else v) for k, v in results[n].items()}))
    print("差分レポート:", core.diff_report(con, run_id, results, first, S))
    core.export(con, S)


def cmd_company(a):
    con = core.connect(); S = load_sources()
    for r in con.execute("SELECT t.src,t.bid_date,t.title,b.company,b.final_amount,b.result,b.remark,t.yotei,t.winner FROM bids b "
                         "JOIN tenders t ON t.id=b.tender_id WHERE b.company_key LIKE ? ORDER BY t.bid_date", ("%" + core.nkey(a.name) + "%",)):
        print("\t".join([S[r[0]].LABEL] + [str(x) if x is not None else "" for x in r[1:]]))


def main():
    ap = argparse.ArgumentParser(prog="nyusatsu_watch", description="入札ウォッチ")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="収集して差分を出す")
    r.add_argument("--src"); r.add_argument("--years"); r.add_argument("--note")
    r.add_argument("--as-of", help="この日付より後のものを取り込まない（差分検知のデモ・検証用）")
    r.add_argument("--use-cache", action="store_true", help="保存済みの原本があれば再取得しない（DB作り直し用）")
    r.add_argument("--limit", type=int, help="詳細の取得件数の上限（動作確認用）")
    r.add_argument("--offline", action="store_true", help="仙台市・宮城県を保存済みの詳細HTMLだけで作り直す（通信しない）")
    r.add_argument("--shard", help="k/N: 一覧のうち k番目/N だけ取得（初回の大量取得を分担するとき）")
    r.set_defaults(f=cmd_run)
    e = sub.add_parser("export", help="DBから出力を作り直す"); e.set_defaults(f=lambda a: core.export(core.connect(), load_sources()))
    c = sub.add_parser("company", help="業者の履歴"); c.add_argument("name"); c.set_defaults(f=cmd_company)
    a = ap.parse_args(); a.f(a)


if __name__ == "__main__":
    main()
