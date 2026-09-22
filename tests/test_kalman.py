import numpy as np

from arbitragex_engine.strategies.kalman import KalmanHedgeRatio


def test_kalman_converges_to_true_static_beta():
    rng = np.random.default_rng(42)
    true_beta = 2.5
    n = 500
    x = rng.normal(100, 5, n)
    noise = rng.normal(0, 0.01, n)
    y = true_beta * x + noise

    kf = KalmanHedgeRatio(process_variance_q=1e-6, observation_variance_r=1e-3, beta=1.0, p=1.0)
    betas = [kf.update(x[i], y[i]) for i in range(n)]

    assert abs(betas[-1] - true_beta) < 0.05
    # Should converge monotonically closer over time, not diverge
    assert abs(betas[-1] - true_beta) < abs(betas[10] - true_beta)


def test_kalman_tracks_slowly_time_varying_beta():
    rng = np.random.default_rng(7)
    n = 1000
    x = rng.normal(50, 2, n)
    true_beta = np.linspace(1.0, 3.0, n)  # linear drift
    y = true_beta * x + rng.normal(0, 0.05, n)

    kf = KalmanHedgeRatio(process_variance_q=1e-4, observation_variance_r=1e-2, beta=1.0, p=1.0)
    betas = [kf.update(x[i], y[i]) for i in range(n)]

    # Late-stage estimate should track the drifted true value reasonably well
    assert abs(betas[-1] - true_beta[-1]) < 0.3


def test_spread_uses_current_beta():
    kf = KalmanHedgeRatio(process_variance_q=1e-5, observation_variance_r=1e-3, beta=2.0, p=1.0)
    assert kf.spread(x_t=10.0, y_t=25.0) == 5.0


def test_zero_x_does_not_crash_gain_computation():
    kf = KalmanHedgeRatio(process_variance_q=1e-5, observation_variance_r=1e-3)
    beta = kf.update(x_t=0.0, y_t=5.0)
    assert beta == kf.beta  # gain is 0 when x_t == 0, beta unchanged
