"""Калькулятор мартингейла для бинарных опционов.

Выплата payout задаётся долей: 0.85 означает чистую прибыль 85% от ставки при выигрыше.
Ставка после проигрыша считается одним из двух способов:
  recover: вернуть все прошлые потери и получить прибыль первой ставки,
  mult:    умножить предыдущую ставку на фиксированный коэффициент.
"""


def breakeven(payout):
    """Минимальный процент выигрышей, при котором ожидаемый результат ставки равен нулю."""
    return 1 / (1 + payout)


def plan(deposit, first, payout, steps, mode="recover", mult=2.2, winrate=0.55):
    rows, loss = [], 0.0
    for k in range(1, steps + 1):
        if k == 1:
            stake = first
        elif mode == "mult":
            stake = rows[-1]["stake"] * mult
        else:
            stake = (loss + first * payout) / payout
        stake = round(stake, 2)
        total = loss + stake
        rows.append({"k": k, "stake": stake, "loss_before": loss, "total": total,
                     "profit": stake * payout - loss,            # чистый результат серии, если выиграли на этой ступени
                     "p_reach": (1 - winrate) ** (k - 1), "afford": total <= deposit})
        loss = total
    affordable = 0
    for r in rows:
        if not r["afford"]:
            break
        affordable += 1
    p_bust = (1 - winrate) ** steps
    ev = sum(winrate * (1 - winrate) ** (r["k"] - 1) * r["profit"] for r in rows) - p_bust * loss
    return {"rows": rows, "affordable": affordable, "p_bust": p_bust, "series_loss": loss,
            "ev": ev, "breakeven": breakeven(payout)}


def max_first_stake(deposit, payout, steps, mode="recover", mult=2.2):
    """Наибольшая первая ставка, при которой депозит выдерживает все steps проигрышей подряд."""
    unit = plan(1e12, 1.0, payout, steps, mode, mult)["series_loss"]
    return deposit / unit if unit else 0.0
