import json
import os
import pathlib
import urllib.parse
import urllib.request

ROOT = pathlib.Path(__file__).parent
PROFILES = json.loads((ROOT / 'profiles.json').read_text(encoding='utf-8'))['profiles']
STATE = ROOT / 'seen.json'
seen = set(json.loads(STATE.read_text(encoding='utf-8')) if STATE.exists() else [])


def fetch():
    url = os.environ['AUCTION_FEED_URL']
    if not url.startswith('https://'):
        raise ValueError('Feed URL must use HTTPS')
    req = urllib.request.Request(url, headers={
        'Authorization': 'Bearer ' + os.getenv('AUCTION_API_KEY', ''),
        'Accept': 'application/json',
    })
    with urllib.request.urlopen(req, timeout=35) as response:
        data = json.load(response)
    if isinstance(data, dict):
        data = data.get('cars', data.get('items', []))
    if not isinstance(data, list):
        raise ValueError('Expected JSON list or {cars: [...]}')
    return data


def yes(value):
    return str(value).strip().lower() in ('yes', 'true', '1')


def match(car, settings):
    if not (yes(car.get('runDrive')) and yes(car.get('keys')) and
            str(car.get('airbags', '')).lower() == 'not_deployed'):
        return False
    if settings.get('brands') and str(car.get('make', '')).lower() not in [str(x).lower() for x in settings['brands']]:
        return False
    if settings.get('singleMake') and str(car.get('make', '')).lower() != str(settings['singleMake']).lower():
        return False
    if settings.get('modelFilter') and str(settings['modelFilter']).lower() not in str(car.get('model', '')).lower():
        return False
    if settings.get('auction') and str(settings['auction']).lower() != str(car.get('auction', '')).lower():
        return False
    for field, low, high in [('year', 'year', 'yearMax'), ('price', 'priceMin', 'price'), ('miles', 'milesMin', 'miles')]:
        if settings.get(low) is not None or settings.get(high) is not None:
            try:
                value = float(car[field])
            except (KeyError, ValueError, TypeError):
                return False
            if settings.get(low) is not None and value < float(settings[low]):
                return False
            if settings.get(high) is not None and value > float(settings[high]):
                return False
    if settings.get('cylFilter'):
        try:
            if int(car['cylinders']) != int(settings['cylFilter']):
                return False
        except (KeyError, TypeError, ValueError):
            return False
    return True


def notify(message):
    token = os.environ['TELEGRAM_BOT_TOKEN']
    chat = os.environ['TELEGRAM_CHAT_ID']
    payload = urllib.parse.urlencode({
        'chat_id': chat,
        'text': message,
        'disable_web_page_preview': 'true',
    }).encode()
    req = urllib.request.Request('https://api.telegram.org/bot' + token + '/sendMessage', data=payload)
    with urllib.request.urlopen(req, timeout=30) as response:
        result = json.load(response)
        if not result.get('ok'):
            raise RuntimeError('Telegram send failed')


def main():
    cars = fetch()
    pending = set()
    new = []
    for profile in PROFILES:
        settings = profile.get('settings', profile)
        for car in cars:
            key = str(car.get('auction', '')) + ':' + str(car.get('lot') or car.get('vin') or '')
            if key.endswith(':') or key in seen or key in pending or not match(car, settings):
                continue
            pending.add(key)
            url = str(car.get('url', ''))
            if not url.startswith('https://'):
                url = 'Nuoroda nepateikta'
            message = (
                f"🚗 {profile.get('name', 'Paieška')}: {car.get('year', '?')} {car.get('make', '')} {car.get('model', '')}\n"
                f"{car.get('auction', '')} · Lot {car.get('lot', '?')} · {car.get('price', '?')} USD · "
                f"{car.get('miles', '?')} mi · {car.get('cylinders', '?')} cilindrai\n{url}"
            )
            new.append((key, message))
    for key, message in new:
        notify(message)
        seen.add(key)
        STATE.write_text(json.dumps(sorted(seen), ensure_ascii=False, indent=2), encoding='utf-8')
    print(f'Patikrinta automobilių: {len(cars)}. Nauji pranešimai: {len(new)}.')


if __name__ == '__main__':
    main()
