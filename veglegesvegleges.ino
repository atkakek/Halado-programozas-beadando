#include <Wire.h>
#include <WiFi.h>
#include <HTTPClient.h>
#include <Adafruit_HTU21DF.h>
#include <Adafruit_SGP40.h>
#include "esp_sleep.h"
#include "secrets.h"

const char* ssid     = WIFI_SSID;
const char* password = WIFI_PASS;

#define HTU_SDA 8
#define HTU_SCL 9
#define SGP_SDA 10
#define SGP_SCL 11

const uint32_t SAMPLE_MS            = 1000;
const uint32_t HTU_EVERY_N_SAMPLES  = 10;
const uint32_t FIRST_SEND_DELAY_S   = 60;
const uint32_t SEND_INTERVAL_S      = 3600;
const uint32_t KEEPALIVE_INTERVAL_S = 10;
const uint32_t KEEPALIVE_PULSE_MS   = 300;
const uint32_t WIFI_TIMEOUT_MS      = 15000;

const bool CONSTANT_LOAD = true;

Adafruit_HTU21DF htu;
Adafruit_SGP40   sgp;
bool htuOk = false, sgpOk = false;

float   temp = 25.0, hum = 50.0;
int32_t vocIndex = 0;
uint32_t sampleCount = 0;

uint32_t nextTickMs, nextSendMs, lastKeepAliveMs;

void readHTU() {
  if (!htuOk) return;
  float t = htu.readTemperature();
  float h = htu.readHumidity();
  if (!isnan(t) && !isnan(h)) {
    temp = t;
    hum  = h;
  }
}

void sampleOnce() {
  if (sampleCount % HTU_EVERY_N_SAMPLES == 0) readHTU();
  if (sgpOk) vocIndex = sgp.measureVocIndex(temp, hum);
  sampleCount++;
}

void waitNextTick(bool canSleep) {
  nextTickMs += SAMPLE_MS;
  int32_t remaining = (int32_t)(nextTickMs - millis());
  if (remaining <= 0) {
    nextTickMs = millis();
    return;
  }
  if (canSleep && remaining > 20) {
    Serial.flush();
    esp_sleep_enable_timer_wakeup((uint64_t)remaining * 1000ULL);
    esp_light_sleep_start();
  } else {
    delay(remaining);
  }
}

void keepAlivePulse() {
  WiFi.mode(WIFI_STA);
  delay(KEEPALIVE_PULSE_MS);
  WiFi.mode(WIFI_OFF);
}

bool sendToAdafruit(const char* feedKey, float value) {
  HTTPClient http;
  String url = String("https://io.adafruit.com/api/v2/") + AIO_USERNAME + "/feeds/" + feedKey + "/data";
  http.begin(url);
  http.addHeader("Content-Type", "application/json");
  http.addHeader("X-AIO-Key", AIO_KEY);
  int code = http.POST("{\"value\": " + String(value, 2) + "}");
  http.end();

  bool ok = (code == 200 || code == 201);
  Serial.printf("[Adafruit IO] %s = %.2f -> %s (%d)\n", feedKey, value, ok ? "OK" : "HIBA", code);
  return ok;
}

void connectAndSend() {
  Serial.printf("KÜLDÉS -> %.2f °C | %.2f %% | VOC: %ld\n", temp, hum, (long)vocIndex);

  if (WiFi.status() != WL_CONNECTED) {
    WiFi.mode(WIFI_STA);
    if (CONSTANT_LOAD) WiFi.setSleep(false);
    WiFi.begin(ssid, password);
  }

  uint32_t start = millis();
  while (WiFi.status() != WL_CONNECTED && millis() - start < WIFI_TIMEOUT_MS) {
    sampleOnce();
    waitNextTick(false);
  }

  if (WiFi.status() == WL_CONNECTED) {
    if (htuOk) {
      sendToAdafruit("homerseklet", temp);
      sendToAdafruit("paratartalom", hum);
    }
    if (sgpOk) sendToAdafruit("voc", vocIndex);
  } else {
    Serial.println("HIBA: Wi-Fi csatlakozás sikertelen, a következő ciklusban újrapróbálja.");
  }

  if (!CONSTANT_LOAD) {
    WiFi.disconnect(true);
    WiFi.mode(WIFI_OFF);
  }
  lastKeepAliveMs = millis();
}

void setup() {
  Serial.begin(115200);
  delay(500);
  Serial.println("\n--- ESP32-S3 INDUL ---");

  WiFi.persistent(false);
  if (CONSTANT_LOAD) {
    WiFi.mode(WIFI_STA);
    WiFi.setSleep(false);
    WiFi.begin(ssid, password);
  } else {
    WiFi.mode(WIFI_OFF);
  }

  Wire.begin(HTU_SDA, HTU_SCL);
  Wire1.begin(SGP_SDA, SGP_SCL);

  htuOk = htu.begin(&Wire);
  Serial.println(htuOk ? "OK: HTU21D kész." : "HIBA: HTU21D nem található (SDA=8, SCL=9)!");
  sgpOk = sgp.begin(&Wire1);
  Serial.println(sgpOk ? "OK: SGP40 kész." : "HIBA: SGP40 nem található (SDA=10, SCL=11)!");

  readHTU();

  uint32_t now = millis();
  nextTickMs      = now;
  nextSendMs      = now + FIRST_SEND_DELAY_S * 1000UL;
  lastKeepAliveMs = now;
}

void loop() {
  sampleOnce();

  uint32_t now = millis();
  if ((int32_t)(now - nextSendMs) >= 0) {
    connectAndSend();
    nextSendMs += SEND_INTERVAL_S * 1000UL;
  } else if (!CONSTANT_LOAD && now - lastKeepAliveMs >= KEEPALIVE_INTERVAL_S * 1000UL) {
    keepAlivePulse();
    lastKeepAliveMs = now;
  }

  waitNextTick(!CONSTANT_LOAD);
}
