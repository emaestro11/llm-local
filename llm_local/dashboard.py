"""Space Invaders style dashboard for Phase 1.

Serves a single HTML page at http://localhost:8090 with two tabs:
  1. ARCHITECTURE - high-level diagram of the system
  2. DECISIONS   - live table of Gemma4's decisions, polled every 3s

Run: uv run python -m llm_local.dashboard
"""

import json
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse

from sqlalchemy import select

from llm_local.config import load_config
from llm_local.models import Candle, Decision, ReplayRun, get_session, init_db

PORT = 8090

HTML = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>LLM LOCAL // PHASE 1 DASHBOARD</title>
<link href="https://fonts.googleapis.com/css2?family=Press+Start+2P&family=VT323&display=swap" rel="stylesheet">
<style>
  :root{
    --green:#33ff66;
    --dim:#0f7a2a;
    --red:#ff3355;
    --yellow:#ffcc33;
    --cyan:#33ccff;
    --bg:#020402;
  }
  *{box-sizing:border-box;margin:0;padding:0}
  html,body{background:var(--bg);color:var(--green);font-family:'VT323',monospace;font-size:20px;min-height:100vh}
  body{padding:20px;overflow-x:hidden;position:relative}
  body::before{
    content:"";
    position:fixed;inset:0;pointer-events:none;z-index:99;
    background:repeating-linear-gradient(
      0deg,
      rgba(0,0,0,0) 0px,
      rgba(0,0,0,0) 2px,
      rgba(0,0,0,.25) 3px,
      rgba(0,0,0,.25) 4px
    );
  }
  body::after{
    content:"";position:fixed;inset:0;pointer-events:none;z-index:100;
    background:radial-gradient(ellipse at center, transparent 55%, rgba(0,0,0,.6) 100%);
  }
  h1,h2,.title{font-family:'Press Start 2P',monospace;color:var(--green);text-shadow:0 0 8px var(--green)}
  h1{font-size:28px;letter-spacing:2px}
  .banner{
    border:2px solid var(--green);padding:16px 20px;margin-bottom:18px;
    display:flex;justify-content:space-between;align-items:center;gap:20px;
    box-shadow:0 0 16px rgba(51,255,102,.35) inset, 0 0 16px rgba(51,255,102,.15);
    background:linear-gradient(180deg, rgba(51,255,102,.04), rgba(0,0,0,0));
  }
  .stats{display:flex;gap:24px;font-family:'Press Start 2P',monospace;font-size:12px}
  .stat{color:var(--dim)}
  .stat b{color:var(--green);font-size:14px;margin-left:6px}
  .invaders{display:flex;gap:10px;font-size:32px;color:var(--green);text-shadow:0 0 8px var(--green)}
  .invader{animation:bob 1.4s steps(2) infinite}
  .invader:nth-child(2){animation-delay:.2s}
  .invader:nth-child(3){animation-delay:.4s}
  @keyframes bob{50%{transform:translateY(-6px)}}
  .tabs{display:flex;gap:4px;margin-bottom:18px}
  .tab{
    font-family:'Press Start 2P',monospace;font-size:12px;
    padding:10px 18px;background:transparent;border:2px solid var(--dim);
    color:var(--dim);cursor:pointer;letter-spacing:2px;
  }
  .tab.active{border-color:var(--green);color:var(--green);background:rgba(51,255,102,.08);box-shadow:0 0 12px rgba(51,255,102,.35)}
  .tab:hover{color:var(--green);border-color:var(--green)}
  .panel{display:none;border:2px solid var(--green);padding:20px;min-height:60vh;background:rgba(51,255,102,.02)}
  .panel.active{display:block}

  /* ARCHITECTURE */
  .arch-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:16px}
  .block{
    border:2px solid var(--green);padding:14px;background:rgba(51,255,102,.05);
    position:relative;min-height:110px;
  }
  .block h3{font-family:'Press Start 2P',monospace;font-size:11px;color:var(--yellow);margin-bottom:10px;letter-spacing:1px}
  .block code{font-family:'VT323',monospace;color:var(--cyan);display:block;margin:2px 0;font-size:18px}
  .block p{margin-top:8px;color:#9ae8b3;font-size:17px;line-height:1.35}
  .arrow{font-family:'Press Start 2P',monospace;color:var(--yellow);text-align:center;font-size:16px;align-self:center}
  .row-label{grid-column:1/-1;font-family:'Press Start 2P',monospace;font-size:11px;color:var(--dim);border-bottom:1px dashed var(--dim);padding-bottom:4px;margin-top:10px}

  /* DECISIONS */
  .run-meta{display:flex;gap:30px;margin-bottom:18px;flex-wrap:wrap;font-size:18px}
  .run-meta div{padding:6px 12px;border:1px solid var(--dim)}
  .run-meta b{color:var(--yellow);margin-left:6px}
  table{width:100%;border-collapse:collapse;font-size:17px}
  th,td{padding:6px 10px;text-align:left;border-bottom:1px dashed var(--dim);vertical-align:top}
  th{font-family:'Press Start 2P',monospace;font-size:10px;color:var(--yellow);letter-spacing:1px}
  tbody tr:hover{background:rgba(51,255,102,.05)}
  .act{font-family:'Press Start 2P',monospace;font-size:10px;padding:4px 6px;border:1px solid;display:inline-block;min-width:50px;text-align:center}
  .act.buy{color:var(--green);border-color:var(--green);background:rgba(51,255,102,.1)}
  .act.sell{color:var(--red);border-color:var(--red);background:rgba(255,51,85,.1)}
  .act.hold{color:var(--yellow);border-color:var(--yellow);background:rgba(255,204,51,.08)}
  .act.force_close{color:var(--red);border-color:var(--red);background:rgba(255,51,85,.18)}
  .confbar{display:inline-block;width:80px;height:12px;background:#051b09;border:1px solid var(--dim);position:relative;vertical-align:middle}
  .conffill{position:absolute;inset:0 auto 0 0;background:var(--green);box-shadow:0 0 6px var(--green)}
  .fallback{color:var(--red)}
  .reasoning{color:#9ae8b3;max-width:650px;font-size:16px;line-height:1.35}
  .empty{padding:40px;text-align:center;color:var(--dim);font-family:'Press Start 2P',monospace;font-size:12px;letter-spacing:2px}
  .blink{animation:blink 1s steps(1) infinite}
  @keyframes blink{50%{opacity:0}}
  .footer{margin-top:18px;color:var(--dim);font-size:16px;text-align:right}
</style>
</head>
<body>

<div class="banner">
  <div>
    <h1>&gt; LLM_LOCAL.EXE</h1>
    <div style="color:var(--dim);margin-top:6px;letter-spacing:2px">PHASE 1 // DECISION HARNESS // GEMMA4-26B</div>
  </div>
  <div class="invaders"><span class="invader">&#x1F47E;</span><span class="invader">&#x1F47E;</span><span class="invader">&#x1F47E;</span></div>
  <div class="stats">
    <div class="stat">RUN<b id="s-run">-</b></div>
    <div class="stat">DECISIONS<b id="s-count">0</b></div>
    <div class="stat">FALLBACKS<b id="s-fb">0</b></div>
    <div class="stat">STATUS<b id="s-status">--</b></div>
  </div>
</div>

<div class="tabs">
  <button class="tab active" data-tab="arch">[1] ARCHITECTURE</button>
  <button class="tab" data-tab="dec">[2] DECISIONS</button>
</div>

<div class="panel active" id="panel-arch">
  <div class="arch-grid">

    <div class="row-label">== INPUT LAYER ==</div>
    <div class="block">
      <h3>BINANCE API</h3>
      <code>ccxt public endpoint</code>
      <p>30d of BTC/USDT 15m candles. No auth. Paginated fetch (1000/req).</p>
    </div>
    <div class="block">
      <h3>config.toml</h3>
      <code>pair, timeframe, fees</code>
      <code>LLM url / tokens / timeout</code>
      <p>Loaded once into Config dataclass. Secrets from env.</p>
    </div>
    <div class="block">
      <h3>data.py</h3>
      <code>fetch_ohlcv()</code>
      <code>compute_indicators()</code>
      <p>pandas-ta: RSI(14), MACD(12,26,9), BB(20,2), SMA(24/96).</p>
    </div>

    <div class="row-label">== STORAGE ==</div>
    <div class="block" style="grid-column:1/-1">
      <h3>SQLITE / trading.db</h3>
      <code>candles   (2,880 rows, OHLCV + indicators)</code>
      <code>decisions (one per candle per run, action/conf/size/reasoning/latency/raw)</code>
      <code>replay_runs (metadata: prompt_version, config JSON, status)</code>
    </div>

    <div class="row-label">== DECISION LAYER ==</div>
    <div class="block">
      <h3>prompts.py</h3>
      <code>build_prompt(v1)</code>
      <p>System + user templates. 24-candle window with OHLCV + TA + position. Version-tracked per run.</p>
    </div>
    <div class="block">
      <h3>harness.py</h3>
      <code>make_decision()</code>
      <code>json_schema constraint</code>
      <p>Pure fn. llama.cpp /v1/chat/completions. Grammar-constrained JSON. Circuit breaker at 3 fails.</p>
    </div>
    <div class="block">
      <h3>llama.cpp :8080</h3>
      <code>gemma-4-26B-Q4_K_XL</code>
      <code>OpenAI compat API</code>
      <p>Reasoning model. 2048 max_tokens (thinking + JSON). ~10-15s/call.</p>
    </div>

    <div class="row-label">== ORCHESTRATION ==</div>
    <div class="block">
      <h3>replay.py</h3>
      <code>run_replay(quick)</code>
      <code>SimulatedPosition</code>
      <p>Iterates candles (skip first 34 warmup). Tracks position. 24-candle max hold. Checkpoint/resume.</p>
    </div>
    <div class="block">
      <h3>analysis.py</h3>
      <code>analyze_run(run_id)</code>
      <p>Joins decisions to candles. Win rate, Sharpe, max DD, Pearson r(confidence, return).</p>
    </div>
    <div class="block">
      <h3>CLI / __main__.py</h3>
      <code>fetch</code>
      <code>replay [--quick]</code>
      <code>analyze [--compare]</code>
      <p>Entry points. argparse dispatch to module functions.</p>
    </div>

  </div>
</div>

<div class="panel" id="panel-dec">
  <div class="run-meta" id="run-meta">
    <div>LOADING<b>...</b></div>
  </div>
  <table>
    <thead>
      <tr>
        <th>#</th>
        <th>CANDLE_TS</th>
        <th>ACTION</th>
        <th>CONF</th>
        <th>SIZE</th>
        <th>LAT</th>
        <th>REASONING</th>
      </tr>
    </thead>
    <tbody id="dec-body">
      <tr><td colspan="7" class="empty">AWAITING DECISIONS<span class="blink">_</span></td></tr>
    </tbody>
  </table>
  <div class="footer">Auto-refresh every 3s. Last update: <span id="last-update">never</span></div>
</div>

<script>
  const tabs = document.querySelectorAll('.tab');
  tabs.forEach(t => t.addEventListener('click', () => {
    tabs.forEach(x => x.classList.remove('active'));
    document.querySelectorAll('.panel').forEach(p => p.classList.remove('active'));
    t.classList.add('active');
    document.getElementById('panel-' + t.dataset.tab).classList.add('active');
  }));

  function fmtTs(ms){
    if(!ms) return '-';
    const d = new Date(ms);
    return d.toISOString().slice(5,16).replace('T',' ');
  }

  async function refresh(){
    try {
      const r = await fetch('/api/state');
      const s = await r.json();
      document.getElementById('s-run').textContent = s.run_id ?? '-';
      document.getElementById('s-count').textContent = s.decision_count;
      document.getElementById('s-fb').textContent = s.fallback_count;
      document.getElementById('s-status').textContent = s.status ?? '--';

      // run meta
      const meta = document.getElementById('run-meta');
      if(s.run_id){
        meta.innerHTML =
          `<div>RUN<b>#${s.run_id}</b></div>` +
          `<div>PROMPT<b>${s.prompt_version}</b></div>` +
          `<div>SYMBOL<b>${s.symbol}/${s.timeframe}</b></div>` +
          `<div>STATUS<b>${s.status}</b></div>` +
          `<div>QUICK<b>${s.quick_mode ? 'YES' : 'NO'}</b></div>` +
          `<div>AVG LAT<b>${s.avg_latency_ms}ms</b></div>`;
      }

      // table
      const body = document.getElementById('dec-body');
      if(!s.decisions || s.decisions.length === 0){
        body.innerHTML = `<tr><td colspan="7" class="empty">AWAITING DECISIONS<span class="blink">_</span></td></tr>`;
      } else {
        body.innerHTML = s.decisions.map(d => {
          const confPct = Math.round((d.confidence || 0) * 100);
          const sizePct = Math.round((d.size_pct || 0) * 100);
          const actCls = (d.action || 'hold').replace(/[^a-z_]/g,'');
          const fb = d.is_fallback ? '<span class="fallback"> [FB]</span>' : '';
          return `<tr>
            <td>${d.id}</td>
            <td>${fmtTs(d.candle_ts)}</td>
            <td><span class="act ${actCls}">${(d.action || 'hold').toUpperCase()}</span>${fb}</td>
            <td>${confPct}% <span class="confbar"><span class="conffill" style="width:${confPct}%"></span></span></td>
            <td>${sizePct}%</td>
            <td>${d.latency_ms || 0}ms</td>
            <td class="reasoning">${(d.reasoning || '').replace(/[<>&]/g, c=>({'<':'&lt;','>':'&gt;','&':'&amp;'}[c]))}</td>
          </tr>`;
        }).join('');
      }

      document.getElementById('last-update').textContent = new Date().toLocaleTimeString();
    } catch(e) {
      console.error(e);
    }
  }

  refresh();
  setInterval(refresh, 3000);
</script>
</body>
</html>
"""


def _fetch_state(db_path: str) -> dict:
    engine = init_db(db_path)
    with get_session(engine) as session:
        run = session.execute(
            select(ReplayRun).order_by(ReplayRun.id.desc()).limit(1)
        ).scalar_one_or_none()

        if run is None:
            return {
                "run_id": None,
                "decision_count": 0,
                "fallback_count": 0,
                "status": "no runs yet",
                "decisions": [],
            }

        decisions = session.execute(
            select(Decision, Candle)
            .join(Candle, Decision.candle_id == Candle.id)
            .where(Decision.run_id == run.id)
            .order_by(Decision.id.desc())
            .limit(100)
        ).all()

        total = session.execute(
            select(Decision).where(Decision.run_id == run.id)
        ).scalars().all()
        total_count = len(total)
        fallback_count = sum(1 for d in total if d.is_fallback)
        latencies = [d.latency_ms for d in total if d.latency_ms]
        avg_lat = int(sum(latencies) / len(latencies)) if latencies else 0

        decision_rows = [
            {
                "id": d.id,
                "candle_ts": c.timestamp_ms,
                "action": d.action,
                "confidence": d.confidence,
                "size_pct": d.size_pct,
                "latency_ms": d.latency_ms,
                "reasoning": d.reasoning or "",
                "is_fallback": bool(d.is_fallback),
            }
            for d, c in decisions
        ]

        return {
            "run_id": run.id,
            "prompt_version": run.prompt_version,
            "symbol": run.symbol,
            "timeframe": run.timeframe,
            "quick_mode": bool(run.quick_mode),
            "status": run.status,
            "decision_count": total_count,
            "fallback_count": fallback_count,
            "avg_latency_ms": avg_lat,
            "decisions": decision_rows,
        }


class Handler(BaseHTTPRequestHandler):
    db_path = "trading.db"

    def log_message(self, *args, **kwargs):
        pass

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/" or path == "/index.html":
            body = HTML.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif path == "/api/state":
            try:
                state = _fetch_state(self.db_path)
                body = json.dumps(state).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            except Exception as e:
                err = json.dumps({"error": str(e)}).encode("utf-8")
                self.send_response(500)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(err)))
                self.end_headers()
                self.wfile.write(err)
        else:
            self.send_response(404)
            self.end_headers()


def main():
    config = load_config()
    Handler.db_path = config.db_path
    server = HTTPServer(("127.0.0.1", PORT), Handler)
    print(f"Dashboard at http://localhost:{PORT}  (Ctrl-C to stop)")
    print(f"Reading from {config.db_path}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.")
        server.server_close()


if __name__ == "__main__":
    main()
