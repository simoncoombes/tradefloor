"""The baseline agent for `.github/workflows/evaluate-selftest.yml`: buy-and-hold."""

import tradefloor as tf


def make_agent():
    return tf.baselines.BuyAndHold()
