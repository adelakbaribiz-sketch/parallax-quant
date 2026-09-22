"""Scalar Kalman filter for a time-varying hedge ratio (beta) between two
cointegrated assets, exactly per the state-space model in the spec:

    State:       beta_t = beta_{t-1} + w_t,   w_t ~ N(0, Q)
    Observation: y_t = beta_t * x_t + v_t,    v_t ~ N(0, R)
    Gain:        K_t = P_(t|t-1) * x_t^T * (x_t * P_(t|t-1) * x_t^T + R)^-1

Q (process variance) controls how fast beta is allowed to drift; R
(observation variance) controls how much a single noisy print moves the
estimate. Both are config-driven (stat_arb.kalman_process_variance_q /
kalman_observation_variance_r) rather than hard-coded, since the right
values are market- and pair-specific.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class KalmanHedgeRatio:
    process_variance_q: float
    observation_variance_r: float
    beta: float = 1.0
    p: float = 1.0  # state estimate covariance

    def update(self, x_t: float, y_t: float) -> float:
        """One filter step: observe (x_t, y_t), return the updated beta."""
        # Predict step: beta_(t|t-1) = beta_(t-1), P_(t|t-1) = P_(t-1) + Q
        p_pred = self.p + self.process_variance_q

        # Kalman gain: K_t = P_(t|t-1) * x_t / (x_t^2 * P_(t|t-1) + R)
        denominator = (x_t * x_t * p_pred) + self.observation_variance_r
        if denominator == 0:
            k_t = 0.0
        else:
            k_t = (p_pred * x_t) / denominator

        # Update step
        residual = y_t - (self.beta * x_t)
        self.beta = self.beta + k_t * residual
        self.p = (1 - k_t * x_t) * p_pred
        return self.beta

    def spread(self, x_t: float, y_t: float) -> float:
        """Residual y_t - beta * x_t using the *current* beta (call after
        update() for the filtered spread, or before it to score a new
        print against the prior estimate)."""
        return y_t - (self.beta * x_t)
