"""The random source the business rules draw from. Tests override it with a seeded one."""

import random


def get_random() -> random.Random:
    return random.SystemRandom()
