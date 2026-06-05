# Tover Floor Navigator

App separata dal vecchio calcolatore. Il database prodotti viene generato dai PDF nella cartella:

```text
/Users/michele/Desktop/STD
```

## Rigenerare i dati

```bash
/Users/michele/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tools/build_std_data.py /Users/michele/Desktop/STD
```

## Importare il listino prezzi

Dopo aver rigenerato le schede STD, importa i prezzi dal listino:

```bash
/Users/michele/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tools/import_price_list.py "/Users/michele/Library/CloudStorage/Dropbox/TOVER/2026/LISTINO 26/TOVER listino prezzi 2026-rev MAG 26.pdf"
```

## Avvio locale

Da questa cartella:

```bash
python3 -m http.server 8765
```

Poi apri:

```text
http://127.0.0.1:8765/
```

L'app legge `data/products.json`, consiglia prodotti in base a sottofondo/rivestimento/obiettivo e calcola il preventivo con prezzi listino, posa, sconto ed extra. I prodotti non trovati nel listino restano compilabili manualmente.
