from __future__ import annotations

from gemini_stock.schemas import GeminiSignal


MIN_ACTIONABLE_RR = 1.2
MIN_ACTIONABLE_MOVE_RATIO = 0.003
MIN_ACTIONABLE_MOVE_ABS = 0.05


def has_actionable_trade_plan(signal: GeminiSignal) -> bool:
    return has_actionable_trade_levels(
        setup_type=signal.setup_type,
        entry_zone=signal.entry_zone,
        stop_loss=signal.stop_loss,
        take_profit=signal.take_profit,
        risk_reward_ratio=signal.risk_reward_ratio,
    )


def has_actionable_trade_levels(
    setup_type: str | None,
    entry_zone: list[float] | None,
    stop_loss: float | None,
    take_profit: list[float] | None,
    risk_reward_ratio: float | None,
) -> bool:
    if setup_type == "no_trade":
        return False
    if not entry_zone or len(entry_zone) < 2:
        return False
    if stop_loss is None:
        return False
    if not take_profit or len(take_profit) < 2:
        return False

    entry_low, entry_high = _normalize_zone(entry_zone)
    target_low, target_high = _normalize_zone(take_profit)
    entry_mid = (entry_low + entry_high) / 2
    target_mid = (target_low + target_high) / 2
    if entry_mid <= 0:
        return False

    target_move = abs(target_mid - entry_mid)
    min_move = max(entry_mid * MIN_ACTIONABLE_MOVE_RATIO, MIN_ACTIONABLE_MOVE_ABS)
    if target_move < min_move:
        return False

    risk = abs(entry_mid - float(stop_loss))
    if risk <= 0:
        return False

    derived_rr = target_move / risk
    rr = float(risk_reward_ratio) if risk_reward_ratio is not None else derived_rr
    if rr <= 0:
        rr = derived_rr
    return rr >= MIN_ACTIONABLE_RR


def _normalize_zone(zone: list[float]) -> tuple[float, float]:
    low, high = sorted(float(value) for value in zone[:2])
    return low, high
