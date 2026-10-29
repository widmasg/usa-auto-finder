
import datetime as dt
import json
import os
import pathlib
import urllib.parse
import urllib.request

ROOT = pathlib.Path(__file__).parent
PROFILES = json.loads(
    (ROOT / "profiles.json").read_text(encoding="utf-8")
)["profiles"]

STATE = ROOT / "seen.json"
QUOTA = ROOT / "quota.json"

BASE = os.environ["AUCTION_FEED_URL"].strip()
if BASE != "https://apibara.tech/api/v1/vehicle-auction/vehicles":
    raise ValueError("Incorrect API endpoint")

TODAY = dt.datetime.now(dt.timezone.utc).date()
MONTH = TODAY.strftime("%Y-%m")


def load_json(path, default):
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def save_json(path, data):
    path.write_text(
        json.dumps(data, indent=2),
        encoding="utf-8"
    )


seen = set(load_json(STATE, []))
quota = load_json(QUOTA, {
    "month": MONTH,
    "used": 0,
    "last_run": ""
})

if quota.get("month") != MONTH:
    quota = {"month": MONTH, "used": 0, "last_run": ""}


def numeric(value):
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def in_range(value, minimum, maximum):
    value = numeric(value)
    return (
        value is not None
        and (minimum is None or value >= float(minimum))
        and (maximum is None or value <= float(maximum))
    )


def eligible(car, settings):
    condition = car.get("condition") or {}
    odometer = car.get("odometer") or {}
    pricing = car.get("pricing") or {}
    specs = car.get("vehicle_specs") or {}

    run = str(
        condition.get("run_condition")
        or condition.get("run_cond")
        or ""
    ).upper()

    if settings.get("requireRunDrive", True):
        if run != "RUNS AND DRIVES":
            return False

    if settings.get("requireKeys", True):
        if condition.get("has_key") is not True:
            return False

    if not in_range(
        car.get("year"),
        settings.get("year"),
        settings.get("yearMax")
    ):
        return False

    if not in_range(
        odometer.get("mi"),
        settings.get("milesMin"),
        settings.get("miles")
    ):
        return False

    price = pricing.get("buy_now_usd")
    if price is None:
        price = pricing.get("current_bid_usd")

    if not in_range(
        price,
        settings.get("priceMin"),
        settings.get("price")
    ):
        return False

    cylinders = settings.get("cylFilter")
    if cylinders:
        if str(specs.get("cylinders", "")).lower() != str(cylinders).lower():
            return False

    return True


def notify(message):
    payload = urllib.parse.urlencode({
        "chat_id": os.environ["TELEGRAM_CHAT_ID"],
        "text": message,
        "disable_web_page_preview": "true"
    }).encode()

    token = os.environ["TELEGRAM_BOT_TOKEN"]
    url = f"https://api.telegram.org/bot{token}/sendMessage"

    request = urllib.request.Request(url, data=payload)

    with urllib.request.urlopen(request, timeout=30) as response:
        result = json.load(response)

    if not result.get("ok"):
        raise RuntimeError("Telegram message rejected")


def api_search(make, platform, model=None):
    params = {
        "platform": platform,
        "make": make,
        "per_page": 20,
        "run_cond": "RUNS AND DRIVES",
        "has_key": "With",
        "upcoming": "only"
    }

    if model:
        params["model"] = model

    url = BASE + "?" + urllib.parse.urlencode(params)

    request = urllib.request.Request(
        url,
        headers={
            "X-API-Key": os.environ["AUCTION_API_KEY"],
            "Accept": "application/json"
        }
    )

    # Count before requesting, including failed requests.
    quota["used"] += 1
    save_json(QUOTA, quota)

    with urllib.request.urlopen(request, timeout=35) as response:
        result = json.load(response)

    if not isinstance(result, dict) or not isinstance(result.get("data"), list):
        raise RuntimeError("Unexpected API response")

    return result["data"]


def main():
    if quota.get("last_run") == TODAY.isoformat():
        print("Already checked today")
        return

    if quota["used"] >= 90:
        print("Monthly safety limit reached")
        return

    searches = []

    for profile in PROFILES:
        settings = profile.get("settings", profile)
        for make in settings.get("brands", []):
            model = settings.get("modelByMake", {}).get(make)
            for platform in ("copart", "iaai"):
                searches.append(
                    (make, platform, model, profile["name"], settings)
                )

    # Rotate across all configured searches.
    offset = TODAY.toordinal() * 3
    total = len(searches)

    if not total:
        print("No searches configured")
        return

    selected = [
        searches[(offset + i) % total]
        for i in range(min(3, total, 90 - quota["used"]))
    ]

    checked = 0
    sent = 0

    for make, platform, model, profile_name, settings in selected:
        cars = api_search(make, platform, model)
        checked += len(cars)

        for car in cars:
            if not eligible(car, settings):
                continue

            identifier = car.get("lot_number") or car.get("vin")
            if not identifier:
                continue

            key = f"{platform}:{identifier}"

            if key in seen:
                continue

            price = (car.get("pricing") or {}).get("buy_now_usd")
            if price is None:
                price = (car.get("pricing") or {}).get("current_bid_usd")

            url = car.get("url") or car.get("auction_url") or ""
            if not str(url).startswith("https://"):
                url = "Nuoroda nepateikta"

            message = (
                f"🚗 {car.get('year', '?')} "
                f"{car.get('make', '')} {car.get('model', '')}\n"
                f"🔎 {profile_name}\n"
                f"🏷️ {platform.upper()} · Lot {identifier}\n"
                f"💰 {price} USD\n"
                f"🛣️ {(car.get('odometer') or {}).get('mi', '?')} mi\n"
                f"{url}"
            )

            notify(message)
            seen.add(key)
            save_json(STATE, sorted(seen))
            sent += 1

    quota["last_run"] = TODAY.isoformat()
    save_json(QUOTA, quota)

    print(f"Patikrinta: {checked}. Išsiųsta: {sent}.")
    print(f"API užklausų šį mėnesį: {quota['used']}/90")


if __name__ == "__main__":
    main()
