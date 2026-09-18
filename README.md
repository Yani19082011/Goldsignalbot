# GoldSignalBot

Автоматичен бот, който следи цената на златото (XAU/USD) и ти праща имейл,
когато няколко технически индикатора едновременно сочат добра точка за
**покупка** или **продажба**.

Работи по същия модел като PennyStockScanner/MemecoinScanner: Flask health
endpoint + фонов thread, hosted безплатно на Render, alerts през Resend
(защото Gmail App Password е блокиран от Family Link на тази сметка).

## Как решава кога да сигнализира

На всеки цикъл (по подразбиране на 5 мин) тегли последните 15-минутни свещи
на златото и проверява 5 независими условия:

1. **EMA9 спрямо EMA21** - краткосрочен тренд нагоре/надолу
2. **Цена спрямо EMA50** - по-общ тренд филтър
3. **RSI(14)** - излиза ли от препродадена/прекупена зона
4. **MACD хистограма** - положителна/отрицателна = бичи/мечи моментум
5. **Bollinger Bands** - тества ли цената долната (подкрепа) или горната
   (съпротива) лента

Ако поне `CONFLUENCE_THRESHOLD` (по подразбиране **4 от 5**) условия
сочат в една посока - изпраща се имейл с цена, RSI, предложени
stop-loss/take-profit (базирани на ATR, R:R ≈ 1:2) и списък какво точно
се е задействало. Няма да получаваш повторен имейл за същия сигнал по-често
от `REALERT_COOLDOWN_HOURS` (по подразбиране 6 часа).

**Важно:** Това са чисто технически сигнали. Златото се движи и от
новини (лихвени решения, геополитика, доларов индекс), които индикаторите
не виждат предварително. Тествано е с автоматични тестове (виж по-долу),
но не е финансов съвет - провери сам преди да отвориш позиция.

## Файлове

- `app.py` - Flask сървър + фонов loop, който прави проверките
- `signals.py` - логиката за индикаторите и confluence сигнала
- `data_fetch.py` - тегли цени от няколко източника с автоматичен fallback:
  Yahoo Finance -> Twelve Data (ако имаш ключ) -> stooq.com
- `notifier.py` - изпраща имейли през Resend API
- `config.py` - всички настройки, четени от environment variables
- `test_signals.py` - офлайн тестове на сигналната логика (без нужда от мрежа)
- `test_data_fetch.py` - офлайн тестове на fallback логиката между източниците

## 1. Локално тестване (по избор)

```bash
pip install -r requirements.txt
python3 test_signals.py          # тества сигналната логика със синтетични данни
python3 test_data_fetch.py       # тества fallback-а между източниците
cp .env.example .env             # попълни RESEND_API_KEY
python3 app.py                   # стартира на http://localhost:10000
curl http://localhost:10000/check-now   # ръчна проверка
```

## 2. Качване в GitHub

```bash
cd GoldSignalBot
git init
git add .
git commit -m "Initial commit: GoldSignalBot"
gh repo create GoldSignalBot --private --source=. --push
# или ръчно през github.com -> New repository -> git remote add origin ... -> git push
```

## 3. Resend (имейлите)

1. Регистрирай се безплатно на https://resend.com
2. Отиди в **API Keys** -> Create API Key -> копирай ключа
   (започва с `re_...`)
3. **Важно за адреса на получателя:** без да верифицираш собствен домейн,
   Resend позволява да изпращаш само до имейла, с който си регистриран в
   Resend. Ако регистрираш акаунта с `yani.kolev2011@gmail.com`, всичко работи
   веднага. Ако искаш да пращаш към друг адрес, верифицирай домейн в
   **Domains** секцията (безплатно, отнема няколко минути с DNS запис).

## 3.5. Twelve Data (по избор, но препоръчително)

Ботът проверява пазара по-често сега (на 5 мин), а Yahoo Finance понякога
временно блокира/рейт-лимитира заявки от cloud сървъри (Render, AWS и т.н.)
при твърде чести автоматични заявки. Затова има вграден **втори източник**,
който се ползва автоматично като backup:

1. https://twelvedata.com -> Sign Up (безплатно, не иска карта)
2. Dashboard -> API Keys -> копирай ключа
3. Добави го в Render като `TWELVEDATA_API_KEY`

Без този ключ ботът пак работи нормално (пада направо към stooq.com при
проблем с Yahoo), просто е малко по-издръжлив с ключа.

## 4. Deploy в Render (безплатно)

1. https://render.com -> **New +** -> **Web Service**
2. Свържи GitHub repo-то `GoldSignalBot`
3. Настройки:
   - **Runtime:** Python 3
   - **Build Command:** `pip install -r requirements.txt`
   - **Start Command:** `gunicorn app:app --bind 0.0.0.0:$PORT --workers 1 --threads 4 --timeout 120`
   - **Instance Type:** Free
4. **Environment** таб -> добави:
   - `RESEND_API_KEY` = ключът от стъпка 3
   - `ALERT_EMAIL` = `yani.kolev2011@gmail.com`
   - `TWELVEDATA_API_KEY` = ключът от стъпка 3.5 (по избор, но препоръчително)
   - `CANDLE_INTERVAL` = `15m`
   - `CHECK_INTERVAL_MINUTES` = `5` (или колкото искаш)
   - `CONFLUENCE_THRESHOLD` = `4`
   - `SEND_TEST_EMAIL_ON_START` = `true` (за да провериш, че имейлите работят; после можеш да го изключиш)
5. **Create Web Service** - Render ще build-не и стартира бота.
   Провери логовете - трябва да видиш `Check complete: price=...`.

## 5. Keep-alive (задължително за free tier)

Render приспива безплатните услуги след ~15 мин без входящ HTTP трафик.
За да не спира ботът:

1. https://cron-job.org -> регистрация (безплатно)
2. Create cronjob -> URL: `https://<твоя-render-service>.onrender.com/`
3. Интервал: на всеки 10 минути

Това е същият pattern, който вече ползваш за другите два бота.

## Настройки (Environment Variables)

| Променлива | По подразбиране | Описание |
|---|---|---|
| `RESEND_API_KEY` | (празно) | задължително - ключ от resend.com |
| `ALERT_EMAIL` | `yani.kolev2011@gmail.com` | къде да идват сигналите |
| `TWELVEDATA_API_KEY` | (празно) | по избор - backup източник за цени |
| `CANDLE_INTERVAL` | `15m` | размер на свещите (5m/15m/30m/60m) |
| `CHECK_INTERVAL_MINUTES` | `5` | на колко минути да проверява пазара |
| `CONFLUENCE_THRESHOLD` | `4` | колко от 5-те условия трябва да съвпаднат (3=повече сигнали/шум, 5=само перфектни setup-и) |
| `REALERT_COOLDOWN_HOURS` | `6` | минимум часове между два еднакви сигнала |
| `GOLD_TICKER` | `XAUUSD=X` | Yahoo Finance тикер (спот злато) |

Пълен списък - виж `.env.example`.

## Известни ограничения / идеи за следваща версия

- Проверява само една графика (по подразбиране 15 мин); може да се добави
  дневен тренд филтър за по-малко фалшиви сигнали при движение срещу
  големия тренд.
- По-честите проверки (на 5 мин) означават повече заявки към Yahoo Finance
  -> по-голям шанс от временно rate-limit. Затова има Twelve Data fallback
  (стъпка 3.5) - силно препоръчително да добавиш ключ, ако ще ползваш
  5-минутния интервал постоянно.
- Може да се добави Telegram/SMS известие успоредно с имейла, ако решиш.
