"""Prometheus metrics — one process-wide registry, exposed on
config.MonitoringConfig.prometheus_port via prometheus_client's built-in
HTTP server. Grafana dashboard JSON consuming these lives in
monitoring/grafana-dashboard.json."""

from __future__ import annotations

from prometheus_client import Counter, Gauge, Histogram, start_http_server

opportunities_detected_total = Counter(
    "arbitragex_opportunities_detected_total", "Raw opportunities detected by scan()", ["strategy"]
)
opportunities_executed_total = Counter(
    "arbitragex_opportunities_executed_total", "Opportunities that resulted in an open position", ["strategy"]
)
orders_rejected_total = Counter(
    "arbitragex_orders_rejected_total", "Orders rejected by risk or opportunity agents", ["strategy", "reason"]
)
order_fill_latency_seconds = Histogram(
    "arbitragex_order_fill_latency_seconds", "Time from execute() call to fill confirmation", ["strategy"]
)
portfolio_equity_usd = Gauge("arbitragex_portfolio_equity_usd", "Current total portfolio equity in USD")
portfolio_drawdown_pct = Gauge("arbitragex_portfolio_drawdown_pct", "Current drawdown from peak equity, percent")
open_positions_count = Gauge("arbitragex_open_positions_count", "Number of currently open positions", ["strategy"])
margin_utilization_pct = Gauge("arbitragex_margin_utilization_pct", "Margin utilization percent", ["exchange", "symbol"])
var_usd = Gauge("arbitragex_var_usd", "Value at Risk in USD at configured confidence/horizon")
cvar_usd = Gauge("arbitragex_cvar_usd", "Conditional Value at Risk in USD")
exchange_connection_status = Gauge(
    "arbitragex_exchange_connection_status", "1 = connected, 0 = disconnected", ["exchange"]
)


def start_metrics_server(port: int) -> None:
    start_http_server(port)
