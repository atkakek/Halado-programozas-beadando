import time
from datetime import datetime, timezone
from matplotlib.widgets import Button
import matplotlib.pyplot as plt
import numpy as np
import requests
from sklearn.linear_model import LinearRegression
from sklearn.preprocessing import PolynomialFeatures


# --- ESP32 és Adafruit IO Beállítások ---
ESP_IP = "http://192.168.1.84"

AIO_USERNAME = ""
AIO_KEY = ""

FEED_TEMP = "homerseklet"
FEED_HUM = "paratartalom"
FEED_AIR = "voc"


def send_to_adafruit_io(feed_key, value, timestamp_str):
    url = (
        f"https://io.adafruit.com/api/v2/{AIO_USERNAME}/feeds/{feed_key}/data"
    )
    headers = {"X-AIO-Key": AIO_KEY, "Content-Type": "application/json"}
    payload = {"value": value, "created_at": timestamp_str}

    try:
        res = requests.post(url, json=payload, headers=headers, timeout=5)
        if res.status_code == 201:
            print(f"[Adafruit IO] {feed_key} elküldve: {value}")
        else:
            print(f"[Adafruit IO Hiba] Status: {res.status_code} - {res.text}")
    except Exception as e:
        print(f"[Adafruit IO Hiba]: {e}")


x_time = []
y_temp = []
y_hum = []
y_air = []

# Mérés állapota
is_running = True

plt.ion()
fig, (ax1, ax2, ax3) = plt.subplots(3, 1, figsize=(11, 8), sharex=True)
fig.suptitle("ESP32 Szenzoradatok & Scikit-Learn Predikció", fontsize=14)

plt.subplots_adjust(bottom=0.18)

# 1. Hőmérséklet grafikon
ax1.set_ylim(-10, 50)
ax1.set_ylabel("Hőmérséklet (°C)")
ax1.grid(True)
(line_temp,) = ax1.plot(
    [], [], marker="o", color="blue", linestyle="-", label="Mért Hőmérséklet"
)

# 2. Páratartalom grafikon
ax2.set_ylim(0, 100)
ax2.set_ylabel("Páratartalom (%)")
ax2.grid(True)
(line_hum,) = ax2.plot(
    [], [], marker="o", color="green", linestyle="-", label="Mért Páratartalom"
)

# 3. Levegőminőség grafikon
ax3.set_ylim(0, 500)
ax3.set_xlabel("Idő (másodperc)")
ax3.set_ylabel("VOC Index")
ax3.grid(True)
(line_air,) = ax3.plot(
    [], [], marker="o", color="red", linestyle="-", label="Mért VOC"
)

# Gombok elhelyezése: [left, bottom, width, height]
ax_start = plt.axes([0.12, 0.03, 0.22, 0.05])
ax_stop = plt.axes([0.38, 0.03, 0.22, 0.05])
ax_predict = plt.axes([0.64, 0.03, 0.24, 0.05])

btn_start = Button(
    ax_start, "Mérés Indítása", color="lightgreen", hovercolor="0.8"
)
btn_stop = Button(
    ax_stop, "Mérés Leállítása", color="lightcoral", hovercolor="0.8"
)
btn_predict = Button(
    ax_predict, "Illesztés & Becslés", color="gold", hovercolor="0.8"
)

fitted_lines = []
pred_points = []


def clear_predictions():
    """Eltávolítja az előző illesztett görbéket és pontokat a grafikonról."""
    global fitted_lines, pred_points
    for l in fitted_lines:
        l.remove()
    for p in pred_points:
        p.remove()
    fitted_lines.clear()
    pred_points.clear()


def start_measurement(event):
    global is_running
    if not is_running:
        is_running = True
        clear_predictions()
        print("[INFO] Mérés újraindítva.")


def stop_measurement(event):
    global is_running
    if is_running:
        is_running = False
        print("[INFO] Mérés szüneteltetve.")


def fit_and_predict(event):
    global is_running
    is_running = False
    clear_predictions()

    if len(x_time) < 4:
        print(
            "[FIGYELMEZTETÉS] Túl kevés adat áll rendelkezésre (min. 4 pont kell)!"
        )
        return

    print("\n--- SCIKIT-LEARN FÜGGVÉNYILLESZTÉS ÉS PREDIKCIÓ ---")

    X = np.array(x_time).reshape(-1, 1)
    next_time = np.array([[x_time[-1] + 10]])  # A következő 10 mp becslése
    X_dense = np.linspace(x_time[0], next_time[0][0], 100).reshape(
        -1, 1
    )  # Sima görbéhez rajzolt x értékek

    # 2. fokú polinomiális transzformáció (y = a*x^2 + b*x + c)
    poly = PolynomialFeatures(degree=2)
    X_poly = poly.fit_transform(X)
    X_dense_poly = poly.transform(X_dense)
    next_time_poly = poly.transform(next_time)

    datasets = [
        (ax1, y_temp, "Hőmérséklet"),
        (ax2, y_hum, "Páratartalom"),
        (ax3, y_air, "VOC Index"),
    ]

    for ax, y_data, name in datasets:
        y = np.array(y_data)

        # Scikit-learn Modell tanítása
        model = LinearRegression()
        model.fit(X_poly, y)

        # Kiszámított együtthatók (c, b, a)
        c = model.intercept_
        b, a = model.coef_[1], model.coef_[2]
        r2 = model.score(X_poly, y)

        # Becsült érték kiszámítása a következő időpontra
        y_pred_next = model.predict(next_time_poly)[0]
        y_dense_pred = model.predict(X_dense_poly)

        print(f"[{name}]")
        print(f"  Függvény: f(t) = ({a:.4f})*t² + ({b:.4f})*t + ({c:.2f})")
        print(f"  Illeszkedés (R²): {r2:.3f}")
        print(f"  Becsült érték t={next_time[0][0]}s-nál: {y_pred_next:.2f}\n")

        (f_line,) = ax.plot(
            X_dense,
            y_dense_pred,
            "m--",
            linewidth=2,
            label=f"Illesztett f(t) [R²={r2:.2f}]",
        )
        (p_point,) = ax.plot(
            next_time[0][0],
            y_pred_next,
            "r*",
            markersize=12,
            label=f"Becsült: {y_pred_next:.1f}",
        )

        fitted_lines.append(f_line)
        pred_points.append(p_point)
        ax.legend(loc="upper left")

    ax3.set_xlim(x_time[0], next_time[0][0] + 5)
    fig.canvas.draw_idle()


btn_start.on_clicked(start_measurement)
btn_stop.on_clicked(stop_measurement)
btn_predict.on_clicked(fit_and_predict)

ax1.legend(loc="upper left")
ax2.legend(loc="upper left")
ax3.legend(loc="upper left")

start_time = time.time()
last_request_time = 0
FETCH_INTERVAL = 1

try:
    while plt.fignum_exists(fig.number):
        current_time = time.time()

        if is_running and (current_time - last_request_time >= FETCH_INTERVAL):
            last_request_time = current_time

            try:
                response = requests.get(f"{ESP_IP}/api/data", timeout=5)

                if response.status_code == 200:
                    data = response.json()
                    temp = data["homerseklet"]
                    hum = data["paratartalom"]
                    air = data["levegominoseg"]

                    # --- adafruit io adatküldés ---
                    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:00:00Z")
                    send_to_adafruit_io(FEED_TEMP, temp, now_str)
                    send_to_adafruit_io(FEED_HUM, hum, now_str)
                    send_to_adafruit_io(FEED_AIR, air, now_str)

                    # --- GRAFIKON FRISSÍTÉS ÉS PRINT ---
                    elapsed_time = int(current_time - start_time)
                    print(
                        f"[{elapsed_time}s] Hőmérséklet: {temp} °C | Páratartalom: {hum}% | VOC Index: {air}"
                    )

                    x_time.append(elapsed_time)
                    y_temp.append(temp)
                    y_hum.append(hum)
                    y_air.append(air)

                    if len(x_time) > 10:
                        ax3.set_xlim(x_time[-10], x_time[-1] + 10)
                    else:
                        ax3.set_xlim(0, max(30, elapsed_time + 10))

                    line_temp.set_xdata(x_time)
                    line_temp.set_ydata(y_temp)

                    line_hum.set_xdata(x_time)
                    line_hum.set_ydata(y_hum)

                    line_air.set_xdata(x_time)
                    line_air.set_ydata(y_air)

                    fig.canvas.draw_idle()
                else:
                    print(f"Hiba: Status code {response.status_code}")

            except Exception as e:
                print(f"Kapcsolódási hiba: {e}")

        plt.pause(0.1)

except KeyboardInterrupt:
    print("\nProgram leállítva.")