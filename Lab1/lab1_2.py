# Погода по городу с авторизацией по API-ключу

import json
import ssl
import urllib.error
import urllib.parse
import urllib.request

import truststore

# Настройки
API_KEY = "943077c6325b3188d42accaadf3da09e"
CITY = "Perm"
UNITS = "metric"
LANG = "ru"
OUTPUT = "weather.json"

BASE_URL = "https://api.openweathermap.org/data/2.5"
SSL_CONTEXT = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)


def request(endpoint, params):
    params["appid"] = API_KEY
    url = f"{BASE_URL}/{endpoint}?" + urllib.parse.urlencode(params)

    try:
        with urllib.request.urlopen(url, timeout=15, context=SSL_CONTEXT) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as e:
        body = json.loads(e.read())
        raise RuntimeError(f"{e.code}: {body.get('message')}") from None


def fetch_current(city):
    return request("weather", {"q": city, "units": UNITS, "lang": LANG})


def fetch_forecast(city):
    return request("forecast", {"q": city, "units": UNITS, "lang": LANG})


def main():
    result = {
        "city": CITY,
        "units": UNITS,
        "current": fetch_current(CITY),
        "forecast": fetch_forecast(CITY),
    }

    with open(OUTPUT, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()