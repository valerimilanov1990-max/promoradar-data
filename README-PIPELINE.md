# Тръбопроводът за данни (безплатен, на GitHub)

Всяка сутрин GitHub пуска scraper.py на своя машина: истински браузър чете
брошурите, OCR ги разчита, kolkostruva архивът се парсва (CSV и XLSX),
и резултатът се записва в data.json. Приложението тегли само този файл.

## Пускане (еднократно, ~10 мин)

1. github.com → New repository → име: promoradar-data → Public → Create
2. Качи файловете от тази папка (uploading an existing file → drag&drop,
   включително скритата папка .github/workflows/scrape.yml — създай я през
   "Add file → Create new file" с име `.github/workflows/scrape.yml`)
3. Repo → Actions → I understand… Enable → Scrape → Run workflow
4. След ~5 минути в repo-то се появява data.json. Адресът му е:
   https://raw.githubusercontent.com/ТВОЯ-ЮЗЪР/promoradar-data/main/data.json
5. Прати този адрес на Claude → вписва се в приложението (RemoteConfig) и
   готово: телефонът пие готови данни за секунда.

## Поддръжка

- Продуктите/думите се сменят в config.json (директно в GitHub).
- Счупи ли се верига: поправка в scraper.py → всички телефони са наред на
  следващата сутрин, БЕЗ нова версия в Google Play.
- Errors секцията в data.json показва какво не е сработило при последното
  пускане.
