
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
EXPECTED = "https://apibara.tech/api/v1/vehicle-auction/vehicles"

if BASE != EXPECTED:
    raise ValueError("Incorrect Apibara API endpoint")

TODAY = dt.datetime.now(dt.timezone.utc).date()
MONTH = TODAY.strftime("%Y-%m")

# Total monthly usage, including previous tests.
MONTHLY_LIMIT = 90
DAILY_LIMIT = 3


def load_json(path):
    if not path.exists():
        raise RuntimeError(f"Missing state file: {path.name}")
    return json.loads(path.read_text(encoding="utf-8"))


def save_json(path, data):
    path.write_text(
        json.dumps(data, indent=2, ensure_ascii=False),
        encoding="utf-8"
    )


seen = set(load_json(STATE))
quota = load_json(QUOTA)

if quota.get("month") != MONTH:
    # Do not silently reset usage.
    raise RuntimeError(
        "Quota month differs. Verify Apibara usage "
        "and update quota.json before continuing."
    )

if not isinstance(quota.get("used"), int):
    raise RuntimeError("Invalid quota counter")


def numeric(value):
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def in_range(value, minimum, maximum):
    number = numeric(value)
    return (
        number is not None
        and (minimum is None or number >= float(minimum))
        and (maximum is None or number <= float(maximum))
    )


def eligible(car, settings, expected_make, expected_model):
    condition = car.get("condition") or {}
    odometer = car.get("odometer") or {}
    pricing = car.get("pricing") or {}
    specs = car.get("vehicle_specs") or {}

    if str(car.get("make", "")).lower() != expected_make.lower():
        return False

    if expected_model:
        if expected_model.lower() not in str(
            car.get("model", "")
        ).lower():
            return False

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
        if str(specs.get("cylinders", "")).lower() != str(
            cylinders
        ).lower():
            return False

    return True


def notify(message):
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    payload = urllib.parse.urlencode({
        "chat_id": os.environ["TELEGRAM_CHAT_ID"],
        "text": message,
        "disable_web_page_preview": "true"
    }).encode()

    request = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/sendMessage",
        data=payload
    )

    with urllib.request.urlopen(request, timeout=30) as response:
        result = json.load(response)

    if not result.get("ok"):
        raise RuntimeError("Telegram message rejected")


def api_search(make, platform, model):
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

    # Record attempted requests, including HTTP errors.
    quota["used"] += 1
    save_json(QUOTA, quota)

    with urllib.request.urlopen(request, timeout=35) as response:
        result = json.load(response)

    if (
        not isinstance(result, dict)
        or result.get("ok") is False
        or not isinstance(result.get("data"), list)
    ):
        raise RuntimeError("Unexpected Apibara response")

    return result["data"]


def build_searches():
    searches = []

    for profile in PROFILES:
        settings = profile.get("settings", profile)
        brands = settings.get("brands", [])
        models = settings.get("modelByMake", {})

        for make in brands:
            configured = models.get(make)

            if isinstance(configured, list):
                model_list = configured or [None]
            else:
                model_list = [configured]

            for model in model_list:
                for platform in ("copart", "iaai"):
                    searches.append((
                        make, platform, model,
                        profile.get("name", "PalmImport"),
                        settings
                    ))

    return searches


def main():
    if quota.get("last_run") == TODAY.isoformat():
        print("Already checked today")
        return

    if quota["used"] >= MONTHLY_LIMIT:
        print("Monthly safety limit reached")
        return

    searches = build_searches()
    if not searches:
        print("No searches configured")
        return

    # Advance by the number of searches completed
    # on previous days, rather than calendar day * 3.
    cursor = int(quota.get("cursor", 0))
    count = min(
        DAILY_LIMIT,
        len(searches),
        MONTHLY_LIMIT - quota["used"]
    )

    checked = 0
    sent = 0

    for i in range(count):
        make, platform, model, profile_name, settings = (
            searches[(cursor + i) % len(searches)]
        )

        # Advance before the request, so failed
        # searches are not repeated indefinitely.
        quota["cursor"] = cursor + i + 1
        save_json(QUOTA, quota)

        cars = api_search(make, platform, model)
        checked += len(cars)

        for car in cars:
            if not eligible(car, settings, make, model):
                continue

            identifier = car.get("lot_number") or car.get("vin")
            if not identifier:
                continue

            key = f"{platform}:{identifier}"
            if key in seen:
                continue

            pricing = car.get("pricing") or {}
            price = pricing.get("buy_now_usd")
            if price is None:
                price = pricing.get("current_bid_usd")

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
    print(f"Apibara užklausų: {quota['used']}/{MONTHLY_LIMIT}")


if __name__ == "__main__":
    main()
