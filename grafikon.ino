#include <Wire.h>
#include <WiFi.h>
#include <WebServer.h>

const char* ssid = "Telekom-6jv13P";
const char* password = "eet46t8pfm3e";

WebServer server(80);

void handleRoot() {
  String html = "<html><body>";
  html += "<h1>ESP32-S3 Szenzor Adatok</h1>";
  html += "<p>A mért adatok az <code>/api/data</code> végponton érhetőek el JSON formátumban.</p>";
  html += "</body></html>";
  
  server.send(200, "text/html", html);
}

void handleData() {
  int homerseklet = random(-10, 51);    // -10 °C és +50 °C között
  int paratartalom = random(20, 95);    // HTU21 páratartalom % (20-95%)
  int levegominoseg = random(0, 501);   // VOC index (0-500)

  String json = "{";
  json += "\"homerseklet\": " + String(homerseklet) + ",";
  json += "\"paratartalom\": " + String(paratartalom) + ",";
  json += "\"levegominoseg\": " + String(levegominoseg);
  json += "}";
  
  server.send(200, "application/json", json);
}

void setup() {
  Serial.begin(115200);

  // Csatlakozás a Wi-Fi-re
  WiFi.begin(ssid, password);
  Serial.print("Csatlakozas a Wi-Fi-hez...");
  while (WiFi.status() != WL_CONNECTED) {
    delay(500);
    Serial.print(".");
  }
  
  Serial.println("\nCsatlakozva!");
  Serial.print("Az ESP32 IP címe: ");
  Serial.println(WiFi.localIP());

  server.on("/", handleRoot);
  server.on("/api/data", handleData);

  server.begin();
  Serial.println("Webszerver elindult!");
}

void loop() {
  server.handleClient();
}