# Execution Engine

The execution layer of the Sprint 9 backtesting platform models
professional order management: advanced order types, a strict lifecycle
state machine, pluggable slippage models, and a configurable commission
engine with taxes and stamp duty.

## Order types

| Type | Trigger | Behaviour |
|---|---|---|
| `MARKET` | immediate | Fills at the bar reference price + slippage |
| `LIMIT` | price constraint | Fills only at/through the limit price |
| `STOP` | stop level | Becomes market once the stop is touched |
| `STOP_LIMIT` | stop + limit | Stop triggers, then limit caps the price |
| `TRAILING_STOP` | ratcheting stop | Stop trails the market each bar |
| `BRACKET` | entry + exits | Entry market order + take-profit + stop-loss (OCO) |
| `OCO` | one-cancels-other | Two legs; filling one cancels the other |
| `IOC` | immediate or cancel | Remaining quantity cancelled after the bar |
| `FOK` | fill or kill | Only fills if the full quantity is available |

Time-in-force policies: `DAY`, `GTC`, `GTD` (with `expires_at`), `IOC`,
`FOK`.

## Lifecycle

```
CREATED → SUBMITTED → ACCEPTED → PARTIALLY_FILLED → FILLED
                          ↘ CANCELLED / REJECTED / EXPIRED
```

`OrderManager`:

- `submit(order)` / `accept(order)` / `cancel(order)` / `reject(order, reason)` / `expire(order)`
- `bracket(...)` / `oco(...)` / `ioc(...)` / `fok(...)` / `gtd(...)` / `trailing_stop(...)`
- `cancel_oco_siblings(order)` — cancels partners when one leg fills
- `expire_gtd_orders(now)` — expires past-dated GTD orders
- `update_trailing_stops(order, price)` — ratchets trailing stops

## Slippage models

All models implement `SlippageModel.compute(order, bar, base_price, context)`
returning the adverse adjustment. Buys pay `base + slip`; sells receive
`base − slip`.

| Model | Parameterisation |
|---|---|
| `fixed` | `amount` per share |
| `percentage` | `rate` fraction of price |
| `volume` | `base_rate` + participation (`quantity / bar.volume`) |
| `volatility` | `vol_factor` × intraday range |
| `spread` | half-spread (`spread_pct` / 2) |
| `random` | uniform between `base_rate` and `max_rate` (seeded) |

Build via `build_slippage_model(name, **params)`.

## Commission engine

Models implement `CommissionModel.compute(quantity, price, context)`
returning `(commission, exchange_fee)`.

| Model | Description |
|---|---|
| `flat` | Fixed per-execution fee |
| `percentage` | Fraction of executed value (+ minimum) |
| `tiered` | Step-down rate tiers by executed value |
| `broker` | NEPSE-style broker + exchange + SEBON rates |

`TaxPolicy(tax_rate, stamp_duty, order_fee)` layers tax and stamp duty
on top. Fills charge the **full** `CommissionResult.total` (commission +
exchange fee + tax + stamp duty) to the account.

Build via `build_commission_model(name, **params)`.

## Example

```python
from src.backtesting.orders import OrderManager
from src.backtesting.models import OrderSide, OrderType

m = OrderManager()
entry, tp, sl = m.bracket("NABIL", OrderSide.BUY, 100, entry_price=500.0,
                          take_profit=560.0, stop_loss=475.0)
# tp and sl share an OCO group; filling one cancels the other.
```
