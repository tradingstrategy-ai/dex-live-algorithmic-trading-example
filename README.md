This is an example repository for setting up a `docker-compose.yml` for a minimal live [trade-executor](https://github.com/tradingstrategy-ai/trade-executor/).

- This allows you to start a live trade execution
- It is a limited preproduction environment with devops and web frontend missing
- You are able to start a live trading strategy
- We use Polygon environment as it is an easy and transaction cost-friendly way to do some test trades
- [See the documentation for more information](https://tradingstrategy.ai/docs/deployment/hot-wallet-deployment.html)

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

Getting started
---------------

