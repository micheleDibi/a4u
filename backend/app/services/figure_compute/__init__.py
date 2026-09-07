"""Calcolo «leaf» delle figure accademiche (Vega-Lite, `function`).

Ogni modulo di questo package è importabile dal processo figlio `spawn`
di `isolated.run_isolated` senza `app.core.config`, SQLAlchemy o altre
dipendenze applicative: solo libreria standard e, dove serve, le librerie
di calcolo (vl_convert, sympy, numpy, matplotlib) importate localmente
nelle funzioni.

- `isolated`: esecuzione di una funzione in un processo figlio con
  timeout e `kill()` (l'unica cosa uccidibile in Python 3.12, A13);
- `vegalite_rules`: regole D5 e euristica del criterio 10 sulle spec
  Vega-Lite (puro, senza rete né librerie);
- `vegalite_render`: bersaglio del figlio per `vl_convert.vegalite_to_svg`;
- `function_*` (WP7): parsing, calcolo numerico/simbolico e disegno delle
  figure matematiche.
"""
