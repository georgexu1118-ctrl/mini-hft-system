"""Dashboard control-plane endpoints for isolated strategy instances."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from api.dependencies import get_strategy_runtime
from strategy.examples.market_maker import SimpleMarketMaker
from strategy.risk import StrategyRiskGuard, StrategyRiskLimits
from strategy.runtime import StrategyRuntime

router = APIRouter()


class MarketMakerRequest(BaseModel):
    strategy_id: str = Field(..., min_length=1, max_length=100)
    symbol: str
    spread_ticks: float = Field(0.05, gt=0)
    quote_size: int = Field(100, gt=0)
    inventory_skew_ticks: float = Field(0.10, ge=0)
    max_position: int = Field(500, gt=0)
    max_order_notional: float = Field(1_000_000.0, gt=0)


@router.get("/", summary="Strategy metrics and PnL")
async def list_strategies(runtime: StrategyRuntime = Depends(get_strategy_runtime)) -> dict:
    return {"strategies": runtime.strategy_metrics()}


@router.post("/market-makers", status_code=status.HTTP_201_CREATED)
async def create_market_maker(
    request: MarketMakerRequest,
    runtime: StrategyRuntime = Depends(get_strategy_runtime),
) -> dict:
    strategy = SimpleMarketMaker(
        strategy_id=request.strategy_id,
        symbol=request.symbol.upper(),
        spread_ticks=request.spread_ticks,
        quote_size=request.quote_size,
        inventory_skew_ticks=request.inventory_skew_ticks,
        max_position=request.max_position,
    )
    try:
        runtime.add_strategy(
            strategy,
            StrategyRiskGuard(StrategyRiskLimits(
                max_order_size=request.quote_size,
                max_position=request.max_position,
                max_notional_per_order=request.max_order_notional,
            )),
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    runtime.start_strategy(request.strategy_id)
    return {"strategy_id": request.strategy_id, "status": "RUNNING"}


@router.post("/{strategy_id}/kill", summary="Engage a strategy kill switch")
async def kill_strategy(
    strategy_id: str,
    runtime: StrategyRuntime = Depends(get_strategy_runtime),
) -> dict:
    if strategy_id not in runtime.strategies:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="strategy not found")
    runtime.engage_kill_switch(strategy_id)
    return {"strategy_id": strategy_id, "status": "KILLED"}
