
import datetime as dt
import json
import os
import pathlib
import urllib.error
import urllib.parse
import urllib.request

ROOT = pathlib.Path(__file__).parent
PROFILES = json.loads(
    (ROOT / "profiles.json").read_text(encoding="utf-8")
)["profiles"]

STATE = ROOT / "seen.json"
SEEN = set(json.loads(STATE.read_text(encoding="utf-8"))) if STATE.exists() else set()

BASE = os.environ["AUCTION_FEED_URL"].strip()
if BASE != "https://apibara.tech/api/v1/vehicle-auction/vehicles":
    raise ValueError("Incorrect auction API endpoint")


def numeric(value):
    try:
        return float(value) if value is not None else None
    except (ValueError, TypeError):
        return None


def in_range(value, minimum, maximum):
    number = numeric(value)
    return (
        number is not None
        and (minimum is None or number >= float(minimum))
        and (maximum is None or number <= float(maximum))
    )


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

    with urllib.request.urlopen(request, timeout=35) as response:
        result = json.load(response)

    if (
        not isinstance(result, dict)
        or result.get("ok") is False
        or not isinstance(result.get("data"), list)
    ):
        raise RuntimeError("Unexpected API response")

    return result["data"]


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
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    chat_id = os.environ["TELEGRAM_CHAT_ID"]

    payload = urllib.parse.urlencode({
        "chat_id": chat_id,
        "text": message,
        "disable_web_page_preview": "true"
    }).encode()

    request = urllib.request.Request(
        "https://api.telegram.org/bot" + token + "/sendMessage",
        data=payload
    )

    with urllib.request.urlopen(request, timeout=30) as response:
        result = json.load(response)
        if not result.get("ok"):
            raise RuntimeError("Telegram rejected message")


def main():
    # Avoid duplicate API calls for overlapping profiles.
    searches = {}

    for profile in PROFILES:
        settings = profile.get("settings", profile)
        brands = settings.get("brands", [])
        models = settings.get("modelByMake", {})
        auction = str(settings.get("auction") or "").lower()

        platforms = (
            [auction] if auction in ("copart", "iaai")
            else ["copart", "iaai"]
        )

        for make in brands:
            model = models.get(make)
            for platform in platforms:
                key = (make.lower(), platform, (model or "").lower())
                searches.setdefault(key, []).append(
                    (profile["name"], settings)
                )

    # Two searches for Ford Mustang and Jeep Wrangler,
    # plus fourteen general searches = 18 requests.
    # Deduplication reduces this to 18 or fewer.
    checked = 0
    sent = 0

    for (make, platform, model), profile_list in searches.items():
        cars = api_search(make, platform, model or None)
        checked += len(cars)

        for car in cars:
            matched = [
                name for name, settings in profile_list
                if eligible(car, settings)
            ]

            if not matched:
                continue

            lot = car.get("lot_number")
            identifier = lot or car.get("vin")
            if not identifier:
                continue

            unique_key = f"{platform}:{identifier}"
            if unique_key in SEEN:
                continue

            pricing = car.get("pricing") or {}
            price = pricing.get("buy_now_usd")
            if price is None:
                price = pricing.get("current_bid_usd")

            odometer = car.get("odometer") or {}
            url = car.get("url") or car.get("auction_url") or ""
            if not str(url).startswith("https://"):
                url = "Nuoroda nepateikta"

            message = (
                f"🚗 {car.get('year', '?')} "
                f"{car.get('make', '')} {car.get('model', '')}\n"
                f"🔎 Paieška: {', '.join(matched)}\n"
                f"🏷️ {platform.upper()} · Lot {lot or '?'}\n"
                f"💰 {price} USD\n"
                f"🛣️ {odometer.get('mi', '?')} mi\n"
                f"{url}"
            )

            notify(message)
            SEEN.add(unique_key)
            STATE.write_text(
                json.dumps(sorted(SEEN), indent=2),
                encoding="utf-8"
            )
            sent += 1

    print(f"Patikrinta: {checked}. Išsiųsta: {sent}.")
    print(f"API užklausų: {len(searches)}")


if __name__ == "__main__":
    main()
