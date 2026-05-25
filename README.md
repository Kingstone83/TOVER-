# Calcolatore Costo-Cantiere Tover

Questo progetto contiene due versioni dell'app:

- `standalone/index.html`: file singolo da condividere direttamente.
- `pwa/`: versione installabile come app da smartphone, pronta per sito o GitHub Pages.

## Test locale

Apri `standalone/index.html` con doppio click, oppure avvia un server nella cartella del progetto:

```bash
python3 -m http.server 8765
```

Poi visita:

```text
http://127.0.0.1:8765/pwa/
```

## Pubblicazione

Per testarla online con GitHub Pages, carica il progetto su GitHub e abilita Pages dalla branch `main`, cartella root. La versione PWA sara disponibile a:

```text
https://NOME-UTENTE.github.io/NOME-REPO/pwa/
```

La PWA richiede HTTPS per service worker e installazione su smartphone.
