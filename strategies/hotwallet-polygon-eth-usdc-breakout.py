"""Strategy module.

- Live trade execution module, converted from eth-breakout-dex-final.ipynb notebook
"""

import datetime

import pandas as pd
import pandas_ta

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
from tradeexecutor.strategy.tag import StrategyTag
from tradeexecutor.state.identifier import TradingPairIdentifier


class Parameters:

    id = "hotwallet-polygon-eth-usdc-breakout" 

    cycle_duration = CycleDuration.cycle_1h 
    candle_time_bucket = TimeBucket.h1
    allocation = 0.98

    atr_length = 17
    fract = 2.5
    adx_length = 60
    adx_filter_threshold = 48

    trailing_stop_loss_pct = 0.96
    trailing_stop_loss_activation_level = 1.07
    stop_loss_pct = 0.97
    take_profit_pct = 1.10

    #
    # Live trading only
    #
    chain_id = ChainId.polygon
    routing = TradeRouting.default  # Pick default routes for trade execution
    required_history_period = datetime.timedelta(hours=adx_length*2)

    #
    # Backtesting only
    #

    backtest_start = datetime.datetime(2019, 6, 1)
    backtest_end = datetime.datetime(2024, 6, 1)
    stop_loss_time_bucket = TimeBucket.m15
    initial_cash = 10_000


def get_strategy_trading_pairs(execution_mode: ExecutionMode) -> list[HumanReadableTradingPairDescription]:
    if execution_mode.is_backtesting():
        # Need longer history
        trading_pairs = [
            (ChainId.centralised_exchange, "binance", "ETH", "USDT"),
        ]

    else:
        # For live trading we do Uniswap v3 on Polygon
        trading_pairs = [
            (ChainId.polygon, "uniswap-v3", "WETH", "USDC", 0.0005),
        ]
    return trading_pairs


def create_trading_universe(
    timestamp: datetime.datetime,
    client: Client,
    execution_context: ExecutionContext,
    universe_options: UniverseOptions,
) -> TradingStrategyUniverse:
    """Create the trading universe."""
    trading_pairs = get_strategy_trading_pairs(execution_context.mode)

    if execution_context.mode.is_backtesting():
        # Backtesting - load Binance data to get longer history
        strategy_universe = create_binance_universe(
            [f"{p[2]}{p[3]}" for p in trading_pairs],
            candle_time_bucket=Parameters.candle_time_bucket,
            stop_loss_time_bucket=Parameters.stop_loss_time_bucket,
            start_at=universe_options.start_at,
            end_at=universe_options.end_at,
            forward_fill=True,
        )        

    else:
        # How many bars of live trading data needed
        required_history_period = Parameters.required_history_period

        dataset = load_partial_data(
            client=client,
            time_bucket=Parameters.candle_time_bucket,
            pairs=trading_pairs,
            execution_context=execution_context,
            universe_options=universe_options,
            liquidity=False,
            stop_loss_time_bucket=Parameters.stop_loss_time_bucket,
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
    close: pd.Series,
    adx_length: int,
    regime_threshold: float,
    pair: TradingPairIdentifier,
    dependency_resolver: IndicatorDependencyResolver,
) -> pd.Series:
    """A regime filter based on ADX indicator.

    Get the trend of BTC applying ADX on a daily frame.
    
    - -1 is bear
    - 0 is sideways
    - +1 is bull
    """
    adx_df = dependency_resolver.get_indicator_data(
        "adx",
        pair=pair,
        parameters={"length": adx_length},
        column="all",
    )

    adx_df = adx_df.shift(1)  # Look ahead bias trick

    def regime_filter(row):
        # ADX, DMP, # DMN
        average_direction_index, directional_momentum_positive, directional_momentum_negative = row.values
        if average_direction_index > regime_threshold:
            if directional_momentum_positive > directional_momentum_negative:
                return Regime.bull.value
            else:
                return Regime.bear.value
        else:
            return Regime.crab.value
    regime_signal = adx_df.apply(regime_filter, axis="columns")    
    return regime_signal


def create_indicators(
    timestamp: datetime.datetime | None,
    parameters: StrategyParameters,
    strategy_universe: TradingStrategyUniverse,
    execution_context: ExecutionContext
):
    indicators = IndicatorSet()

    indicators.add(
        "atr",
        pandas_ta.atr,
        {"length": parameters.atr_length},
        IndicatorSource.ohlcv,
    )    

    indicators.add(
        "adx",
        pandas_ta.adx,
        {"length": parameters.adx_length},
        IndicatorSource.ohlcv,
    )

    # A regime filter to detect the trading pair bear/bull markets
    indicators.add(
        "regime",
        regime,
        {"adx_length": parameters.adx_length, "regime_threshold": parameters.adx_filter_threshold},
        IndicatorSource.close_price,
        order=3,
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
    cash = position_manager.get_current_cash()

    trading_pairs = get_strategy_trading_pairs(input.execution_context.mode)

    trades = []

    # Enable trailing stop loss after we reach the profit taking level
    #
    for position in state.portfolio.open_positions.values():
        if position.trailing_stop_loss_pct is None:
            close_price = indicators.get_price(pair=position.pair)
            if close_price >= position.get_opening_price() * parameters.trailing_stop_loss_activation_level:
                position.trailing_stop_loss_pct = parameters.trailing_stop_loss_pct                

    # Trade each of our pair individually.
    #
    # If any pair has open position,
    # do not try to rebalance, but hold that position to the end.
    #
    for pair_desc in trading_pairs:
        pair = strategy_universe.get_pair_by_human_description(pair_desc)

        close_price = indicators.get_price(pair=pair)  # Price the previous 15m candle closed for this decision cycle timestamp
        atr = indicators.get_indicator_value("atr", pair=pair)  # The ATR value at the time of close price
        # last_hour = (timestamp - parameters.cycle_duration.to_pandas_timedelta()).floor(freq="H")  # POI (point of interest): Account 15m of lookahead bias whehn using decision cycle timestamp
        last_hour = (timestamp - parameters.cycle_duration.to_pandas_timedelta()).floor(freq="4H")  # POI (point of interest): Account 15m of lookahead bias whehn using decision cycle timestamp
        previous_price = indicators.get_price(pair=pair, timestamp=last_hour)  # The price at the start of the breakout period
        regime_val = indicators.get_indicator_value("regime", pair=pair, data_delay_tolerance=pd.Timedelta(hours=24))  # Because the regime filter is calculated only daily, we allow some lookback
    
        if None in (atr, close_price, previous_price):
            # Not enough historic data,
            # cannot make decisions yet
            continue
        
        # If regime filter does not have enough data at the start of the backtest,
        # default to bull market
        regime = Regime(regime_val)  # Convert to enum for readability
                
        # We assume a breakout if our current 15m candle has closed
        # above the 1h starting price + (atr * fraction) target level
        long_breakout_entry_level = previous_price + atr * parameters.fract

        # Check for open condition - is the price breaking out
        #
        if not position_manager.is_any_open():
            if regime == Regime.bull:  
                if close_price > long_breakout_entry_level:
                    trades += position_manager.open_spot(
                        pair,
                        value=cash * parameters.allocation,
                        stop_loss_pct=parameters.stop_loss_pct,             
                    )

    # Visualisations
    #
    if input.is_visualisation_enabled():
        visualisation = state.visualisation
        # Visualise ATR for BTC        
        pair = strategy_universe.get_pair_by_human_description(trading_pairs[0])
        visualisation.plot_indicator(
            timestamp, 
            "ATR", 
            PlotKind.technical_indicator_detached, 
            atr,
            pair=pair,
        )

    return trades  # Return the list of trades we made in this cycle


#
# Define strategy module metadata.
#

trading_strategy_engine_version = "0.5"  # Allows to upgrade strategy file format

name = Parameters.id  # Optional: Frontend metadata

tags = {StrategyTag.beta, StrategyTag.live}  # Optional: Frontend metadata

icon = ""  # Optional: Frontend metadata

short_description = ""  # Optional: Frontend metadata

long_description = ""  # Optional: Frontend metadata