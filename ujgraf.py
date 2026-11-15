import json
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import numpy as np
import requests
from dateutil import tz
from dotenv import load_dotenv
from matplotlib.widgets import Button

load_dotenv()
AIO_KEY = os.environ.get("AIO_KEY")
if not AIO_KEY:
    raise RuntimeError("Hiányzó kulcs!")
BASE_URL = "https://io.adafruit.com/api/v2/atka/feeds"
PRED_FILE = Path(__file__).resolve().with_name("homerseklet_elorejelzes.json")

FETCH_INTERVAL = 60
PAGE_SIZE, MAX_PAGES = 1000, 50
MATCH_TOL = timedelta(minutes=2)
TOL = timedelta(minutes=5)
TEMP_CLIP = (-40, 60)
NEXT_ALPHA, DAILY_ALPHA = 1.0, 5.0
HALF_LIFE_DAYS = 3.0
MIN_PAIRS = 15

FEEDS = [
    dict(key="homerseklet", name="Hőmérséklet", unit="°C", ylim=(-10, 50), color="blue"),
    dict(key="paratartalom", name="Páratartalom", unit="%", ylim=(0, 100), color="green"),
    dict(key="voc", name="VOC Index", unit="", ylim=(0, 500), color="red"),
]
TEMP, U = FEEDS[0], FEEDS[0]["unit"]

session = requests.Session()
session.headers["X-AIO-Key"] = AIO_KEY


#lekeri egyszer az adatokat adafruit io-rol
def fetch(key, limit, end=None):
    params = {"limit": limit}
    if end:
        params["end_time"] = end.astimezone(timezone.utc).isoformat()
    try:
        res = session.get(f"{BASE_URL}/{key}/data", params=params, timeout=15)
        res.raise_for_status()
    except requests.RequestException as e:
        print(f"[Hiba] {key}: {e}")
        return []
    return [(datetime.fromisoformat(d["created_at"].replace("Z", "+00:00")).astimezone(), float(d["value"]), d["id"])
            for d in reversed(res.json())]

#addig hivja a fetchet ameddig minden adatot meg nem kap
def fetch_all(key):
    out, seen, end = [], set(), None
    for _ in range(MAX_PAGES):
        page = fetch(key, PAGE_SIZE, end)
        new = [r for r in page if r[2] not in seen]
        if not new:
            break
        seen.update(r[2] for r in new)
        out, end = new + out, new[0][0]
        if len(page) < PAGE_SIZE:
            break
    return out

#a 3 feedet idopont szerint parositja
def merge(series):
    pos = [0] * (len(series) - 1)
    rows = []
    for dt, v, _ in series[0]:
        vals = [v]
        for j, s in enumerate(series[1:]):
            i = pos[j]
            while i + 1 < len(s) and abs(s[i + 1][0] - dt) <= abs(s[i][0] - dt):
                i += 1
            pos[j] = i
            vals.append(s[i][1] if s and abs(s[i][0] - dt) <= MATCH_TOL else np.nan)
        rows.append((dt, vals))
    return rows



HARM = ["sin(2πh/24)", "cos(2πh/24)", "sin(4πh/24)", "cos(4πh/24)"]

#napszakos jellemzok
def harmonics(times):
    w = 2 * np.pi * np.array([t.hour + t.minute / 60 for t in times]) / 24
    return np.column_stack([np.sin(w), np.cos(w), np.sin(2 * w), np.cos(2 * w)])

#sulyozott ridge regreszzio
def fit_ridge(X, y, alpha, weights=None):
    sw = np.ones(len(y)) if weights is None else weights
    Xm, ym = np.average(X, axis=0, weights=sw), np.average(y, weights=sw)
    Xc = X - Xm
    w = np.linalg.solve(Xc.T @ (Xc * sw[:, None]) + alpha * np.eye(X.shape[1]), Xc.T @ (sw * (y - ym)))
    return ym - Xm @ w, w

#keplet kiirasa
def formula(lhs, b, names, w):
    terms = " ".join(f"{'+' if c >= 0 else '−'} {abs(c):.4f}·{n}" for c, n in zip(w, names) if c)
    return f"{lhs} = {b:.4f} {terms}"

#napszakos modell tanitasa
def daily_model(times, vals):
    age = np.array([(times[-1] - t).total_seconds() / 86400 for t in times])
    return fit_ridge(harmonics(times), np.asarray(vals), DAILY_ALPHA, 0.5 ** (age / HALF_LIFE_DAYS))

#kovetkezo meres becslesehez keszit elo adatokat
def next_pairs(times, vals, step):
    v = np.asarray(vals)
    idx = np.array([i for i in range(1, len(times)) if times[i] - times[i - 1] <= step * 1.5], dtype=int)
    return np.column_stack([v[idx - 1], harmonics([times[i] for i in idx])]), v[idx]

#meresek kozott eltelt ido
def measurement_interval(times, n=20):
    d = np.diff([t.timestamp() for t in times[-n:]])
    d = d[d > 0]
    return timedelta(seconds=float(np.median(d))) if len(d) else timedelta(hours=1)



t_all, ys = [], [[] for _ in FEEDS]
pending = None
is_running = True
pred_artists = []

plt.ion()
fig, axes = plt.subplots(3, 1, figsize=(12, 8), sharex=True)
plt.subplots_adjust(bottom=0.18, top=0.90)
for ax, f in zip(axes, FEEDS):
    f["ax"] = ax
    ax.set(ylim=f["ylim"], ylabel=f"{f['name']} ({f['unit']})" if f["unit"] else f["name"])
    ax.grid(True)
    (f["line"],) = ax.plot([], [], marker="o", ms=3, lw=1, color=f["color"], label=f"Mért {f['name']}")
    ax.legend(loc="upper left")
locator = mdates.AutoDateLocator(tz=tz.tzlocal())
axes[-1].xaxis.set_major_locator(locator)
axes[-1].xaxis.set_major_formatter(mdates.ConciseDateFormatter(locator, tz=tz.tzlocal()))
axes[-1].set_xlabel("Idő")

#ablak cime
def set_title(text=None):
    fig.suptitle(text or "Valós idejű szenzoradatok", fontsize=11 if text else 14,
                 fontweight="bold" if text else "normal")


set_title()
X_OF = mdates.date2num

#egy merest hozzaad a listahoz
def add_point(dt, vals, verbose=True):
    t_all.append(dt)
    for y, v in zip(ys, vals):
        y.append(v)
    if verbose:
        print(f"[{dt:%m-%d %H:%M}] " + " | ".join(
            f"{f['name']}: " + ("—" if np.isnan(v) else f"{v:.2f}{f['unit']}") for f, v in zip(FEEDS, vals)))

#ujrarajzolja a grafikont
def redraw():
    x = X_OF(t_all)
    for f, y in zip(FEEDS, ys):
        f["line"].set_data(x, y)
        f["line"].set_marker("o" if len(x) <= 200 else "")
    if len(x):
        right = max(x[-1], X_OF(pending["target"])) if pending else x[-1]
        pad = max(1 / 48, (right - x[0]) * 0.03)
        axes[-1].set_xlim(x[0] - pad / 3, right + pad)
    TEMP["ax"].legend(loc="upper left")
    fig.canvas.draw_idle()

#beallitja vagy torli a becslest
def set_pending(p):
    global pending
    pending = p
    try:
        if p:
            data = {**p, "target": p["target"].isoformat(), "after": p["after"].isoformat()}
            PRED_FILE.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        else:
            PRED_FILE.unlink(missing_ok=True)
    except OSError as e:
        print(f"[Hiba] Előrejelzés-fájl: {e}")

#becsles torlese grafikonrol
def clear_pred_artists():
    while pred_artists:
        pred_artists.pop().remove()

#becsles rajzolasa
def draw_pending():
    if not (pending and t_all):
        return
    ax, t, p = TEMP["ax"], pending["target"], pending["val"]
    pred_artists.extend(ax.plot([X_OF(t_all[-1]), X_OF(t)], [ys[0][-1], p], "m--", lw=2, label=pending["label"]))
    pred_artists.extend(ax.plot(X_OF(t), p, "r*", ms=12, zorder=5, label=f"Jósolt (~{t:%m-%d %H:%M}): {p:.2f}{U}"))

#meres es becsles osszehasonlitasa
def evaluate(dt, actual):
    xp, xa, p = X_OF(pending["target"]), X_OF(dt), pending["val"]
    err = actual - p
    pct = f" ({abs(err) / abs(actual) * 100:.1f}%)" if abs(actual) >= 0.5 else ""  # 0 °C körül értelmetlen
    msg = (f"Becslés (~{pending['target']:%m-%d %H:%M}): {p:.2f}{U} | Mért ({dt:%H:%M}): {actual:.2f}{U} | "
           f"Hiba: {err:+.2f}{U}{pct}")
    print(f"\n[PREDIKCIÓS HIBA] {msg}\n  Használt képlet: {pending['formula']}")
    ax = TEMP["ax"]
    pred_artists.extend(ax.plot([xp, xa], [p, actual], color="darkorange", ls=":", lw=2.5, zorder=4))
    pred_artists.append(ax.annotate(
        f"Hiba: {err:+.2f}{U}{pct}", xy=((xp + xa) / 2, (p + actual) / 2), xytext=(8, 0),
        textcoords="offset points", color="darkred", fontweight="bold", fontsize=9,
        bbox=dict(boxstyle="round,pad=0.2", fc="lightyellow", ec="orange", alpha=0.9)))
    set_title(msg)
    set_pending(None)

#urjainditas eseten becsles betoltese
def restore_pending():
    global pending
    try:
        d = json.loads(PRED_FILE.read_text(encoding="utf-8"))
        pending = {"label": "Trend", "formula": "", **d, "val": float(d["val"]),
                   "target": datetime.fromisoformat(d["target"]), "after": datetime.fromisoformat(d["after"])}
    except FileNotFoundError:
        return
    except (OSError, ValueError, KeyError, TypeError) as e:
        print(f"A mentett előrejelzés hibás vagy régi formátumú, eldobom: {e}")
        return set_pending(None)

    print(f"[INFO] Mentett becslés: ~{pending['target']:%m-%d %H:%M} -> {pending['val']:.2f}{U}")
    later = [(dt, v) for dt, v in zip(t_all, ys[0]) if dt > pending["after"]]
    if later:
        evaluate(*later[0])
    else:
        draw_pending()
        set_title(f"Mentett hőmérséklet-becslés: ~{pending['target']:%m-%d %H:%M} → {pending['val']:.2f}{U}")
    redraw()


#meresek kirajzolasa
def load_history():

    print("Az összes eddigi adat betöltése...")
    rows = merge([fetch_all(f["key"]) for f in FEEDS])
    if not rows:
        return print("Nem sikerült korábbi adatokat betölteni.")
    for dt, vals in rows[:-1]:
        add_point(dt, vals, verbose=False)
    print(f"[INFO] {len(rows)} mérés: {rows[0][0]:%Y-%m-%d %H:%M} – {rows[-1][0]:%Y-%m-%d %H:%M}. Legutóbbi:")
    add_point(*rows[-1])
    redraw()

#meresek lekerese, becsles kiertekelese
def poll():
    rows = [r for r in merge([fetch(f["key"], 50) for f in FEEDS]) if not t_all or r[0] > t_all[-1]]
    # ha a legfrissebb mérés egyik feedje még nem ért fel, várunk vele
    if rows and np.isnan(rows[-1][1]).any() and datetime.now().astimezone() - rows[-1][0] < MATCH_TOL:
        rows.pop()
    if not rows:
        return print("Még nincs újabb adat...")
    for dt, vals in rows:
        add_point(dt, vals)
        if pending and dt > pending["after"]:
            evaluate(dt, vals[0])
    redraw()


#uj becsles
def start_prediction(p, title):
    set_pending(p)
    clear_pred_artists()
    draw_pending()
    set_title(title)
    redraw()

#van-e eleg adat
def enough_data():
    if len(t_all) < 5:
        print("Kevés hőmérséklet-adat (min. 5 pont kell)!")
    return len(t_all) >= 5

#kovetkezo ora becslese
def predict_next():
    if not enough_data():
        return
    times, vals = t_all, ys[0]
    step = measurement_interval(times)
    target = times[-1] + step
    while target < datetime.now().astimezone() - TOL:
        target += step
    h = target.hour + target.minute / 60
    print(f"\n[KÖVETKEZŐ MÉRÉS] Időköz: ~{step.total_seconds() / 60:.0f} perc | "
          f"Utolsó: {vals[-1]:.2f}{U} ({times[-1]:%H:%M}) | Várható: ~{target:%H:%M} (h = {h:.2f})")

    X, y = next_pairs(times, vals, step)
    if len(y) < MIN_PAIRS:
        print(f"  Kevés egymást követő mérés ({len(y)} < {MIN_PAIRS}), napszakos modellt használok.")
        b, w = daily_model(times, vals)
        x_new, names, lhs = harmonics([target])[0], HARM, "T(h)"
    else:
        k = max(3, len(y) // 5)
        b, w = fit_ridge(X[:-k], y[:-k], NEXT_ALPHA)
        mae_model, mae_naive = np.mean(np.abs(b + X[-k:] @ w - y[-k:])), np.mean(np.abs(X[-k:, 0] - y[-k:]))
        print(f"  Visszamérés ({k} mérés): modell átl. hibája {mae_model:.2f}{U}, "
              f"'nem változik' becslésé {mae_naive:.2f}{U}")
        if mae_model <= mae_naive:
            b, w = fit_ridge(X, y, NEXT_ALPHA)
        else:
            print("A modell most nem jobb a naiv becslésnél, ezért azt használom.")
            b, w = 0.0, np.array([1.0, 0, 0, 0, 0])
        x_new, names, lhs = np.r_[vals[-1], harmonics([target])[0]], ["T(most)"] + HARM, "T(következő)"

    p = float(np.clip(b + x_new @ w, *TEMP_CLIP))
    form = formula(lhs, b, names, w)
    print(f"  Képlet: {form}\n  Behelyettesítve: T(most) = {vals[-1]:.2f}, h = {h:.2f} -> {p:.2f}{U}")
    start_prediction(dict(target=target, after=times[-1], val=p, label="Trend (következő mérés)", formula=form),
                     f"Következő mérés becslése (~{target:%H:%M}): {p:.2f}{U}")

#holnapi adat becslese
def predict_tomorrow():
    if not enough_data():
        return
    now = datetime.now().astimezone()
    target = now + timedelta(hours=24)
    b, w = daily_model(t_all, ys[0])
    p = float(np.clip(b + harmonics([target])[0] @ w, *TEMP_CLIP))
    form = formula("T(h)", b, HARM, w)

    day = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    s = np.clip(b + harmonics([day + timedelta(hours=i) for i in range(24)]) @ w, *TEMP_CLIP)
    title = f"Holnap ({day:%Y-%m-%d}) — Hőmérséklet: {s.min():.1f}–{s.max():.1f}{U}, átl. {s.mean():.1f}{U}"
    print(f"\n[ELŐREJELZÉS +24 óra] Jelenlegi: {ys[0][-1]:.2f}{U}\n  Képlet: {form}   (h = a nap órája)\n"
          f"  Behelyettesítve: h = {target.hour + target.minute / 60:.2f} -> {p:.2f}{U}\n  {title}")
    start_prediction(dict(target=target, after=target - TOL, val=p, label="Trend (+24 óra)", formula=form), title)

#becslesek torlese
def clear_predictions():
    clear_pred_artists()
    set_pending(None)
    set_title()
    redraw()
    print("Az eddigi becslések törölve.")

#meres leallitasa, elinditasa
def set_running(state):
    global is_running
    is_running = state
    print("Mérés " + ("újraindítva." if state else "szüneteltetve."))

#gombok
buttons = []
for x, w, label, color, cb in [
    (0.03, 0.17, "Mérés Indítása", "lightgreen", lambda e: set_running(True)),
    (0.22, 0.17, "Mérés Leállítása", "lightcoral", lambda e: set_running(False)),
    (0.41, 0.17, "Következő mérés", "gold", lambda e: predict_next()),
    (0.60, 0.17, "Holnapi hőmérséklet", "skyblue", lambda e: predict_tomorrow()),
    (0.79, 0.18, "Becslések törlése", "lightgray", lambda e: clear_predictions()),
]:
    buttons.append(Button(plt.axes([x, 0.03, w, 0.05]), label, color=color, hovercolor="0.8"))
    buttons[-1].on_clicked(cb)


load_history()
restore_pending()
last_poll = time.time()

try:
    while plt.fignum_exists(fig.number):
        if is_running and time.time() - last_poll >= FETCH_INTERVAL:
            last_poll = time.time()
            poll()
        plt.pause(0.2)
except KeyboardInterrupt:
    print("\nProgram leállítva.")