#pragma once
#define WIFI_SSID "YOUR_WIFI_NAME"
#define WIFI_PASSWORD "YOUR_WIFI_PASSWORD"
#define DEVICE_ID "urjagrid-01"
// Must match DEVICE_TOKEN in the backend environment. Never use a database key.
#define DEVICE_TOKEN "REPLACE_WITH_THE_SAME_RANDOM_DEVICE_SECRET_AS_THE_BACKEND"
#define SERVER_BASE_URL "https://YOUR_PROJECT.vercel.app"

// Install the trusted root PEM for the actual HTTPS endpoint. Do not call
// setInsecure(). Vercel's certificate chain can vary; verify your endpoint.
static const char SERVER_ROOT_CA[] = R"PEM(
PASTE_THE_ENDPOINTS_TRUSTED_ROOT_CA_PEM_HERE
)PEM";

// Development only: a literal private LAN IPv4 HTTP endpoint, NOT internet HTTP.
// Example SERVER_BASE_URL: http://192.168.1.10:8000
#define ALLOW_LOCAL_HTTP false

// Leave false until wiring/protection, 3.3V relay compatibility, USB power path
// and full battery-charge-voltage tolerance of both loads have been checked.
#define ENABLE_LOAD_OUTPUTS false

// Provisional settings. Change only against verified hardware specifications.
#define MAX_LOAD_SUPPLY_V 12.8f
#define BULB_CURRENT_LIMIT_A 1.5f
#define FAN_CURRENT_LIMIT_A 0.3f
