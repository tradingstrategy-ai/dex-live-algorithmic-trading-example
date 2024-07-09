This is an example repository for setting up a `docker-compose.yml` for a minimal live [trade-executor](https://github.com/tradingstrategy-ai/trade-executor/).

- This allows you to start a live trade execution in your computer using Docker
- It is a limited preproduction environment with devops and web frontend missing
- We use Polygon environment as it is an easy and cost-friendly way to do some test trades
- The folder structure is set up in a manner you can run several trade executors under the same `docker-compose.yml` configuration

For further iformation

- [See the documentation](https://tradingstrategy.ai/docs/deployment/hot-wallet-deployment.html)

Preface
-------

First before doing the production deployment, it is a good idea to practice the deployment on a local laptop.

The preproduction set up can be more straightforward than the actual production deployment, as we shortcut few things which are the major share of the work for executing a production live trading strategy.

Missing things
- Security like managing the access to the private keys
- Web frontend
- Devops and diagnostics output
    - Only output is Docker process stdoud

- `[ ]` A repository tool where you are going to manage your configuration files (Github, Gitlab, etc.)
    - See the project structure details below
- `[ ]` Linux / macOS laptop (Windows should work, but the command line commmands differ so much, so it is unsupported)
- `[ ]` :ref:`Docker installation with Docker compose <managing docker images>`
- `[ ]` Convert your backtest notebook :ref:`to a Python strategy module <>`


# Step 1: Develop a trading strategy

See [Getting started](https://github.com/tradingstrategy-ai/getting-started) if you do not have a trading strategy yet.

The usual deliverables of developing a trading strategy include: 

1. Initial backtest notebook
2. Optimiser that crunches through multiple parameters
2. Final backtest notebook with fixed parameters you are prepared to take to the live trading

For this example we have 

1. Initial backtest - somewhere in [Getting started repo](https://github.com/tradingstrategy-ai/getting-started)
2. Optimiser notebook. [See here](./notebooks/eth-breakout-optimiser.ipynb)
3. Final backtest. [See here](./notebooks/eth-breakout-dex-final.ipynb)

# Step 2: Give the strategy id

You are going to have a lot of strategies. You need to have a systematic way to keep track of them.

We are going to us `hotwallet-polygon-eth-usdc-breakout`. It's a mouthful, but self-explanatory.

This id is used in

- URLs
- State file
- Log files 
- etc.

# Step 3: Extract strategy as a Python module

We convert the final backtest notebook to a Python module. We use (3) from the step above as the starting point.

Rules to convert

- Create a Python file
- Copy-paste the strategy from the notebook 
  - `Parameteters` class
  - `decide_trades`
  - `create_indicators`
  - `create_strategy_universe`
- Autocomplete the imports for the Python file
  - Usually a good editor like Visual Studio Code or PyCharm can do this for you with a keypress or two
- Modify `create_strategy_universe` to cater to both the backtesting and live trading
  - Different options are available if you want to show DEX backtesting data (usually too short period)
- Add the following Python module variables 

```python
trading_strategy_engine_version = "0.5"
name = "ETH-BTC-USDC momentum"  # Optional: Frontend metadata
tags = {StrategyTag.beta, StrategyTag.live}  # Optional: Frontend metadata
icon = ""  # Optional: Frontend metadata
short_description = ""  # Optional: Frontend metadata
long_description = ""  # Optional: Frontend metadata
```

We also edit the strategy to dynamically decide which trading universe to use
- Binance for backtesting (longer history)
- DEX for live trading

Example modification here:

```python
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
```

- [See the full resulting Python file here](./strategies/hotwallet-polygon-eth-usdc-breakout.py) 

# Step 4: Set up docker compose entry

- [See docker-compose.yml](./docker-compose.yml)

**Note**: We use new style `docker compose` commands in this README, instead of `docker-compose` legacy command (with dash). 
Make sure you have the latest version.

# Step 5: Set up an environment file

Each trade executor docker is configured using environment variable files using the normal Docker conventions.

**Note**: For the production deployment it is recommend against putting any highly sensitive material like private keys
directly to these files/

- See [hotwallet-polygon-eth-usdc-breakout.env](./hotwallet-polygon-eth-usdc-breakout.env)

The file comes with

- No hot wallet/private key set up
- Public low quality Polygon RPC endpoint (do not use for production - likely just keeps crashing)

# Step 6: Run backtest with the strategy module

This will run the strategy backtest using `docker compose` command.

- This command will execute the same backtest as you would have runned earlier in the final notebook
- Python errors when running this command will catch any mistakes made during the strategy module creation
- Backtest artifacts are written under `state/` folder
- The same artifacts will be served in the web frontend for the users who want to see the backtest results

First we need to choose the trade-executor release. The trade executor is under active development
and may see multiple releases per day. [You can find releases on Github](https://github.com/tradingstrategy-ai/trade-executor/pkgs/container/trade-executor)

Then we try to run and verify our Docker Compose launches.

```shell
source scripts/export-latest-trade-executor-version.sh
docker compose run hotwallet-polygon-eth-usdc-breakout --help
```

You should see the command line help for [trade-executor command](https://tradingstrategy.ai/docs/deployment/trade-executor.html):

```
Usage: trade-executor [OPTIONS] COMMAND [ARGS]...

Options:
  --install-completion [bash|zsh|fish|powershell|pwsh]
                                  Install completion for the specified shell.
  --show-completion [bash|zsh|fish|powershell|pwsh]
                                  Show completion for the specified shell, to copy it or customize the installation.
  --help                          Show this message and exit.
...
```

Then you need to add your [Trading Strategy API key](https://tradingstrategy.ai/trading-view/api) in [./env/hotwallet-polygon-eth-usdc-breakout.env](./env/hotwallet-polygon-eth-usdc-breakout.env):

Now run the backtest:

```shell
docker compose run hotwallet-polygon-eth-usdc-breakout backtest
```

You are likely to encounter several Python bugs in this step, so keep fixing your Python strategy module.

The end of the output should look like:

```
Writing backtest data the state file: /usr/src/trade-executor/state/hotwallet-polygon-eth-usdc-breakout-backtest.json
Exporting report, notebook: state/hotwallet-polygon-eth-usdc-breakout-backtest.ipynb, HTML: state/hotwallet-polygon-eth-usdc-breakout-backtest.html
```

After the backtest is complete, you can view the HTML report:

```shell
# macOS way to open a HTML file from the command line
open state/hotwallet-polygon-eth-usdc-breakout-backtest.html
```

You should see the backtest results, as captured from the default backtest notebook template.

# Step 7: Set up a hot wallet

You need a hot wallet

- It costs ETH/MATIC/etc. to broadcast the transactions for your trades
- A hot wallet is just an EVM account with the associated private key 

## Step 7.a: Generate a private key

To create a hot wallet for the executor you can [do it from the command line](https://ethereum.stackexchange.com/questions/82926/how-to-generate-a-new-ethereum-address-and-private-key-from-a-command-line):

```shell
head -c 32 /dev/urandom|xxd -ps -c 32
```

This will give you a private key (example - do not use this private key):

```
68f4e1be83e2bd242d1a5a668574dd3b6b76a29f254b4ae662eba5381d1fc3a6
```

Then

- Store the private key safely in your backup storage (password manager, or something stronger depending on the security level)

**Note**: Hot wallets cannot be shared across different `trade-executor` instances, because this will mess up accounting.

## Step 7.b: Add the private key environment variable file

Private key will be needed in the trade execution configuration file

- Edit `hotwallet-polygon-eth-usdc-breakout.env`.
- Add `0x` prefix to the raw hex output from the step above
- Fill in `PRIVATE_KEY`.

Example:

```shell
# Do not use this private key 
PRIVATE_KEY=0x68f4e1be83e2bd242d1a5a668574dd3b6b76a29f254b4ae662eba5381d1fc3a6
```

## Step 7.c Check the wallet

`trade-executor` provides the subcommand `check-wallet` to check the hot wallet status.

This checks

- You are connected to the right blockchain

- Your hot wallet private key has been correctly set up

- You have native token for gas fees

- You have trading capital

- The last block number of the blockchain

- We know how to route trades for our strategy, using the current wallet


```shell
docker compose run hotwallet-polygon-eth-usdc-breakout check-wallet
```
    
Output:

```
RPC details
    Chain id is 56
    Latest block is 23,387,643
    Balance details
    Hot wallet is ...
    We have 0.370500 gas money left
    Reserve asset: USDC
    Balance of USD Coin: 500 USDC
    Estimated gas fees for chain 56: <Gas pricing method:legacy base:None priority:None max:None legacy:None>
    Execution details
    Execution model is tradeexecutor.ethereum.uniswap_v2_execution.UniswapV2ExecutionModel
    Routing model is tradeexecutor.ethereum.uniswap_v2_routing.UniswapV2SimpleRoutingModel
    Token pricing model is tradeexecutor.ethereum.uniswap_v2_live_pricing.UniswapV2LivePricing
    Position valuation model is tradeexecutor.ethereum.uniswap_v2_valuation.UniswapV2PoolRevaluator
Routing details
    Factory 0xca143ce32fe78f1f7019d7d551a6402fc5350c73 uses router 0x10ED43C718714eb63d5aA57B78B54704E256024E
    Routed reserve asset is <0x8ac76a51cc950d9822d68b83fe1ad97b32cd580d at 0x8ac76a51cc950d9822d68b83fe1ad97b32cd580d>

```

## Step 7.c: Fund the account

We need 

- MATIC to cover the gas fees: get 25 MATIC.
- USDC.e: get 25 USDC.e.

You can use a service like [Transak](https://transak.com/) to get MATIC with a debit card. KYC needed.

Send MATIC and USDC.e to the address you saw above.

**Note**: Due to historical reasons we use USDC.e (bridged variant) over native USDC on Polygon,
because it has better liquidity. These two tokens are not fungible as the writing of this.

## Step 7.d: Check the wallet again

Now you should have some MATIC and USDC.e in your hot wallet.


```shell
docker compose run hotwallet-polygon-eth-usdc-breakout check-wallet
```
    
We see the account is funded now:

```

```

## Step 8: Check the trading universe

`trade-executor` provides the subcommand `check-universe` to ensure the market data feeds work correctly.

- This confirms your Trading Strategy oracle API keys are correctly set up
  and your strategy can receive data.

- The market data feed is up-to-date

You can run this with configured `docker-compose` as:

```shell
docker compose run pancake-eth-usd-sma check-universe
```

This will print out:

```
Latest OHCLV candle is at: 2022-11-24 16:00:00, 1:49:57.985345 ago
```

## Step 9: Perform test trade

After you are sure that trading data and hot wallet are fine,
you can perform a test trade from the command line.

- This will ensure trade routing and execution gas fee methods
  are working by executing a live trade against live blockchain.

- The test trade will buy and sell the "default" asset of the strategy
  worth 1 USD. For a single pair strategies the asset is the default
  base token.

- This will open a position using the strategy's exchange and trade
  pair routing.

- The position and the trade will have notes field filled in that
  this was a test trade.

- Broadcasting a transaction through your JSON-RPC connection
  works.

Example:

```shell
docker-compose run pancake-eth-usd-sma perform-test-trade
```


```
...
Making a test trade on pair: <Pair ETH-USDC at 0xea26b78255df2bbc31c1ebf60010d78670185bd0 on exchange 0xca143ce32fe78f1f7019d7d551a6402fc5350c73>, for 1.000000 USDC price is 1217.334094 ETH/USDC
...
Position <Open position #2 <Pair ETH-USDC at 0xea26b78255df2bbc31c1ebf60010d78670185bd0 on exchange 0xca143ce32fe78f1f7019d7d551a6402fc5350c73> $1.000501504460405> open. Now closing the position.
...
All ok
```

## Step 11: Launch the live strategy

Now you are ready to start the strategy.

We first suggest to start on foreground.

```shell
docker compose up -d     
```

The given strategy rebalances every 1h. So you should see something working or not working within 1h.

## Step 12: Configure additional RPC providers (optional)

## Step 13: Set up Discord logging (optional)

