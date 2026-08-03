name: Scrape

on:
  schedule:
    - cron: "0 4 * * *"    # всяка сутрин 04:00 UTC (7:00 БГ)
  workflow_dispatch: {}      # + ръчно пускане от бутона Run workflow

permissions:
  contents: write

concurrency:                 # две пускания едновременно само си пречат
  group: scrape
  cancel-in-progress: false

jobs:
  scrape:
    runs-on: ubuntu-latest
    timeout-minutes: 90

    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
          cache: pip

      - name: Install deps
        run: |
          pip install -q -r requirements.txt
          python -m playwright install --with-deps chromium

      - name: Run scraper
        run: python scraper.py

      # ------------------------------------------------------------------
      # Данните отиват в ОТДЕЛЕН клон "data" с история от един-единствен
      # комит. Иначе всекидневният многомегабайтов файл се трупа в git
      # завинаги — за година това са гигабайти, които никой не ползва.
      # Тук всяко пускане презаписва клона наново и хранилището остава малко.
      # ------------------------------------------------------------------
      - name: Publish data branch
        run: |
          set -e
          test -f data.json || { echo "няма data.json — скрейпърът се провали"; exit 1; }

          PUB="$RUNNER_TEMP/pub"
          rm -rf "$PUB" && mkdir -p "$PUB"
          cp data.json "$PUB"/
          cp -r feed "$PUB"/ 2>/dev/null || true

          cd "$PUB"
          git init -q -b data
          git config user.name  "promo-radar-bot"
          git config user.email "bot@promoradar"
          git add -A
          git commit -q -m "data $(date -u '+%Y-%m-%d %H:%M UTC')"
          git remote add origin \
            "https://x-access-token:${{ secrets.GITHUB_TOKEN }}@github.com/${{ github.repository }}.git"
          git push -f -q origin data

          echo "Публикувано в клон data:"
          ls -lh data.json feed/ 2>/dev/null || true

      - name: Summary
        if: always()
        run: |
          echo "### Промо Радар — резултат" >> $GITHUB_STEP_SUMMARY
          if [ -f data.json ]; then
            python - <<'PY' >> $GITHUB_STEP_SUMMARY
          import json
          d = json.load(open("data.json", encoding="utf-8"))
          print(f"- обновено: **{d.get('updated')}**")
          print(f"- оферти: **{len(d.get('offers', []))}**")
          print(f"- официални цени: **{len(d.get('basics', []))}**")
          print(f"- eBag: **{len(d.get('ebag', []))}**")
          st = d.get("stats", {})
          if st:
              print("\n**Статистика**\n")
              for k, v in st.items():
                  print(f"- `{k}`: {v}")
          er = d.get("errors", [])
          if er:
              print(f"\n**Грешки ({len(er)})**\n")
              for e in er[:25]:
                  print(f"- {e}")
          PY
          else
            echo "- скрейпърът не произведе data.json" >> $GITHUB_STEP_SUMMARY
          fi
