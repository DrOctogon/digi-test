#!/usr/bin/env python3
"""Render a gorgeous self-contained HTML dashboard from the latest snapshot.

  python dashboard.py            # latest data/excelta_*.csv
  python dashboard.py --date 2026-06-30

Output: data/dashboard_<date>.html  (open in any browser; ECharts via CDN).
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import analysis as A
import config
import timeseries as TS

VENDOR_ECHARTS = config.ROOT / "vendor" / "echarts.min.js"
_CDN_ECHARTS = "https://cdn.jsdelivr.net/npm/echarts@5.5.0/dist/echarts.min.js"

_PRICE_BINS = [(0, 10), (10, 25), (25, 50), (50, 100), (100, 250),
               (250, 500), (500, 1000), (1000, float("inf"))]
_BIN_LABELS = ["<$10", "$10-25", "$25-50", "$50-100", "$100-250",
               "$250-500", "$500-1k", "$1k+"]


def _price_histogram(rows: list[dict]) -> list[int]:
    counts = [0] * len(_PRICE_BINS)
    for r in rows:
        p = A.num(r.get("unit_price")) or 0
        if p <= 0:
            continue
        for i, (lo, hi) in enumerate(_PRICE_BINS):
            if lo <= p < hi:
                counts[i] += 1
                break
    return counts


def _downsample(seq: list, target: int = 300) -> list:
    if len(seq) <= target:
        return seq
    step = len(seq) / target
    return [seq[int(i * step)] for i in range(target)]


def build_trends(data_dir: Path = config.DATA_DIR) -> dict | None:
    """WS6 viz payload — None until >=2 snapshots exist (auto-activates)."""
    files = TS.snapshot_files(data_dir)
    if len(files) < 2:
        return None
    # Per-pair series across all history (for sparkline trends).
    series = {"dates": [], "demand": [], "stockouts": [], "restocks": [], "price_moves": []}
    prev = None
    latest = None
    for f in files:
        cur = TS.load_index(f)
        if prev is not None:
            d = TS.diff_pair(prev, cur)
            series["dates"].append(f.stem.replace("excelta_", ""))
            series["demand"].append(d["demand_units_proxy"])
            series["stockouts"].append(d["stockout_events"])
            series["restocks"].append(d["restock_events"])
            series["price_moves"].append(d["price_moves"])
            latest = d
        prev = cur
    cum = TS.cumulative_events(files)
    movers = sorted([c for c in latest["changes"] if c["units_moved_proxy"] > 0],
                    key=lambda c: c["units_moved_proxy"], reverse=True)[:8]
    return {
        "prev_date": files[-2].stem.replace("excelta_", ""),
        "cur_date": files[-1].stem.replace("excelta_", ""),
        "latest": {"demand": latest["demand_units_proxy"], "stockouts": latest["stockout_events"],
                   "restocks": latest["restock_events"], "price_moves": latest["price_moves"],
                   "new": len(latest["new_skus"]), "discontinued": len(latest["discontinued_skus"])},
        "cumulative": cum,
        "series": series,
        "top_movers": [{"dk": c["digikey_part_number"], "cat": c["category_name"],
                        "prev": c["qty_prev"], "cur": c["qty_cur"], "units": c["units_moved_proxy"]}
                       for c in movers],
    }


def build_payload(rows: list[dict], res: dict, date: str,
                  data_dir: Path = config.DATA_DIR) -> dict:
    abc, stock, price, assort, fp = (res["abc"], res["stock"], res["price"],
                                     res["assortment"], res["footprint"])
    A_ = abc["tier_summary"]["A"]
    pareto = _downsample([round(r["cum_value_share"] * 100, 2) for r in abc["rows"]])
    cats = stock["categories"]
    return {
        "date": date,
        "kpis": {
            "total_skus": len(rows),
            "inv_value": abc["total_inventory_value"],
            "stockout_pct": round(stock["overall_stockout_rate"] * 100, 1),
            "units": stock["total_units"],
            "atier_skus": A_["skus"],
            "atier_share": round(A_["value_share"] * 100, 1),
            "atier_sku_pct": round(A_["skus"] / max(abc["valued_sku_count"], 1) * 100, 1),
            "median_price": price["price_median"],
            "quote_only": price["zero_price_count"],
            "categories": fp["categories_covered"],
        },
        "pareto": pareto,
        "categories": [{
            "name": c["category_name"], "parts": c["parts"], "in_stock": c["in_stock"],
            "stockout": round(c["stockout_rate"] * 100, 1), "value": round(c["inventory_value"]),
        } for c in cats],
        "price_hist": {"labels": _BIN_LABELS, "counts": _price_histogram(rows)},
        "assortment": [{"name": k, "value": v} for k, v in assort["buckets"].items() if v > 0],
        "abc_tiers": [{"name": f"{t}-tier", "skus": abc["tier_summary"][t]["skus"],
                       "value": round(abc["tier_summary"][t]["value"])} for t in ("A", "B", "C")],
        "trends": build_trends(data_dir),
    }


TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>Excelta @ DigiKey — Analysis __DATE__</title>
__ECHARTS__
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap" rel="stylesheet">
<style>
:root{
  --bg:#0a0c12; --bg2:#0f1320; --card:rgba(255,255,255,.04); --stroke:rgba(255,255,255,.08);
  --txt:#e8ecf4; --muted:#8b93a7; --red:#ff3b46; --red2:#c00000; --amber:#ffb347;
  --cyan:#35d0ba; --violet:#8b7cff; --grad:linear-gradient(135deg,#ff3b46,#ff7a3c);
}
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:Inter,system-ui,sans-serif;background:
  radial-gradient(1200px 700px at 80% -10%,rgba(255,59,70,.10),transparent 60%),
  radial-gradient(900px 600px at -10% 20%,rgba(139,124,255,.08),transparent 55%),
  var(--bg);color:var(--txt);padding:32px 28px 60px;min-height:100vh}
.wrap{max-width:1320px;margin:0 auto}
header{display:flex;align-items:baseline;justify-content:space-between;flex-wrap:wrap;gap:8px;margin-bottom:6px}
h1{font-size:26px;font-weight:800;letter-spacing:-.02em}
h1 .brand{background:var(--grad);-webkit-background-clip:text;background-clip:text;color:transparent}
.sub{color:var(--muted);font-size:13px}
.tag{font-size:11px;color:var(--muted);border:1px solid var(--stroke);padding:3px 9px;border-radius:999px}
.kpis{display:grid;grid-template-columns:repeat(5,1fr);gap:14px;margin:22px 0}
.kpi{background:var(--card);border:1px solid var(--stroke);border-radius:16px;padding:18px 18px 16px;
  position:relative;overflow:hidden;backdrop-filter:blur(8px)}
.kpi::before{content:"";position:absolute;inset:0 auto auto 0;width:3px;height:100%;background:var(--grad)}
.kpi .l{font-size:11px;text-transform:uppercase;letter-spacing:.08em;color:var(--muted)}
.kpi .v{font-size:28px;font-weight:800;margin-top:8px;letter-spacing:-.02em}
.kpi .d{font-size:12px;color:var(--muted);margin-top:4px}
.grid{display:grid;grid-template-columns:repeat(12,1fr);gap:16px}
.panel{background:var(--card);border:1px solid var(--stroke);border-radius:18px;padding:18px 16px 8px;backdrop-filter:blur(8px)}
.panel h3{font-size:14px;font-weight:600;margin:0 4px 2px;display:flex;justify-content:space-between;align-items:center}
.panel h3 small{color:var(--muted);font-weight:400;font-size:11px}
.c-7{grid-column:span 7}.c-5{grid-column:span 5}.c-6{grid-column:span 6}.c-12{grid-column:span 12}
.chart{width:100%;height:320px}
.chart.tall{height:380px}
footer{color:var(--muted);font-size:12px;margin-top:24px;line-height:1.6}
footer b{color:var(--txt)}
@media(max-width:980px){.kpis{grid-template-columns:repeat(2,1fr)}.c-7,.c-5,.c-6{grid-column:span 12}}
</style>
</head>
<body>
<div class="wrap">
  <header>
    <div>
      <h1><span class="brand">Excelta</span> @ DigiKey — Inventory & Pricing</h1>
      <div class="sub">Baseline analysis · snapshot __DATE__ · single-day (T=0)</div>
    </div>
    <div class="tag">inventory value = list × visible stock (proxy)</div>
  </header>

  <section class="kpis" id="kpis"></section>

  <section class="grid">
    <div class="panel c-7"><h3>Pareto — value concentration <small>cumulative % of inventory value by SKU rank</small></h3><div id="pareto" class="chart tall"></div></div>
    <div class="panel c-5"><h3>ABC tiers <small>share of value</small></h3><div id="abc" class="chart tall"></div></div>
    <div class="panel c-7"><h3>Priority map <small>stockout % × inventory value · bubble = SKU count</small></h3><div id="bubble" class="chart tall"></div></div>
    <div class="panel c-5"><h3>Assortment mix <small>price × stock buckets</small></h3><div id="assort" class="chart tall"></div></div>
    <div class="panel c-7"><h3>Inventory value by category <small>top lines (proxy $)</small></h3><div id="catbar" class="chart"></div></div>
    <div class="panel c-5"><h3>Price distribution <small>priced SKUs only</small></h3><div id="pricehist" class="chart"></div></div>
  </section>

  <section id="trends" style="display:none">
    <h2 style="font-size:18px;font-weight:700;margin:30px 4px 4px">Trends &amp; demand <span style="color:var(--muted);font-weight:400;font-size:13px" id="trends-range"></span></h2>
    <div class="kpis" id="trend-kpis" style="grid-template-columns:repeat(6,1fr)"></div>
    <div class="grid">
      <div class="panel c-7"><h3>Demand proxy &amp; stockouts over time <small>units removed / stockout events per day</small></h3><div id="trendline" class="chart"></div></div>
      <div class="panel c-5"><h3>Top demand-proxy movers <small>latest day</small></h3><div id="movers" class="chart"></div></div>
    </div>
  </section>

  <footer>
    <b>Guardrails:</b> $0 (quote-only) SKUs excluded from price stats · qty=0 counted as a real state, never imputed ·
    inventory value is a list×stock proxy · demand = net units removed (proxy, not confirmed sell-through).
    <b id="t0note">Demand & restock velocity unlock once ≥2 daily snapshots exist.</b>
  </footer>
</div>

<script>
const D = __DATA__;
const C = {red:'#ff3b46',red2:'#c00000',amber:'#ffb347',cyan:'#35d0ba',violet:'#8b7cff',muted:'#8b93a7',grid:'rgba(255,255,255,.06)'};
const base = {textStyle:{fontFamily:'Inter',color:'#cdd3e0'},grid:{left:48,right:18,top:24,bottom:40}};
const fmtUSD = v => '$'+Intl.NumberFormat('en',{notation:'compact',maximumFractionDigits:1}).format(v);
const fmtN = v => Intl.NumberFormat('en').format(v);

// KPIs
const k = D.kpis;
const cards = [
  ['Total SKUs', fmtN(k.total_skus), k.categories+' categories'],
  ['Inventory value', fmtUSD(k.inv_value), 'list × stock (proxy)'],
  ['A-tier concentration', k.atier_share+'%', k.atier_sku_pct+'% of SKUs = '+k.atier_share+'% value'],
  ['Stockout rate', k.stockout_pct+'%', fmtN(k.units)+' units in stock'],
  ['Median price', '$'+k.median_price.toFixed(2), k.quote_only+' quote-only ($0)'],
];
document.getElementById('kpis').innerHTML = cards.map(c=>
  `<div class="kpi"><div class="l">${c[0]}</div><div class="v">${c[1]}</div><div class="d">${c[2]}</div></div>`).join('');

const mk = id => echarts.init(document.getElementById(id),null,{renderer:'canvas'});
const axisLine = {lineStyle:{color:'rgba(255,255,255,.15)'}};
const splitLine = {lineStyle:{color:C.grid}};

// Pareto
mk('pareto').setOption({...base,
  tooltip:{trigger:'axis',valueFormatter:v=>v+'%'},
  xAxis:{type:'category',data:D.pareto.map((_,i)=>i+1),name:'SKU rank',nameLocation:'middle',nameGap:26,
    axisLine,axisLabel:{color:C.muted,showMaxLabel:true}},
  yAxis:{type:'value',max:100,axisLabel:{formatter:'{value}%',color:C.muted},splitLine},
  series:[{type:'line',data:D.pareto,smooth:true,showSymbol:false,
    lineStyle:{width:3,color:C.red},
    areaStyle:{color:new echarts.graphic.LinearGradient(0,0,0,1,[{offset:0,color:'rgba(255,59,70,.45)'},{offset:1,color:'rgba(255,59,70,0)'}])},
    markLine:{symbol:'none',data:[{yAxis:80,lineStyle:{color:C.amber,type:'dashed'},label:{formatter:'80% value',color:C.amber}}]}}]
});

// ABC tiers donut
mk('abc').setOption({...base,tooltip:{trigger:'item',valueFormatter:fmtUSD},
  legend:{bottom:0,textStyle:{color:C.muted}},
  series:[{type:'pie',radius:['45%','72%'],center:['50%','46%'],
    itemStyle:{borderColor:'#0a0c12',borderWidth:2},
    label:{color:'#e8ecf4',formatter:'{b}\n{d}%'},
    data:D.abc_tiers.map((t,i)=>({name:t.name,value:t.value,itemStyle:{color:[C.red,C.amber,C.violet][i]}}))}]
});

// Priority bubble: x=stockout%, y=value, size=parts
const maxParts = Math.max(...D.categories.map(c=>c.parts));
mk('bubble').setOption({...base,grid:{left:64,right:24,top:24,bottom:46},
  tooltip:{trigger:'item',formatter:p=>`<b>${p.data[3]}</b><br/>stockout ${p.data[0]}%<br/>value ${fmtUSD(p.data[1])}<br/>${p.data[2]} SKUs`},
  xAxis:{type:'value',name:'stockout %',nameLocation:'middle',nameGap:28,max:100,axisLine,axisLabel:{formatter:'{value}%',color:C.muted},splitLine},
  yAxis:{type:'value',name:'inventory value',nameGap:10,axisLabel:{formatter:fmtUSD,color:C.muted},splitLine},
  series:[{type:'scatter',
    data:D.categories.map(c=>[c.stockout,c.value,c.parts,c.name]),
    symbolSize:d=>12+44*Math.sqrt(d[2]/maxParts),
    itemStyle:{color:new echarts.graphic.LinearGradient(0,0,1,1,[{offset:0,color:'rgba(255,123,60,.9)'},{offset:1,color:'rgba(255,59,70,.7)'}]),
      borderColor:'rgba(255,255,255,.25)',shadowBlur:14,shadowColor:'rgba(255,59,70,.35)'},
    label:{show:true,formatter:p=>p.data[2]>=80?p.data[3]:'',position:'top',color:C.muted,fontSize:10}}]
});

// Assortment donut
mk('assort').setOption({...base,tooltip:{trigger:'item',valueFormatter:fmtN},
  legend:{bottom:0,textStyle:{color:C.muted},type:'scroll'},
  series:[{type:'pie',roseType:'radius',radius:['30%','70%'],center:['50%','45%'],
    itemStyle:{borderColor:'#0a0c12',borderWidth:2},label:{color:C.muted,fontSize:11},
    data:D.assortment.map((b,i)=>({name:b.name.replace(/_/g,' '),value:b.value,
      itemStyle:{color:[C.red,C.amber,C.cyan,C.violet,'#5b6478'][i%5]}}))}]
});

// Category value bar (top 12, horizontal)
const cats = [...D.categories].sort((a,b)=>b.value-a.value).slice(0,12).reverse();
mk('catbar').setOption({...base,grid:{left:140,right:30,top:10,bottom:30},
  tooltip:{trigger:'axis',valueFormatter:fmtUSD},
  xAxis:{type:'value',axisLabel:{formatter:fmtUSD,color:C.muted},splitLine},
  yAxis:{type:'category',data:cats.map(c=>c.name),axisLabel:{color:'#cdd3e0',width:130,overflow:'truncate'},axisLine},
  series:[{type:'bar',data:cats.map(c=>c.value),barWidth:'62%',
    itemStyle:{borderRadius:[0,6,6,0],color:new echarts.graphic.LinearGradient(0,0,1,0,[{offset:0,color:'#c00000'},{offset:1,color:'#ff7a3c'}])}}]
});

// Price histogram
mk('pricehist').setOption({...base,grid:{left:46,right:18,top:14,bottom:40},
  tooltip:{trigger:'axis',valueFormatter:fmtN},
  xAxis:{type:'category',data:D.price_hist.labels,axisLabel:{color:C.muted,rotate:30,fontSize:10},axisLine},
  yAxis:{type:'value',axisLabel:{color:C.muted},splitLine},
  series:[{type:'bar',data:D.price_hist.counts,barWidth:'58%',
    itemStyle:{borderRadius:[6,6,0,0],color:new echarts.graphic.LinearGradient(0,0,0,1,[{offset:0,color:C.cyan},{offset:1,color:'rgba(53,208,186,.25)'}])}}]
});

// Trends (WS6) — only when >=2 snapshots produced a trends payload
if (D.trends){
  const T = D.trends;
  document.getElementById('trends').style.display='block';
  document.getElementById('t0note').style.display='none';
  document.getElementById('trends-range').textContent = '· '+T.prev_date+' → '+T.cur_date;
  const tc = [
    ['Demand proxy', fmtN(T.latest.demand), 'units removed'],
    ['Stockouts', T.latest.stockouts, '+→0 events'],
    ['Restocks', T.latest.restocks, '0→+ events'],
    ['Price changes', T.latest.price_moves, 'day-over-day'],
    ['New SKUs', T.latest.new, 'added'],
    ['Discontinued', T.latest.discontinued, 'removed'],
  ];
  document.getElementById('trend-kpis').innerHTML = tc.map(c=>
    `<div class="kpi"><div class="l">${c[0]}</div><div class="v">${c[1]}</div><div class="d">${c[2]}</div></div>`).join('');

  mk('trendline').setOption({...base,grid:{left:48,right:48,top:30,bottom:40},
    tooltip:{trigger:'axis'},legend:{top:0,textStyle:{color:C.muted},data:['demand proxy','stockouts']},
    xAxis:{type:'category',data:T.series.dates,axisLine,axisLabel:{color:C.muted}},
    yAxis:[{type:'value',name:'units',axisLabel:{color:C.muted},splitLine},
           {type:'value',name:'events',axisLabel:{color:C.muted},splitLine:{show:false}}],
    series:[
      {name:'demand proxy',type:'line',smooth:true,showSymbol:true,data:T.series.demand,
       lineStyle:{width:3,color:C.cyan},itemStyle:{color:C.cyan},
       areaStyle:{color:new echarts.graphic.LinearGradient(0,0,0,1,[{offset:0,color:'rgba(53,208,186,.35)'},{offset:1,color:'rgba(53,208,186,0)'}])}},
      {name:'stockouts',type:'bar',yAxisIndex:1,data:T.series.stockouts,barWidth:'40%',
       itemStyle:{color:'rgba(255,59,70,.55)',borderRadius:[4,4,0,0]}}]
  });

  const mv = [...T.top_movers].reverse();
  mk('movers').setOption({...base,grid:{left:120,right:30,top:10,bottom:30},
    tooltip:{trigger:'axis',formatter:p=>{const d=mv[p[0].dataIndex];return `<b>${d.dk}</b><br/>${d.cat}<br/>${d.prev}→${d.cur} (${d.units} removed)`;}},
    xAxis:{type:'value',axisLabel:{color:C.muted},splitLine},
    yAxis:{type:'category',data:mv.map(m=>m.dk),axisLabel:{color:'#cdd3e0',fontSize:10},axisLine},
    series:[{type:'bar',data:mv.map(m=>m.units),barWidth:'62%',
      itemStyle:{borderRadius:[0,6,6,0],color:new echarts.graphic.LinearGradient(0,0,1,0,[{offset:0,color:'#8b7cff'},{offset:1,color:'#35d0ba'}])}}]
  });
}

addEventListener('resize',()=>document.querySelectorAll('.chart').forEach(e=>echarts.getInstanceByDom(e)?.resize()));
</script>
</body>
</html>"""


def _echarts_tag() -> str:
    """Inline the vendored ECharts for a fully offline file; fall back to CDN."""
    if VENDOR_ECHARTS.exists():
        return "<script>" + VENDOR_ECHARTS.read_text(encoding="utf-8") + "</script>"
    return f'<script src="{_CDN_ECHARTS}"></script>'


def build_html(payload: dict, date: str, data_dir: Path = config.DATA_DIR) -> Path:
    html = (TEMPLATE
            .replace("__ECHARTS__", _echarts_tag())
            .replace("__DATA__", json.dumps(payload))
            .replace("__DATE__", date))
    path = data_dir / f"dashboard_{date}.html"
    path.write_text(html, encoding="utf-8")
    return path


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Render the analysis dashboard (HTML)")
    ap.add_argument("--date", default=None, help="Snapshot date YYYY-MM-DD (default: latest)")
    args = ap.parse_args(argv)

    csv_path = (config.DATA_DIR / f"excelta_{args.date}.csv") if args.date else A.latest_csv()
    date = csv_path.stem.replace("excelta_", "")
    rows = A.load_rows(csv_path)
    res = A.run_all(rows)
    payload = build_payload(rows, res, date)
    path = build_html(payload, date)
    print(f"Dashboard for {len(rows)} SKUs -> {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
