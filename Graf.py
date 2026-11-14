import os
import time
from datetime import datetime, timedelta

import matplotlib.pyplot as plt
import numpy as np
import requests
from matplotlib.ticker import MultipleLocator
from matplotlib.widgets import Button
from dotenv import load_dotenv

load_dotenv()

AIO_USERNAME = "atka"
AIO_KEY = os.environ.get("AIO_KEY")
if not AIO_KEY:
    raise RuntimeError("Hiányzik az AIO_KEY környezeti változó!")
BASE_URL = f"https://io.adafruit.com/api/v2/{AIO_USERNAME}/feeds"

FETCH_INTERVAL = 60
HISTORY_LIMIT = 1000
VISIBLE_POINTS = 10
ERROR_TOLERANCE = timedelta(minutes=5)

FEEDS = [
    dict(key="homerseklet", name="Hőmérséklet", unit="°C", ylim=(-10, 50), clip=(-40, 60), color="blue"),
    dict(key="paratartalom", name="Páratartalom", unit="%", ylim=(0, 100), clip=(0, 100), color="green"),
    dict(key="voc", name="VOC Index", unit="", ylim=(0, 500), clip=(1, 500), color="red"),
]

session = requests.Session()
session.headers["X-AIO-Key"] = AIO_KEY


def fetch(feed_key, limit):
    try:
        res = session.get(f"{BASE_URL}/{feed_key}/data", params={"limit": limit}, timeout=10)
        res.raise_for_status()
    except requests.RequestException as e:
        print(f"[Hiba] {feed_key}: {e}")
        return []
    return [
        (datetime.fromisoformat(d["created_at"].replace("Z", "+00:00")).astimezone(), float(d["value"]), d["id"])
        for d in reversed(res.json())
    ]


def fetch_rows(limit):
    series = [fetch(f["key"], limit) for f in FEEDS]
    n = min(map(len, series))
    if n == 0:
        return []
    series = [s[-n:] for s in series]
    return [
        (rows[0][0], {f["key"]: r[1] for f, r in zip(FEEDS, rows)}, rows[0][2])
        for rows in zip(*series)
    ]


def features(times):
    w = 2 * np.pi * np.array([t.hour + t.minute / 60 for t in times]) / 24
    return np.column_stack([np.sin(w), np.cos(w), np.sin(2 * w), np.cos(2 * w)])


def ridge_predict(times, values, targets, alpha=50.0):
    X, y = features(times), np.asarray(values)
    Xm, ym = X.mean(axis=0), y.mean()
    Xc = X - Xm
    w = np.linalg.solve(Xc.T @ Xc + alpha * np.eye(X.shape[1]), Xc.T @ (y - ym))
    return features(targets) @ w + (ym - Xm @ w)


x_time = []
data = {f["key"]: [] for f in FEEDS}
base_ts = None
last_id = None
is_running = True
pending = None
trend_artists = []


plt.ion()
fig, axes = plt.subplots(3, 1, figsize=(11, 8), sharex=True)
plt.subplots_adjust(bottom=0.18, top=0.90)
fig.suptitle("Valós idejű szenzoradatok", fontsize=14)

for ax, f in zip(axes, FEEDS):
    f["ax"] = ax
    ax.set_ylim(*f["ylim"])
    ax.set_ylabel(f"{f['name']} ({f['unit']})" if f["unit"] else f["name"])
    ax.grid(True)
    (f["line"],) = ax.plot([], [], marker="o", color=f["color"], label=f"Mért {f['name']}")
    ax.legend(loc="upper left")

axes[-1].set_xlabel("Idő (óra)")
axes[-1].xaxis.set_major_locator(MultipleLocator(1.0))


def hrs(dt):
    return (dt - base_ts).total_seconds() / 3600


def add_point(dt, vals):
    global base_ts
    if base_ts is None:
        base_ts = dt
    x_time.append(hrs(dt))
    for f in FEEDS:
        data[f["key"]].append(vals[f["key"]])
    print(f"[{dt:%H:%M:%S} | {x_time[-1]:.2f} óra] "
          + " | ".join(f"{f['name']}: {vals[f['key']]:.2f}{f['unit']}" for f in FEEDS))


def redraw():
    for f in FEEDS:
        f["line"].set_data(x_time, data[f["key"]])
    if x_time:
        start = x_time[max(0, len(x_time) - VISIBLE_POINTS)]
        span = x_time[-1] - start
        if span < 1.0:
            axes[-1].xaxis.set_major_locator(plt.AutoLocator())
        else:
            axes[-1].xaxis.set_major_locator(MultipleLocator(1.0))
        axes[-1].set_xlim(start, x_time[-1] + max(30 / 3600.0, span * 0.1))
    fig.canvas.draw_idle()


def clear_trends():
    for a in trend_artists:
        a.remove()
    trend_artists.clear()
    for f in FEEDS:
        f["ax"].legend(loc="upper left")


def load_history(count=10):
    global last_id
    print(f"[INFO] Az előző {count} adatpont betöltése...")
    rows = fetch_rows(count)
    if not rows:
        print("[FIGYELMEZTETÉS] Nem sikerült korábbi adatokat betölteni.")
        return
    for dt, vals, _ in rows:
        add_point(dt, vals)
    last_id = rows[-1][2]
    redraw()


def poll():
    global last_id
    rows = fetch_rows(1)
    if not rows:
        return
    dt, vals, rid = rows[0]
    if rid == last_id:
        print("[Várakozás] Még nincs újabb adat...")
        return
    last_id = rid
    add_point(dt, vals)
    if pending and dt >= pending["target"] - ERROR_TOLERANCE:
        draw_error(x_time[-1], vals)
    redraw()


def predict(hours):
    global is_running, pending
    is_running = False
    clear_trends()

    if len(x_time) < 2:
        print("[FIGYELMEZTETÉS] Legalább 2 mérési pont kell!")
        return

    history = {f["key"]: fetch(f["key"], HISTORY_LIMIT) for f in FEEDS}
    if any(len(h) < 5 for h in history.values()):
        print("[FIGYELMEZTETÉS] Kevés történeti adat (min. 5 pont kell)!")
        return

    now = datetime.now().astimezone()
    target = now + timedelta(hours=hours)
    x_pred = hrs(target)
    preds = {}

    print(f"\n--- ELŐREJELZÉS (+{hours} óra, {target:%m-%d %H:%M}) ---")
    for f in FEEDS:
        times, vals, _ = zip(*history[f["key"]])
        p = float(np.clip(ridge_predict(times, vals, [target])[0], *f["clip"]))
        preds[f["key"]] = p
        last = data[f["key"]][-1]
        print(f"[{f['name']}] Jelenlegi: {last:.2f}{f['unit']} -> {target:%H:%M}: {p:.2f}{f['unit']}")

        ax = f["ax"]
        trend_artists.extend(ax.plot([x_time[-1], x_pred], [last, p], "m--", lw=2, label=f"Trend (+{hours} óra)"))
        ax.plot(x_pred, p, "r*", ms=12, zorder=5, label=f"Jósolt ({target:%H:%M}): {p:.2f}{f['unit']}")
        ax.legend(loc="upper left")

    pending = {"target": target, "x": x_pred, "vals": preds}

    title = daily_summary(history, now) if hours >= 24 else f"Óránkénti becslés ({target:%H:%M})"
    fig.suptitle(title, fontsize=11, fontweight="bold")
    
    axes[-1].xaxis.set_major_locator(MultipleLocator(2.0 if hours >= 24 else 1.0))
    axes[-1].set_xlim(x_time[0], x_pred + max(300 / 3600.0, 0.05 * (x_pred - x_time[0])))
    fig.canvas.draw_idle()


def daily_summary(history, now):
    start = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    hours = [start + timedelta(hours=i) for i in range(24)]
    parts = []
    print(f"\n=== HOLNAPI ÖSSZESÍTÉS ({start:%Y-%m-%d}) ===")
    for f in FEEDS[:2]:
        times, vals, _ = zip(*history[f["key"]])
        s = np.clip(ridge_predict(times, vals, hours), *f["clip"])
        u = f["unit"]
        print(f"  {f['name']}: Min {s.min():.1f}{u} | Max {s.max():.1f}{u} | Átlag {s.mean():.1f}{u}")
        parts.append(f"{f['name']}: {s.min():.1f}–{s.max():.1f}{u}, átl. {s.mean():.1f}{u}")
    return f"Holnap ({start:%Y-%m-%d}) — " + "  |  ".join(parts)


def draw_error(x_actual, actual):
    global pending
    print("\n--- PREDIKCIÓS HIBA ---")
    for f in FEEDS:
        p, a = pending["vals"][f["key"]], actual[f["key"]]
        err = a - p
        print(f"[{f['name']}] Jósolt: {p:.2f} | Tényleges: {a:.2f} | Hiba: {err:+.2f}{f['unit']}")
        ax = f["ax"]
        ax.plot([pending["x"], x_actual], [p, a], color="darkorange", ls=":", lw=2.5, zorder=4)
        ax.annotate(
            f"Hiba: {err:+.2f}{f['unit']}",
            xy=((pending["x"] + x_actual) / 2, (p + a) / 2),
            xytext=(8, 0), textcoords="offset points",
            color="darkred", fontweight="bold", fontsize=9,
            bbox=dict(boxstyle="round,pad=0.2", fc="lightyellow", ec="orange", alpha=0.9),
        )
    pending = None


def set_running(state):
    global is_running
    is_running = state
    if state:
        clear_trends()
    print("[INFO] Mérés " + ("újraindítva." if state else "szüneteltetve."))


buttons = []
for pos, label, color, callback in [
    ([0.06, 0.03, 0.19, 0.05], "Mérés Indítása", "lightgreen", lambda e: set_running(True)),
    ([0.27, 0.03, 0.19, 0.05], "Mérés Leállítása", "lightcoral", lambda e: set_running(False)),
    ([0.48, 0.03, 0.22, 0.05], "Következő érték becslése", "gold", lambda e: predict(1)),
    ([0.72, 0.03, 0.22, 0.05], "Holnapi Becslés", "skyblue", lambda e: predict(24)),
]:
    btn = Button(plt.axes(pos), label, color=color, hovercolor="0.8")
    btn.on_clicked(callback)
    buttons.append(btn)


load_history(count=10)
last_poll = 0

try:
    while plt.fignum_exists(fig.number):
        if is_running and time.time() - last_poll >= FETCH_INTERVAL:
            last_poll = time.time()
            poll()
        plt.pause(0.2)
except KeyboardInterrupt:
    print("\nProgram leállítva.")