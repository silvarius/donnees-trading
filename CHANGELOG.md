# Changelog

## 2026-10-23
- Ajout de `scripts/construire_serie_continue.py` : construit la série continue MNQ 1 minute (brute, ajustée par différence, ajustée par ratio) et la table des rolls à partir de l'export Databento GLBX ohlcv-1m.
- Règle de roll hybride hors fenêtres : contrat de plus gros volume de la veille (J-1) comme base, bascule anticipée dès que le volume cumulé de session du contrat suivant dépasse celui de l'ancien, reportée si elle tombe dans une fenêtre de trading (Tokyo 09:00/14:00, London 08:00/14:00, New York 09:30/14:00, 90 min, heure locale).
- Spreads calendaires exclus ; série de 2 632 103 barres, 30 rolls, du 2019-05-06 au 2026-10-21.
