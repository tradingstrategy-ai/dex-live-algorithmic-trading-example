"""BTC VWAP based breakout strategy.

Based on eth-breakout 1h notebook in getting-started repo.

To backtest this strategy module locally:

.. code-block:: console

    docker-compose run arbitrum-btc-breakout backtest

    trade-executor \
        backtest \
        --strategy-file=strategies/arbitrum-btc-breakout.py \  # TODO
        --trading-strategy-api-key=$TRADING_STRATEGY_API_KEY


"""

import datetime

import pandas as pd
import pandas_ta

from tradingstrategy.lending import LendingProtocolType, LendingReserveDescription
from tradingstrategy.pair import HumanReadableTradingPairDescription
from tradingstrategy.utils.groupeduniverse import resample_candles
from tradingstrategy.chain import ChainId
from tradingstrategy.client import Client
from tradingstrategy.timebucket import TimeBucket

from tradeexecutor.analysis.regime import Regime
from tradeexecutor.strategy.pandas_trader.indicator import IndicatorSet, IndicatorSource, IndicatorDependencyResolver
from tradeexecutor.strategy.parameters import StrategyParameters
from tradeexecutor.strategy.trading_strategy_universe import TradingStrategyUniverse
from tradeexecutor.strategy.execution_context import ExecutionContext, ExecutionMode
from tradeexecutor.strategy.trading_strategy_universe import load_partial_data
from tradeexecutor.strategy.universe_model import UniverseOptions
from tradeexecutor.strategy.default_routing_options import TradeRouting
from tradeexecutor.strategy.cycle import CycleDuration
from tradeexecutor.state.visualisation import PlotKind
from tradeexecutor.state.trade import TradeExecution
from tradeexecutor.strategy.pandas_trader.strategy_input import StrategyInput
from tradeexecutor.utils.binance import create_binance_universe


trading_strategy_engine_version = "0.5"


class Parameters:
    """Parameteres for this strategy.

    - Collect parameters used for this strategy here

    - Both live trading and backtesting parameters
    """

    id = "arbitrum-btc-breakout-vwap-1d" # Used in cache paths

    cycle_duration = CycleDuration.cycle_1d
    candle_time_bucket = TimeBucket.d1
    allocation = 0.98

    adx_length = 30
    adx_filter_threshold = 0

    trailing_stop_loss_pct = 0.98
    trailing_stop_loss_activation_level = 1.075
    stop_loss_pct = 0.98
    take_profit_pct = 1.10

    #
    # Live trading only
    #
    chain_id = ChainId.arbitrum
    routing = TradeRouting.default  # Pick default routes for trade execution
    required_history_period = datetime.timedelta(hours=200)

    #
    # Backtesting only
    #
    # Because the underlying DEX live trading market does not have enough
    # history, we perform the backtesting using Binance data.
    #

    backtest_start = datetime.datetime(2022, 10, 1)
    backtest_end = datetime.datetime(2024, 5, 1)

    stop_loss_time_bucket = TimeBucket.m5
    initial_cash = 10_000


def get_strategy_trading_pairs(execution_mode: ExecutionMode) -> tuple[list[HumanReadableTradingPairDescription], list[LendingReserveDescription] | None]:
    trading_pairs = [
        (ChainId.arbitrum, "uniswap-v3", "WBTC", "USDC", 0.0005),
    ]

    lending_reserves = [
        (ChainId.arbitrum, LendingProtocolType.aave_v3, "USDC.e"),
    ]

    return trading_pairs, lending_reserves


def create_trading_universe(
    timestamp: datetime.datetime,
    client: Client,
    execution_context: ExecutionContext,
    universe_options: UniverseOptions,
) -> TradingStrategyUniverse:
    """Create the trading universe.

    - For live trading, we load DEX data
    """
    trading_pairs, lending_reserves = get_strategy_trading_pairs(execution_context.mode)

    if execution_context.mode.is_backtesting():
        required_history_period = None
    else:
        required_history_period = Parameters.required_history_period

    dataset = load_partial_data(
        client=client,
        time_bucket=Parameters.candle_time_bucket,
        pairs=trading_pairs,
        execution_context=execution_context,
        universe_options=universe_options,
        liquidity=False,
        stop_loss_time_bucket=Parameters.stop_loss_time_bucket,
        lending_reserves=lending_reserves,
        required_history_period=required_history_period,
    )
    # Construct a trading universe from the loaded data,
    # and apply any data preprocessing needed before giving it
    # to the strategy and indicators
    strategy_universe = TradingStrategyUniverse.create_from_dataset(
        dataset,
        reserve_asset="USDC",
        forward_fill=True,
    )

    return strategy_universe


def regime(
    open,
    high,
    low,
    close,
    length,
    regime_threshold,
    dependency_resolver: IndicatorDependencyResolver,
) -> pd.Series:
    """A regime filter based on ADX indicator.

    Get the trend of BTC applying ADX on a daily frame.
    
    - -1 is bear
    - 0 is sideways
    - +1 is bull
    """
    def regime_filter(row):
        # ADX, DMP, # DMN
        average_direction_index, directional_momentum_positive, directional_momentum_negative = row.values

        # We use a threshold to eliminate the noise in ADX,
        # but the threshold can be also zero
        if average_direction_index > regime_threshold:
            # In this case the filter is that if ADX positive is higher than ADX negative,
            # we bullish
            if directional_momentum_positive > directional_momentum_negative:
                return Regime.bull.value
            else:
                return Regime.bear.value
        else:
            return Regime.crab.value

    adx_df = dependency_resolver.get_indicator_data(
        "adx",
        parameters={"length": length},
        column="all",
    )
    regime_signal = adx_df.apply(regime_filter, axis="columns")    
    return regime_signal



def create_indicators(
    timestamp: datetime.datetime | None,
    parameters: StrategyParameters,
    strategy_universe: TradingStrategyUniverse,
    execution_context: ExecutionContext
):
    indicators = IndicatorSet()

    # https://github.com/twopirllc/pandas-ta/blob/main/pandas_ta/overlap/vwap.py
    indicators.add(
        "vwap",
        pandas_ta.vwap,
        {},
        IndicatorSource.ohlcv,
    )

    # ADX https://www.investopedia.com/articles/trading/07/adx-trend-indicator.asp
    # https://github.com/twopirllc/pandas-ta/blob/main/pandas_ta/trend/adx.py
    indicators.add(
        "adx",
        pandas_ta.adx,
        {"length": parameters.adx_length},
        IndicatorSource.ohlcv,
        order=1,
    )

    # A regime filter to detect the trading pair bear/bull markets
    indicators.add(
        "regime",
        regime,
        {"length": parameters.adx_length, "regime_threshold": parameters.adx_filter_threshold},
        IndicatorSource.ohlcv,
        order=2,
    )

    return indicators


def decide_trades(
    input: StrategyInput,
) -> list[TradeExecution]:

    # 
    # Decidion cycle setup.
    # Read all variables we are going to use for the decisions.
    #
    parameters = input.parameters
    position_manager = input.get_position_manager()
    state = input.state
    timestamp = input.timestamp
    indicators = input.indicators
    strategy_universe = input.strategy_universe

    pair = strategy_universe.get_single_pair()
    cash = position_manager.get_current_cash()

    #
    # Indicators
    #

    close_price = indicators.get_price()
    vwap = indicators.get_indicator_value("vwap")
    regime_val = indicators.get_indicator_value("regime")
 
    if None in (vwap, close_price):
        # Not enough historic data,
        # cannot make decisions yet
        return []
    
    # If regime filter does not have enough data at the start of the backtest, default to crab market
    if regime_val is None:
        regime = Regime.crab
    else:
        regime = Regime(regime_val)  # Convert to enum for readability
        
    #
    # Trading logic
    #

    trades = []

    # Check for open condition - is the price breaking out
    #
    if not position_manager.is_any_long_position_open():
        if regime == Regime.bull:
            if close_price > vwap:
                # Unwind credit position to have cash to take a directional position
                if position_manager.is_any_credit_supply_position_open():
                    credit_supply_position = position_manager.get_current_credit_supply_position()
                    trades += position_manager.close_credit_supply_position(credit_supply_position)
                    cash = float(credit_supply_position.get_quantity())

                trades += position_manager.open_spot(
                    pair,
                    value=cash * parameters.allocation,
                    stop_loss_pct=parameters.stop_loss_pct,             
                )
    else:        
        # Enable trailing stop loss after we reach the profit taking level
        #
        for position in state.portfolio.open_positions.values():
            if position.is_spot() and position.trailing_stop_loss_pct is None:
                close_price = indicators.get_price(position.pair)
                if close_price >= position.get_opening_price() * parameters.trailing_stop_loss_activation_level:
                    position.trailing_stop_loss_pct = parameters.trailing_stop_loss_pct

    # Move all cash to to Aave credit to earn interest
    if not position_manager.is_any_credit_supply_position_open() and not position_manager.is_any_long_position_open():
        amount = cash * 0.9999
        trades += position_manager.open_credit_supply_position_for_reserves(amount)

    # Visualisations
    #
    if input.is_visualisation_enabled():
        visualisation = state.visualisation
        visualisation.plot_indicator(timestamp, "VWAP", PlotKind.technical_indicator_on_price, vwap)

    return trades  # Return the list of trades we made in this cycle

