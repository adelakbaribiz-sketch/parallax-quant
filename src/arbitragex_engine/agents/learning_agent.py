"""Learning agent — research-stage, off by default
(config.agents.learning_agent_enabled: false). Only the "bandit" algorithm
is actually implemented: an epsilon-greedy multi-armed bandit that nudges
a strategy's entry threshold among a small discrete set of candidates
based on realized win rate.

"ppo" and "dqn" are declared in the config schema (config.py's
AgentsConfig.learning_agent_algorithm) as the intended production
direction, but are NOT implemented here — building a real PPO/DQN training
loop needs a proper Gym-style environment, reward shaping, and offline
validation against historical data before it should touch a threshold
that affects real capital, which is a research project in its own right.
Selecting them raises NotImplementedError rather than a silent no-op.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field


@dataclass
class BanditArm:
    value: float
    trials: int = 0
    wins: float = 0.0  # sum of realized win-rate observations, for a running average

    @property
    def average_reward(self) -> float:
        return self.wins / self.trials if self.trials > 0 else 0.0


@dataclass
class EpsilonGreedyBandit:
    """Standard epsilon-greedy: explore a random arm with probability
    epsilon, otherwise exploit the arm with the highest running average
    reward. `arms` are candidate values for the tuned parameter (e.g. a
    handful of entry_zscore choices around the configured default)."""

    arms: list[BanditArm]
    epsilon: float = 0.1
    _rng: random.Random = field(default_factory=random.Random)

    def select_arm(self) -> BanditArm:
        if self._rng.random() < self.epsilon or all(a.trials == 0 for a in self.arms):
            return self._rng.choice(self.arms)
        return max(self.arms, key=lambda a: a.average_reward)

    def update(self, arm: BanditArm, reward: float) -> None:
        arm.trials += 1
        arm.wins += reward


class LearningAgent:
    """Wraps a bandit per tunable parameter. `propose_value()` /
    `record_outcome()` are the only two calls the orchestrator needs;
    everything else about the bandit's internal state stays private to
    this class."""

    def __init__(self, algorithm: str, candidate_values: list[float], epsilon: float = 0.1, seed: int | None = None) -> None:
        if algorithm != "bandit":
            raise NotImplementedError(
                f"learning_agent_algorithm '{algorithm}' is declared in config.py's schema as a "
                "documented future direction but is not implemented in this build — see "
                "agents/learning_agent.py's module docstring and docs/LIMITATIONS.md. Only "
                "'bandit' is implemented."
            )
        arms = [BanditArm(value=v) for v in candidate_values]
        self.bandit = EpsilonGreedyBandit(arms=arms, epsilon=epsilon, _rng=random.Random(seed))
        self._last_arm: BanditArm | None = None

    def propose_value(self) -> float:
        self._last_arm = self.bandit.select_arm()
        return self._last_arm.value

    def record_outcome(self, win: bool) -> None:
        if self._last_arm is None:
            return
        self.bandit.update(self._last_arm, reward=1.0 if win else 0.0)

    def best_value_so_far(self) -> float:
        return max(self.bandit.arms, key=lambda a: a.average_reward).value
