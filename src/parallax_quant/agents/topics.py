"""Message-bus topic names shared across agents — kept in one place so
producer/consumer topic strings can never silently drift apart."""

TOPIC_RAW_OPPORTUNITIES = "opportunities.raw"
TOPIC_EVALUATED_OPPORTUNITIES = "opportunities.evaluated"
TOPIC_APPROVED_OPPORTUNITIES = "opportunities.approved"
TOPIC_REJECTED_OPPORTUNITIES = "opportunities.rejected"
TOPIC_EXECUTIONS = "executions"
TOPIC_RISK_ALERTS = "risk.alerts"
