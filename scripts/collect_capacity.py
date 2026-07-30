#!/usr/bin/env python3
"""One-time collector for shelter capacity shown in Kumamoto portal map popups."""
from __future__ import annotations

import argparse, asyncio, csv, hashlib, json, re, unicodedata
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

try:
    from scripts.capacity_matcher import CAPACITY_INPUT_COLUMNS, capacity_match_key, clean_text
except ModuleNotFoundError:
    from capacity_matcher import CAPACITY_INPUT_COLUMNS, capacity_match_key, clean_text

JST = ZoneInfo("Asia/Tokyo")
BASE = "https://portal.bousai.pref.kumamoto.jp/sp.html"
MUNICIPALITY_TEXT = """
431001 熊本市
432024 八代市
432032 人吉市
432041 荒尾市
432059 水俣市
432067 玉名市
432083 山鹿市
432105 菊池市
432113 宇土市
432121 上天草市
432130 宇城市
432148 阿蘇市
432156 天草市
432164 合志市
433489 美里町
433641 玉東町
433675 南関町
433683 長洲町
433691 和水町
434035 大津町
434043 菊陽町
434230 南小国町
434248 小国町
434256 産山村
434281 高森町
434329 西原村
434337 南阿蘇村
434418 御船町
434426 嘉島町
434434 益城町
434442 甲佐町
434477 山都町
434680 氷川町
434825 芦北町
434841 津奈木町
435015 錦町
435058 多良木町
435066 湯前町
435074 水上村
435104 相良村
435112 五木村
435121 山江村
435139 球磨村
435147 あさぎり町
435317 苓北町
"""
MUNICIPALITIES = dict(line.split(maxsplit=1) for line in MUNICIPALITY_TEXT.strip().splitlines())
CAP_KEYS = ("定員", "収容定員", "収容可能人数", "収容人数", "capacity", "maxcapacity", "teiin")
NAME_KEYS = ("避難所名", "施設名", "名称", "name", "sheltername", "facilityname")
ADDR_KEYS = ("住所", "所在地", "address")
MUNI_KEYS = ("市町村", "自治体", "municipality", "cityname")
ID_KEYS = ("避難所id", "施設id", "id", "shelterid", "facilityid", "objectid")
LAT_KEYS = ("緯度", "lat", "latitude", "y")
LON_KEYS = ("経度", "lon", "lng", "longitude", "x")
CAP_RE = re.compile(r"(?:収容可能人数|収容定員|収容人数|定員)\s*[:：]?\s*([0-9０-９][0-9０-９,，]*)\s*人?")


def nkey(v: object) -> str:
    return re.sub(r"[^0-9a-zぁ-んァ-ヶ一-龠]", "", unicodedata.normalize("NFKC", str(v)).casefold())


def pick(obj: dict[str, Any], aliases: tuple[str, ...]) -> Any:
    values = {nkey(k): v for k, v in obj.items()}
    return next((values[nkey(a)] for a in aliases if nkey(a) in values and values[nkey(a)] not in (None, "")), "")


def parse_capacity(value: object) -> tuple[str, str, str]:
    raw = clean_text(value)
    if not raw or raw in {"---", "-", "不明", "未入力", "なし"}: return "", raw, "missing"
    m = re.search(r"[0-9０-９][0-9０-９,，]*", raw)
    if not m: return "", raw, "invalid"
    try: return str(int(unicodedata.normalize("NFKC", m.group()).replace(",", ""))), raw, "parsed"
    except ValueError: return "", raw, "invalid"


def stable_id(muni: str, name: str, address: str, lat: str = "", lon: str = "") -> str:
    value = "|".join(clean_text(v).casefold() for v in (muni, name, address, lat, lon))
    return "derived:" + hashlib.sha256(value.encode()).hexdigest()[:24]


def url_for(code: str) -> str:
    return BASE + "?" + urlencode({"p": "evacuation/shelter", "l": "15-1", "ll": "32.6382,130.7761", "z": "9", "municipalityCd": code})


def make_row(obj: dict[str, Any], code: str, muni: str, acquired: str, source: str) -> dict[str, str] | None:
    cap, name = pick(obj, CAP_KEYS), clean_text(pick(obj, NAME_KEYS))
    if cap in (None, "") or not name: return None
    muni = clean_text(pick(obj, MUNI_KEYS)) or muni
    address, lat, lon = clean_text(pick(obj, ADDR_KEYS)), clean_text(pick(obj, LAT_KEYS)), clean_text(pick(obj, LON_KEYS))
    persons, raw, status = parse_capacity(cap)
    pid = clean_text(pick(obj, ID_KEYS)) or stable_id(muni, name, address, lat, lon)
    return {"portal_shelter_id": pid, "municipality_code": code, "municipality": muni,
            "shelter_name": name, "address": address, "portal_latitude": lat, "portal_longitude": lon,
            "portal_capacity_persons": persons, "portal_capacity_raw": raw,
            "capacity_source": "kumamoto_portal_map", "capacity_acquired_at_jst": acquired,
            "capacity_match_key": capacity_match_key(muni, name, address), "capacity_parse_status": status,
            "source_url": source}


def walk(value: Any, code: str, muni: str, acquired: str, source: str) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    if isinstance(value, dict):
        row = make_row(value, code, muni, acquired, source)
        if row: out.append(row)
        for child in value.values(): out.extend(walk(child, code, muni, acquired, source))
    elif isinstance(value, list):
        for child in value: out.extend(walk(child, code, muni, acquired, source))
    return out


def read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists(): return []
    with path.open("r", encoding="utf-8-sig", newline="") as f: return list(csv.DictReader(f))


def write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=CAPACITY_INPUT_COLUMNS, extrasaction="ignore"); w.writeheader()
        w.writerows({c: r.get(c, "") for c in CAPACITY_INPUT_COLUMNS} for r in rows)


def merge(existing: list[dict[str, str]], new: list[dict[str, str]]) -> list[dict[str, str]]:
    out: dict[str, dict[str, str]] = {}
    for raw in existing + new:
        row = {c: clean_text(raw.get(c, "")) for c in CAPACITY_INPUT_COLUMNS}
        row["capacity_match_key"] = row["capacity_match_key"] or capacity_match_key(row["municipality"], row["shelter_name"], row["address"])
        row["portal_shelter_id"] = row["portal_shelter_id"] or stable_id(row["municipality"], row["shelter_name"], row["address"])
        key = row["portal_shelter_id"] or row["capacity_match_key"]
        old = out.get(key)
        if old is None or row["capacity_parse_status"] == "parsed" or old["capacity_parse_status"] != "parsed": out[key] = row
    return sorted(out.values(), key=lambda r: (r["municipality"], r["shelter_name"], r["address"]))


async def click_all(page: Any) -> None:
    loc = page.get_by_text("全ての避難所", exact=True)
    for i in range(await loc.count()):
        try:
            if await loc.nth(i).is_visible(): await loc.nth(i).click(force=True); await page.wait_for_timeout(900); return
        except Exception: pass


async def popup_rows(page: Any, code: str, muni: str, acquired: str) -> list[dict[str, str]]:
    """Scroll the virtual dgrid, click each row once, and parse popup text."""
    return await page.evaluate(r"""
    async ({code, muni, acquired}) => {
      const norm=s=>(s||'').replace(/\u00a0/g,' ').replace(/\s+/g,' ').trim(), sleep=ms=>new Promise(r=>setTimeout(r,ms));
      const hrow=Array.from(document.querySelectorAll('tr')).find(tr=>Array.from(tr.children).some(c=>c.tagName==='TH'&&norm(c.textContent).includes('避難所名')));
      if(!hrow) return [];
      const headers=Array.from(hrow.children).filter(c=>c.tagName==='TH').map(c=>norm(c.textContent));
      const hi=s=>Math.max(0,headers.findIndex(h=>h.includes(s))), ni=hi('避難所名'), ai=hi('住所'), mi=hi('市町村');
      const hc=hrow.closest('.dgrid-header'), root=(hc&&hc.parentElement)||hrow.closest('[role=grid]')||hrow.closest('.dgrid'); if(!root)return [];
      const sc=Array.from(root.querySelectorAll('.dgrid-scroller')).sort((a,b)=>(b.scrollHeight-b.clientHeight)-(a.scrollHeight-a.clientHeight))[0];
      const seen=new Set(), out=[];
      const visible=el=>{const s=getComputedStyle(el),r=el.getBoundingClientRect();return s.display!=='none'&&s.visibility!=='hidden'&&r.width>0&&r.height>0};
      const popupText=()=>Array.from(document.querySelectorAll('.dijitPopup,.dijitDialog,.leaflet-popup,.olPopup,.map-popup,.popup,.infowindow,[role=dialog],body *'))
        .filter(el=>visible(el)&&(el.innerText||'').includes('定員')).map(el=>norm(el.innerText)).sort((a,b)=>a.length-b.length)[0]||'';
      if(sc){sc.scrollTop=0;sc.dispatchEvent(new Event('scroll',{bubbles:true}));await sleep(500)}
      for(let step=0;step<700;step++){
        const nodes=Array.from(root.querySelectorAll('.dgrid-row'));
        let added=0;
        for(const node of nodes){
          const tr=node.tagName==='TR'?node:node.querySelector('tr'); if(!tr)continue;
          const cells=Array.from(tr.children).filter(c=>c.tagName==='TD').map(c=>norm(c.textContent));
          const id=node.getAttribute('data-id')||node.id||tr.getAttribute('data-id')||tr.id||JSON.stringify(cells);
          if(seen.has(id)||!cells[ni])continue; seen.add(id); added++;
          node.dispatchEvent(new MouseEvent('click',{bubbles:true,cancelable:true,view:window})); await sleep(180);
          const text=popupText(), m=text.match(/(?:収容可能人数|収容定員|収容人数|定員)\s*[:：]?\s*([0-9０-９][0-9０-９,，]*)\s*人?/);
          if(m) out.push({portal_shelter_id:id,municipality_code:code,municipality:cells[mi]||muni,shelter_name:cells[ni],address:cells[ai]||'',portal_latitude:'',portal_longitude:'',portal_capacity_raw:m[1],capacity_source:'kumamoto_portal_map',capacity_acquired_at_jst:acquired,source_url:location.href});
        }
        if(!sc)break; const max=Math.max(0,sc.scrollHeight-sc.clientHeight), bottom=sc.scrollTop>=max-2;
        if(bottom&&added===0)break; sc.scrollTop=bottom?max:Math.min(max,sc.scrollTop+Math.max(280,Math.floor(sc.clientHeight*.72)));sc.dispatchEvent(new Event('scroll',{bubbles:true}));await sleep(250);
      }
      return out;
    }""", {"code": code, "muni": muni, "acquired": acquired})


async def collect(args: argparse.Namespace) -> list[dict[str, str]]:
    from playwright.async_api import async_playwright
    acquired = datetime.now(JST).isoformat(timespec="seconds")
    debug = Path(args.debug_dir); payload_dir = debug / "payloads"; payload_dir.mkdir(parents=True, exist_ok=True)
    collected: list[dict[str, str]] = []; context_info = {"code":"", "muni":""}; tasks: set[asyncio.Task[Any]] = set(); payload_no = 0
    async with async_playwright() as pw:
      browser = await pw.chromium.launch(headless=True)
      ctx = await browser.new_context(locale="ja-JP", timezone_id="Asia/Tokyo", viewport={"width":1440,"height":1200})
      page = await ctx.new_page(); page.set_default_timeout(args.timeout_ms)
      async def capture(resp: Any) -> None:
        nonlocal payload_no
        try:
          if resp.request.resource_type not in {"xhr","fetch"} and "json" not in (resp.headers.get("content-type") or "").lower(): return
          body = await resp.body()
          if not body or len(body)>15_000_000:return
          text=body.decode("utf-8",errors="replace")
          if not any(t in text for t in ("定員","収容人数","収容定員")) and "capacity" not in text.lower():return
          try:data=json.loads(text)
          except json.JSONDecodeError:return
          if payload_no<args.max_debug_payloads: payload_no+=1;(payload_dir/f"payload_{payload_no:03d}.json").write_text(text,encoding="utf-8")
          collected.extend(walk(data,context_info["code"],context_info["muni"],acquired,resp.url))
        except Exception as exc: print(f"WARNING response capture: {exc}")
      def listener(resp: Any) -> None:
        task=asyncio.create_task(capture(resp));tasks.add(task);task.add_done_callback(tasks.discard)
      page.on("response",listener)
      codes=args.municipality_code or list(MUNICIPALITIES)
      if args.max_municipalities:codes=codes[:args.max_municipalities]
      for i,code in enumerate(codes,1):
        muni=MUNICIPALITIES.get(code,code);context_info.update(code=code,muni=muni);print(f"[{i}/{len(codes)}] {muni}")
        await page.goto(url_for(code),wait_until="domcontentloaded",timeout=args.timeout_ms);await page.wait_for_timeout(1800);await click_all(page);await page.wait_for_timeout(1500)
        if tasks:await asyncio.gather(*list(tasks),return_exceptions=True)
        if not args.network_only:
          dom=await popup_rows(page,code,muni,acquired)
          for row in dom:
            persons,raw,status=parse_capacity(row.get("portal_capacity_raw",""));row["portal_capacity_persons"]=persons;row["portal_capacity_raw"]=raw;row["capacity_parse_status"]=status;row["capacity_match_key"]=capacity_match_key(row["municipality"],row["shelter_name"],row["address"])
          collected.extend(dom);print(f"  popup rows={len(dom)}")
      if tasks:await asyncio.gather(*list(tasks),return_exceptions=True)
      await page.screenshot(path=str(debug/"last_page.png"),full_page=True);(debug/"last_page.html").write_text(await page.content(),encoding="utf-8")
      await ctx.close();await browser.close()
    return collected


def main() -> int:
    p=argparse.ArgumentParser();p.add_argument("--output",default="reference/portal_shelter_capacity.csv");p.add_argument("--history-dir",default="reference/capacity_history");p.add_argument("--debug-dir",default="debug/capacity");p.add_argument("--municipality-code",action="append",default=[]);p.add_argument("--max-municipalities",type=int,default=0);p.add_argument("--timeout-ms",type=int,default=90000);p.add_argument("--minimum-parsed",type=int,default=1);p.add_argument("--max-debug-payloads",type=int,default=30);p.add_argument("--network-only",action="store_true");args=p.parse_args()
    output=Path(args.output);existing=read_csv(output);new=asyncio.run(collect(args));rows=merge(existing,new);parsed=sum(r.get("capacity_parse_status")=="parsed" for r in rows)
    if parsed<args.minimum_parsed:raise RuntimeError(f"定員取得件数が下限未満です: {parsed} < {args.minimum_parsed}")
    write_csv(output,rows);history=Path(args.history_dir)/f"{datetime.now(JST).date().isoformat()}.csv";write_csv(history,rows)
    print(json.dumps({"output":str(output),"history":str(history),"existing":len(existing),"collected":len(new),"merged":len(rows),"parsed":parsed},ensure_ascii=False,indent=2));return 0

if __name__=="__main__":raise SystemExit(main())
