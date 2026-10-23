"""Construit la serie continue MNQ 1 minute a partir de l'export Databento (GLBX ohlcv-1m).

Regle de roll "hybride hors fenetres" :
  - base : le jour de trading J utilise le contrat de plus gros volume du jour J-1 (bascule a l'ouverture de session, 18h ET) ;
  - anticipation : pendant les 9 jours de trading qui precedent l'expiration, on bascule sur le contrat suivant
    des que son volume cumule depuis l'ouverture de session depasse celui de l'ancien (minimum 3000 contrats cumules),
    de facon irreversible ;
  - contrainte : si ce signal tombe dans une fenetre de trading, la bascule est reportee a la 1ere minute libre.
Fenetres (90 min, heure locale, DST pris en compte) : Tokyo 09:00 et 14:00, London 08:00 et 14:00, New York 09:30 et 14:00.

Sorties (dans le dossier de sortie) :
  MNQ_1m_continu_brut.csv   prix bruts, colonnes ts_event(UTC), symbol, open..volume, roll (1 = 1ere barre du nouveau contrat)
  MNQ_1m_continu_diff.csv   idem, OHLC ajustes par difference (back-adjust, dernier prix = prix reel)
  MNQ_1m_continu_ratio.csv  idem, OHLC ajustes par ratio
  MNQ_rolls.csv             table des rolls (heure de bascule, prix des 2 contrats, ecart, ratio, cause)
"""
import sys
import numpy as np
import pandas as pd
from zoneinfo import ZoneInfo

SRC, OUT = sys.argv[1], sys.argv[2]
MONTH = {"H": 3, "M": 6, "U": 9, "Z": 12}
YEAR = {str(i): 2020 + i for i in range(10)}; YEAR["9"] = 2019
WIN = [("Asia/Tokyo", 9 * 60), ("Asia/Tokyo", 14 * 60), ("Europe/London", 8 * 60), ("Europe/London", 14 * 60),
       ("America/New_York", 9 * 60 + 30), ("America/New_York", 14 * 60)]
MIN_CUM, WIN_DAYS = 3000, 9


def expiry(sym):
    d = pd.Timestamp(YEAR[sym[-1]], MONTH[sym[3]], 1)
    return d + pd.Timedelta(days=(4 - d.weekday()) % 7 + 14)          # 3e vendredi du mois


def trading_day(idx):
    return pd.DatetimeIndex((idx.tz_convert("America/New_York") + pd.Timedelta(hours=6)).normalize().tz_localize(None))


def in_window(idx):
    out = np.zeros(len(idx), bool)
    for tz, start in WIN:
        loc = idx.tz_convert(ZoneInfo(tz)); t = loc.hour * 60 + loc.minute
        out |= (t >= start) & (t < start + 90)
    return out


df = pd.read_csv(SRC, parse_dates=["ts_event"])
df = df[~df.symbol.str.contains("-")].copy()                         # on retire les spreads calendaires
df["td"] = trading_day(pd.DatetimeIndex(df.ts_event))
dv = df.groupby(["td", "symbol"]).volume.sum().unstack(fill_value=0)
days = dv.index
sel = dv.idxmax(axis=1).shift(1).dropna()                            # contrat J-1
order = sorted(df.symbol.unique(), key=expiry)

switches, causes = [], []
for a, b in zip(order[:-1], order[1:]):
    e = expiry(a)
    if e < days.min() or e > days.max():
        continue
    idx = (days <= e).sum() - 1
    W = days[idx - WIN_DAYS + 1: idx + 1]
    x = df[df.symbol.isin([a, b]) & df.td.isin(W)]
    m = x.pivot_table(index="ts_event", columns="symbol", values="volume", aggfunc="sum").reindex(columns=[a, b]).fillna(0)
    m = m.reindex(pd.date_range(m.index.min(), m.index.max(), freq="1min"), fill_value=0)
    td = trading_day(m.index); w = in_window(m.index)
    base = pd.Series(td, index=m.index).map(lambda d: sel.get(d, a)).eq(b).values
    cum = m.groupby(td)[[a, b]].cumsum().shift(1).fillna(0)
    trig = ((cum[b] > cum[a]) & (cum.sum(axis=1) >= MIN_CUM)).values
    pend, t_sw, cause = False, None, None
    for i in range(len(m)):
        pend = pend or trig[i]
        if base[i]:
            t_sw, cause = m.index[i], "J-1"; break
        if pend and not w[i]:
            t_sw, cause = m.index[i], "intraday"; break
    assert t_sw is not None, (a, b)
    assert not (cause == "intraday" and in_window(pd.DatetimeIndex([t_sw]))[0])
    switches.append((a, b, t_sw)); causes.append(cause)

# assemblage des segments
first_td = sel.index[0]
t0 = df.loc[df.td == first_td, "ts_event"].min()
bounds = [(sel.iloc[0], t0)] + [(b, t) for _, b, t in switches]
parts = []
for k, (sym, t_start) in enumerate(bounds):
    t_end = bounds[k + 1][1] if k + 1 < len(bounds) else df.ts_event.max() + pd.Timedelta(minutes=1)
    s = df[(df.symbol == sym) & (df.ts_event >= t_start) & (df.ts_event < t_end)].copy()
    s["roll"] = 0
    if k > 0 and len(s):
        s.iloc[0, s.columns.get_loc("roll")] = 1
    parts.append(s)
cont = pd.concat(parts).sort_values("ts_event").reset_index(drop=True)
assert cont.ts_event.is_unique

# ecarts aux rolls : dernier close connu de chaque contrat avant la minute de bascule
by_sym = {s: g.set_index("ts_event").close for s, g in df.groupby("symbol")}
rolls = []
for (a, b, t), cause in zip(switches, causes):
    ca, cb = by_sym[a][:t - pd.Timedelta(minutes=1)], by_sym[b][:t - pd.Timedelta(minutes=1)]
    rolls.append(dict(roll_ts=t, old=a, new=b, close_old=ca.iloc[-1], close_new=cb.iloc[-1],
                      age_old_min=(t - ca.index[-1]).total_seconds() / 60, age_new_min=(t - cb.index[-1]).total_seconds() / 60,
                      gap=cb.iloc[-1] - ca.iloc[-1], ratio=cb.iloc[-1] / ca.iloc[-1], cause=cause))
rolls = pd.DataFrame(rolls)

# back-adjustment : on remonte le temps, le prix le plus recent reste le prix reel
PX = ["open", "high", "low", "close"]
seg = cont["roll"].cumsum().values                                   # 0..n_rolls ; segment k precede le roll k+1
n = len(rolls)
add = np.zeros(n + 1); mul = np.ones(n + 1)
for k in range(n - 1, -1, -1):                                       # segment k est ajuste de tous les ecarts des rolls k..n-1
    add[k] = add[k + 1] + rolls.gap.iloc[k]
    mul[k] = mul[k + 1] * rolls.ratio.iloc[k]
brut = cont[["ts_event", "symbol"] + PX + ["volume", "roll"]]
diff = brut.copy(); diff[PX] = diff[PX].add(add[seg], axis=0).round(2)
ratio = brut.copy(); ratio[PX] = ratio[PX].mul(mul[seg], axis=0).round(4)
for name, d in (("brut", brut), ("diff", diff), ("ratio", ratio)):
    d.to_csv(f"{OUT}/MNQ_1m_continu_{name}.csv", index=False, date_format="%Y-%m-%dT%H:%M:%SZ")
rolls.round(4).to_csv(f"{OUT}/MNQ_rolls.csv", index=False)
print("barres", len(cont), "rolls", n, "du", cont.ts_event.min(), "au", cont.ts_event.max())
