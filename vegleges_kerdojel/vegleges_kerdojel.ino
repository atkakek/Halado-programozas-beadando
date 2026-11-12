#include <Wire.h>
#include <WiFi.h>
#include <HTTPClient.h>
#include <Adafruit_HTU21DF.h>
#include <Adafruit_SGP40.h>

// --- Wi-Fi Beállítások ---
const char* ssid = "Telekom-6jv13P";
const char* password = "eet46t8pfm3e";

// --- Adafruit IO Beállítások ---
#define AIO_USERNAME "A_TE_ADAFRUIT_USERNAMED"
#define AIO_KEY      "A_TE_ADAFRUIT_IO_KEYD"

// --- Mélyalvás (Deep Sleep) Beállítások ---
#define uS_TO_S_FACTOR 1000000ULL
#define TIME_TO_SLEEP  7200  // 2 óra = 7200 másodperc

// --- I2C Lábak (ESP32-S3 esetén módosítható, pl. SDA=8, SCL=9 vagy SDA=21, SCL=22) ---
#define I2C_SDA 8
#define I2C_SCL 9

Adafruit_HTU21DF htu = Adafruit_HTU21DF();
Adafruit_SGP40 sgp   = Adafruit_SGP40();

void sendToAdafruit(String feedKey, float value) {
  if (WiFi.status() != WL_CONNECTED) return;

  HTTPClient http;
  String url = "https://io.adafruit.com/api/v2/" + String(AIO_USERNAME) + "/feeds/" + feedKey + "/data";

  http.begin(url);
  http.addHeader("Content-Type", "application/json");
  http.addHeader("X-AIO-Key", AIO_KEY);

  // created_at nélkül küldjük, így az Adafruit IO adja hozzá a pontos szerveridőt
  String payload = "{\"value\": " + String(value, 2) + "}";
  int httpCode = http.POST(payload);

  if (httpCode == 201) {
    Serial.println("[Adafruit IO] " + feedKey + " elküldve: " + String(value, 2));
  } else {
    Serial.println("[Adafruit IO Hiba] " + feedKey + " Status: " + String(httpCode));
  }
  http.end();
}

void setup() {
  Serial.begin(115200);
  delay(500);

  // 1. I2C Busz és Szenzorok inicializálása
  Wire.begin(I2C_SDA, I2C_SCL);

  if (!htu.begin()) {
    Serial.println("Hiba: HTU21D szenzor nem található!");
  }
  if (!sgp.begin()) {
    Serial.println("Hiba: SGP40 szenzor nem található!");
  }

  // 2. Wi-Fi csatlakozás elindítása a háttérben (a bemelegedési idő alatt felkapcsol)
  WiFi.begin(ssid, password);

  // 3. HTU21D beolvasása (Hőmérséklet & Páratartalom)
  float temp = htu.readTemperature();
  float hum = htu.readHumidity();

  // Érvényességi ellenőrzés (ha a szenzor hibás értéket adna)
  if (isnan(temp) || isnan(hum)) {
    Serial.println("Hiba a HTU21D adatok olvasásakor!");
    temp = 25.0; // Tartalék érték a kompenzációhoz
    hum = 50.0;
  }

  // 4. SGP40 Bemelegedés és Stabilizálás (10 másodperc várakozás a pontos VOC indexhez)
  Serial.println("SGP40 melegedése és adatok stabilizálása (10 mp)...");
  uint16_t voc_index = 0;
  for (int i = 0; i < 10; i++) {
    // A mérést többször lefuttatjuk a bemelegedés alatt, átadva a meglévő T és RH értékeket
    voc_index = sgp.measureVocIndex(temp, hum);
    delay(1000);
  }

  // 5. Megvárjuk, míg a Wi-Fi teljesen felcsatlakozik
  int timeout = 0;
  while (WiFi.status() != WL_CONNECTED && timeout < 20) {
    delay(500);
    Serial.print(".");
    timeout++;
  }
  Serial.println();

  // 6. Adatok elküldése az Adafruit IO-ra
  if (WiFi.status() == WL_CONNECTED) {
    Serial.println("Mért adatok:");
    Serial.printf(" Hőmérséklet: %.2f °C\n Páratartalom: %.2f %%\n VOC Index: %d\n", temp, hum, voc_index);

    sendToAdafruit("homerseklet", temp);
    sendToAdafruit("paratartalom", hum);
    sendToAdafruit("voc", voc_index);
  } else {
    Serial.println("Wi-Fi kapcsolódási hiba, adatok nem kerültek elküldésre.");
  }

  // 7. Mélyalvás elindítása 2 órára
  Serial.println("ESP32 mélyalvásba lép 2 órára...");
  Serial.flush();
  esp_sleep_enable_timer_wakeup(TIME_TO_SLEEP * uS_TO_S_FACTOR);
  esp_deep_sleep_start();
}

void loop() {
  // Deep sleep miatt a loop nem fut le
}