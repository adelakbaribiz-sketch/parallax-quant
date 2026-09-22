import pytest

from arbitragex_engine.agents.learning_agent import LearningAgent


def test_bandit_converges_toward_higher_reward_arm():
    agent = LearningAgent(algorithm="bandit", candidate_values=[1.0, 1.5, 2.0], epsilon=0.1, seed=42)

    # Arm 2.0 "wins" 80% of the time, others 20% — simulate many rounds
    import random

    rng = random.Random(1)
    for _ in range(2000):
        value = agent.propose_value()
        if value == 2.0:
            win = rng.random() < 0.8
        else:
            win = rng.random() < 0.2
        agent.record_outcome(win)

    assert agent.best_value_so_far() == 2.0


def test_ppo_algorithm_raises_not_implemented():
    with pytest.raises(NotImplementedError):
        LearningAgent(algorithm="ppo", candidate_values=[1.0])


def test_dqn_algorithm_raises_not_implemented():
    with pytest.raises(NotImplementedError):
        LearningAgent(algorithm="dqn", candidate_values=[1.0])


def test_record_outcome_without_prior_proposal_is_noop():
    agent = LearningAgent(algorithm="bandit", candidate_values=[1.0, 2.0], seed=1)
    agent.record_outcome(True)  # should not raise even though propose_value() was never called
