import datetime as dt
import json
import os
import pathlib
import urllib.error
import urllib.parse
import urllib.request

ROOT = pathlib.Path(__file__).parent
PROFILES = json.loads((ROOT / 'profiles.json').read_text(encoding='utf-8'))['profiles']
STATE = ROOT / 'seen.json'
SEEN = set(json.loads(STATE.read_text(encoding='utf-8'))) if STATE.exists() else set()
BASE = os.environ['AUCTION_FEED_URL'].strip()
if BASE != 'https://apibara.tech/api/v1/vehicle-auction/vehicles':
    raise ValueError('AUCTION_FEED_URL must be the Apibara vehicles endpoint')


def api_search(make, platform, model=None):
    params = {'platform': platform, 'make': make, 'per_page': 20,
              'run_cond': 'RUNS AND DRIVES', 'has_key': 'With', 'upcoming': 'only'}
    if model:
        params['model'] = model
    url = BASE + '?' + urllib.parse.urlencode(params)
    request = urllib.request.Request(url, headers={
        'X-API-Key': os.environ['AUCTION_API_KEY'],
        'Accept': 'application/json'})
    try:
        with urllib.request.urlopen(request, timeout=35) as response:
            result = json.load(response)
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f'Apibara HTTP {exc.code}; check API plan, key and filters') from exc
    if not isinstance(result, dict) or result.get('ok') is False or not isinstance(result.get('data'), list):
        raise RuntimeError('Unexpected Apibara response; expected data array')
    return result['data']


def numeric(value):
    try:
        return float(value) if value is not None else None
    except (ValueError, TypeError):
        return None


def in_range(value, minimum, maximum):
    number = numeric(value)
    return number is not None and (minimum is None or number >= float(minimum)) and (maximum is None or number <= float(maximum))


def eligible(car, settings):
    condition = car.get('condition') or {}
    odometer = car.get('odometer') or {}
    pricing = car.get('pricing') or {}
    specs = car.get('vehicle_specs') or {}
    run = str(condition.get('run_condition') or condition.get('run_cond') or '').upper()
    if settings.get('requireRunDrive', True) and run != 'RUNS AND DRIVES':
        return False
    if settings.get('requireKeys', True) and condition.get('has_key') is not True:
        return False
    if not in_range(car.get('year'), settings.get('year'), settings.get('yearMax')):
        return False
    if not in_range(odometer.get('mi'), settings.get('milesMin'), settings.get('miles')):
        return False
    price = pricing.get('buy_now_usd')
    if price is None:
        price = pricing.get('current_bid_usd')
    if not in_range(price, settings.get('priceMin'), settings.get('price')):
        return False
    cyl = settings.get('cylFilter')
    if cyl and str(specs.get('cylinders', '')).lower() != str(cyl).lower():
        return False
    if car.get('make', '').lower() == 'chevrolet' and 'corvette' not in str(car.get('model', '')).lower():
        return False
    return True


def notify(message):
    token = os.environ['TELEGRAM_BOT_TOKEN']
    payload = urllib.parse.urlencode({'chat_id': os.environ['TELEGRAM_CHAT_ID'],
                                      'text': message, 'disable_web_page_preview': 'true'}).encode()
    request = urllib.request.Request('https://api.telegram.org/bot' + token + '/sendMessage', data=payload)
    with urllib.request.urlopen(request, timeout=30) as response:
        if not json.load(response).get('ok'):
            raise RuntimeError('Telegram rejected message')


def main():
    # Two requests/day: 60-62 per month; rotate through seven brands.
    # A single page per platform is a SAMPLE, not a complete inventory scan.
    brands = ['Ford', 'Audi', 'BMW', 'Jeep', 'Dodge', 'Chevrolet', 'Tesla']
    make = brands[dt.date.today().toordinal() % len(brands)]
    settings = PROFILES[0].get('settings', PROFILES[0])
    model = 'Corvette' if make == 'Chevrolet' else None
    checked = sent = 0
    for platform in ('copart', 'iaai'):
        cars = api_search(make, platform, model)
        checked += len(cars)
        for car in cars:
            lot = car.get('lot_number')
            identifier = lot or car.get('vin')
            if not identifier or not eligible(car, settings):
                continue
            key = f'{platform}:{identifier}'
            if key in SEEN:
                continue
            pricing = car.get('pricing') or {}
            price = pricing.get('buy_now_usd')
            if price is None:
                price = pricing.get('current_bid_usd')
            url = car.get('url') or car.get('auction_url') or ''
            if not str(url).startswith('https://'):
                url = 'Nuoroda nepateikta'
            message = (f"🚗 {car.get('year', '?')} {car.get('make', '')} {car.get('model', '')}\n"
                       f"{platform.upper()} · Lot {lot or '?'} · {price} USD · "
                       f"{(car.get('odometer') or {}).get('mi', '?')} mi\n{url}")
            notify(message)
            SEEN.add(key)
            STATE.write_text(json.dumps(sorted(SEEN), indent=2), encoding='utf-8')
            sent += 1
    print(f'Markė: {make}. Patikrinta: {checked}. Išsiųsta: {sent}.')
    print('Pastaba: tik pirmieji 20 kiekvieno aukciono įrašų; ne visa pasiūla.')


if __name__ == '__main__':
    main()
