import json, os, pathlib, urllib.request, urllib.parse
ROOT=pathlib.Path(__file__).parent
PROFILES=json.loads((ROOT/'profiles.json').read_text(encoding='utf-8'))['profiles']
STATE=ROOT/'seen.json'
seen=set(json.loads(STATE.read_text()) if STATE.exists() else [])

def fetch():
    url=os.environ['AUCTION_FEED_URL']
    if not url.startswith('https://'): raise ValueError('Feed URL must use HTTPS')
    req=urllib.request.Request(url,headers={'Authorization':'Bearer '+os.getenv('AUCTION_API_KEY',''),'Accept':'application/json'})
    with urllib.request.urlopen(req,timeout=35) as response: data=json.load(response)
    if isinstance(data,dict): data=data.get('cars',data.get('items',[]))
    if not isinstance(data,list): raise ValueError('Expected JSON list or {cars: [...]}')
    return data

def yes(v): return str(v).strip().lower() in ('yes','true','1')
def match(c,s):
    if not (yes(c.get('runDrive')) and yes(c.get('keys')) and str(c.get('airbags','')).lower()=='not_deployed'):return False
    if s.get('brands') and str(c.get('make','')).lower() not in [str(x).lower() for x in s['brands']]:return False
    if s.get('singleMake') and str(c.get('make','')).lower()!=str(s['singleMake']).lower():return False
    if s.get('modelFilter') and str(s['modelFilter']).lower() not in str(c.get('model','')).lower():return False
    if s.get('auction') and str(s['auction']).lower()!=str(c.get('auction','')).lower():return False
    for field,low,high in [('year','year','yearMax'),('price','priceMin','price'),('miles','milesMin','miles')]:
        if s.get(low) or s.get(high):
            try: val=float(c[field])
            except (KeyError,ValueError,TypeError):return False
            if s.get(low) and val<float(s[low]):return False
            if s.get(high) and val>float(s[high]):return False
    if s.get('cylFilter'):
        try:
            if int(c['cylinders'])!=int(s['cylFilter']):return False
        except (KeyError,TypeError,ValueError):return False
    return True

def notify(message):
    token=os.environ['TELEGRAM_BOT_TOKEN']; chat=os.environ['TELEGRAM_CHAT_ID']
    payload=urllib.parse.urlencode({'chat_id':chat,'text':message,'disable_web_page_preview':'true'}).encode()
    req=urllib.request.Request('https://api.telegram.org/bot'+token+'/sendMessage',data=payload)
    with urllib.request.urlopen(req,timeout=30) as r:
        result=json.load(r)
        if not result.get('ok'):raise RuntimeError('Telegram send failed')

def main():
    cars=fetch(); new=[]; pending=set()
    for p in PROFILES:
        s=p.get('settings',p)
        for c in cars:
            key=str(c.get('auction',''))+':'+str(c.get('lot') or c.get('vin') or '')
            if key.endswith(':') or key in seen or key in pending or not match(c,s):continue
            pending.add(key)
            url=str(c.get('url',''))
            if not url.startswith('https://'):url='Nuoroda nepateikta'
            new.append((key,f"🚗 {p.get('name','Paieška')}: {c.get('year','?')} {c.get('make','')} {c.get('model','')}\n{c.get('auction','')} · Lot {c.get('lot','?')} · {c.get('price','?')} USD · {c.get('miles','?')} mi · {c.get('cylinders','?')} cilindrai\n{url}"))
    for key,msg in new:
        notify(msg)
        seen.add(key)
        STATE.write_text(json.dumps(sorted(seen),ensure_ascii=False,indent=2))
    print(f'Checked {len(cars)} cars; sent {len(new)} notifications')
if __name__=='__main__':main()
