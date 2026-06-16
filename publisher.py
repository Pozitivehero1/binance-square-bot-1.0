import requests
import os

def publish(text):
    url = "https://www.binance.com/bapi/composite/v1/public/pgc/openApi/content/add"

    headers = {
        "X-Square-OpenAPI-Key": os.getenv("SQUARE_API"),
        "clienttype": "binanceSkill",
        "Content-Type": "application/json"
    }

    payload = {
        "bodyTextOnly": text
    }

    r = requests.post(
        url,
        headers=headers,
        json=payload,
        timeout=30
    )

    print("STATUS:", r.status_code)
    print("RESPONSE:")
    print(r.text)

    return r
