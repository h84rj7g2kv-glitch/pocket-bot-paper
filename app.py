
import os, time, hmac, hashlib, base64, urllib.parse, threading, sqlite3, json
from functools import wraps
from pathlib import Path
from flask import Flask, request, jsonify, render_template, session
import requests

BASE=Path(__file__).parent
app=Flask(__name__)
app.secret_key=os.getenv("SESSION_SECRET", "local-dev-only-change-before-deploy")
app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Strict", SESSION_COOKIE_SECURE=os.getenv("HTTPS_ONLY")=="true")
PASSWORD=os.getenv("APP_PASSWORD","")
DB=str(BASE/"bot.sqlite3")
PAIRS={"BTC/USD":"XBTUSD","ETH/USD":"ETHUSD","SOL/USD":"SOLUSD"}
lock=threading.RLock()
state={"running":False,"last_check":None,"error":None}
def conn():
    c=sqlite3.connect(DB,timeout=20)
    c.row_factory=sqlite3.Row
    return c
def init():
    with conn() as c:
        c.execute("CREATE TABLE IF NOT EXISTS config (k TEXT PRIMARY KEY,v TEXT NOT NULL)")
        c.execute("CREATE TABLE IF NOT EXISTS trades (id INTEGER PRIMARY KEY AUTOINCREMENT,ts INTEGER,pair TEXT,side TEXT,qty REAL,price REAL,fee REAL,reason TEXT)")
        defaults={"cash":"1000","btc":"0","eth":"0","sol":"0","max_trade_pct":"5","stop_loss_pct":"2","take_profit_pct":"4","max_daily_loss_pct":"3","fee_pct":"0.4","auto":"false"}
        for k,v in defaults.items(): c.execute("INSERT OR IGNORE INTO config VALUES (?,?)",(k,v))
def get(c,k): return c.execute("SELECT v FROM config WHERE k=?",(k,)).fetchone()["v"]
def setv(c,k,v): c.execute("UPDATE config SET v=? WHERE k=?",(str(v),k))
def guard(f):
    @wraps(f)
    def inner(*a,**kw):
        if not session.get("ok"): return jsonify(error="Login required"),401
        return f(*a,**kw)
    return inner
@app.after_request
def headers(r):
    r.headers["X-Content-Type-Options"]="nosniff"
    r.headers["X-Frame-Options"]="DENY"
    r.headers["Cache-Control"]="no-store"
    return r
@app.route("/")
def home(): return render_template("index.html")
@app.post("/api/login")
def login():
    if not PASSWORD: return jsonify(error="Set APP_PASSWORD on the server first"),503
    if not hmac.compare_digest(str(request.json.get("password","")),PASSWORD): return jsonify(error="Incorrect password"),403
    session["ok"]=True
    return jsonify(ok=True)
@app.post("/api/logout")
def logout(): session.clear();return jsonify(ok=True)
def public(endpoint,params=None):
    r=requests.get("https://api.kraken.com/0/public/"+endpoint,params=params,timeout=12)
    r.raise_for_status(); j=r.json()
    if j.get("error"): raise ValueError(str(j["error"]))
    return j["result"]
def candles(pair):
    d=public("OHLC",{"pair":PAIRS[pair],"interval":15})
    k=next(x for x in d if x!="last")
    return d[k][:-1] # exclude unfinished candle
def ticker(pair):
    d=public("Ticker",{"pair":PAIRS[pair]})
    return float(next(iter(d.values()))["c"][0])
def signal(rows):
    prices=[float(r[4]) for r in rows]
    if len(prices)<23:return "WAIT"
    fast_prev=sum(prices[-10:-1])/9
    slow_prev=sum(prices[-22:-1])/21
    fast=sum(prices[-9:])/9
    slow=sum(prices[-21:])/21
    return "BUY" if fast_prev<=slow_prev and fast>slow else "SELL" if fast_prev>=slow_prev and fast<slow else "HOLD"
def info():
    out={}
    for pair in PAIRS:
        try:
            rows=candles(pair)
            out[pair]={"price":ticker(pair),"signal":signal(rows),"last_closed":int(rows[-1][0])}
        except Exception as e: out[pair]={"error":str(e)}
    return out
def snapshot(c,markets):
    cash=float(get(c,"cash"))
    positions={}
    total=cash
    for pair in PAIRS:
        sym=pair.split("/")[0].lower()
        qty=float(get(c,sym))
        p=markets.get(pair,{}).get("price",0)
        value=qty*p
        positions[pair]={"qty":qty,"value":value}
        total+=value
    return {"cash":cash,"equity":total,"positions":positions}
def paper_trade(c,pair,side,markets,reason):
    p=markets[pair].get("price")
    if not p or p<=0:raise ValueError("Price unavailable")
    snap=snapshot(c,markets)
    cash=snap["cash"]; equity=snap["equity"]
    sym=pair.split("/")[0].lower()
    owned=float(get(c,sym))
    fee=float(get(c,"fee_pct"))/100
    allocation=float(get(c,"max_trade_pct"))/100
    if side=="buy":
        budget=min(cash, equity*allocation)
        if budget<5: raise ValueError("Not enough virtual cash for trade")
        qty=(budget/(1+fee))/p
        cost=qty*p
        setv(c,"cash",cash-cost*(1+fee))
        setv(c,sym,owned+qty)
    else:
        if owned<=0:raise ValueError("No position to sell")
        qty=owned;cost=qty*p
        setv(c,"cash",cash+cost*(1-fee))
        setv(c,sym,0)
    c.execute("INSERT INTO trades (ts,pair,side,qty,price,fee,reason) VALUES (?,?,?,?,?,?,?)",
              (int(time.time()),pair,side,qty,p,cost*fee,reason))
    return {"qty":qty,"price":p}
def daily_loss(c,markets):
    # Conservative: realized paper trade cash flows aren't a reliable daily equity baseline.
    # Use mark-to-market baseline stored at start of UTC day.
    day=time.strftime("%Y-%m-%d",time.gmtime())
    key="baseline_"+day
    row=c.execute("SELECT v FROM config WHERE k=?",(key,)).fetchone()
    eq=snapshot(c,markets)["equity"]
    if not row:
        c.execute("INSERT INTO config VALUES (?,?)",(key,str(eq)))
        return 0
    base=float(row["v"])
    return max(0,(base-eq)/base*100) if base else 0
def cycle():
    markets=info()
    with lock,conn() as c:
        if any("error" in v for v in markets.values()): raise ValueError("Market data unavailable; no trades made")
        if daily_loss(c,markets)>=float(get(c,"max_daily_loss_pct")):return
        for pair,market in markets.items():
            sig=market["signal"]
            sym=pair.split("/")[0].lower()
            qty=float(get(c,sym))
            # Stops and targets based on weighted-average acquisition price of current position.
            if qty>0:
                buys=c.execute("SELECT qty,price FROM trades WHERE pair=? AND side='buy' ORDER BY id DESC",(pair,)).fetchall()
                avg=sum(x["qty"]*x["price"] for x in buys)/sum(x["qty"] for x in buys) if buys else market["price"]
                move=(market["price"]/avg-1)*100
                if move<=-float(get(c,"stop_loss_pct")): sig="SELL";reason="Stop loss (polled)"
                elif move>=float(get(c,"take_profit_pct")):sig="SELL";reason="Take profit (polled)"
                else:reason="MA crossover"
            else:reason="MA crossover"
            if sig=="BUY" and qty==0:paper_trade(c,pair,"buy",markets,reason)
            elif sig=="SELL" and qty>0:paper_trade(c,pair,"sell",markets,reason)
def worker():
    while True:
        try:
            with conn() as c: enabled=get(c,"auto")=="true"
            state["running"]=enabled
            if enabled:cycle()
            state["error"]=None
        except Exception as e:state["error"]=str(e)
        state["last_check"]=int(time.time())
        time.sleep(60)
@app.get("/api/status")
@guard
def status():
    markets=info()
    with conn() as c:
        trades=[dict(x) for x in c.execute("SELECT * FROM trades ORDER BY id DESC LIMIT 50")]
        config={k:float(get(c,k)) for k in ("max_trade_pct","stop_loss_pct","take_profit_pct","max_daily_loss_pct","fee_pct")}
        return jsonify(markets=markets,portfolio=snapshot(c,markets),trades=trades,config=config,auto=get(c,"auto")=="true",worker=state,live_enabled=False)
@app.post("/api/config")
@guard
def config():
    data=request.get_json(force=True)
    bounds={"max_trade_pct":(0.1,10),"stop_loss_pct":(0.5,10),"take_profit_pct":(0.5,25),"max_daily_loss_pct":(0.5,10),"fee_pct":(0,2)}
    with lock,conn() as c:
        for k,v in data.items():
            if k not in bounds: return jsonify(error="Invalid setting"),400
            try:v=float(v)
            except: return jsonify(error="Invalid number"),400
            lo,hi=bounds[k]
            if not lo<=v<=hi:return jsonify(error=f"{k} must be {lo}–{hi}"),400
            setv(c,k,v)
    return jsonify(ok=True)
@app.post("/api/auto")
@guard
def auto():
    enabled=request.get_json(force=True).get("enabled")
    if not isinstance(enabled,bool):return jsonify(error="Boolean required"),400
    with lock,conn() as c:setv(c,"auto","true" if enabled else "false")
    return jsonify(ok=True)
@app.post("/api/paper-order")
@guard
def order():
    d=request.get_json(force=True)
    pair=d.get("pair");side=d.get("side")
    if pair not in PAIRS or side not in ("buy","sell"):return jsonify(error="Invalid pair or side"),400
    try:
        markets=info()
        with lock,conn() as c:result=paper_trade(c,pair,side,markets,"Manual paper order")
        return jsonify(ok=True,trade=result)
    except Exception as e:return jsonify(error=str(e)),400
@app.post("/api/live-order")
@guard
def live_order():
    return jsonify(error="Live orders deliberately disabled. Requires independent execution-service review, exchange permissions and deployment hardening."),403
if __name__=="__main__":
    init()
    threading.Thread(target=worker,daemon=True).start()
    app.run(host="127.0.0.1",port=8080,debug=False)

