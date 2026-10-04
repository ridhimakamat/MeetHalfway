import requests
q = '[out:json][timeout:25];node["amenity"="cafe"](around:1500,15.4,73.9);out 3;'
for url in ["https://overpass-api.de/api/interpreter",
            "https://overpass.kumi.systems/api/interpreter"]:
    try:
        r = requests.post(url, data={"data": q},
                          headers={"User-Agent": "meethalfway_app"}, timeout=35)
        print(url, r.status_code, r.text[:120].replace("\n", " "))
    except Exception as e:
        print(url, type(e).__name__, e)