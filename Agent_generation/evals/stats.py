"""统计工具：Fleiss' κ（多次采样一致性）、bootstrap 置信区间"""
import random
from typing import List, Sequence


def fleiss_kappa_binary(votes: List[Sequence[int]]) -> float:
    """votes: 每个条目 k 次评审的二值结果（1/0）。返回 Fleiss κ（k≥2）"""
    votes = [v for v in votes if len(v) >= 2]
    if not votes:
        return float("nan")
    k = len(votes[0])
    votes = [v for v in votes if len(v) == k]
    n = len(votes)
    p_yes = sum(sum(v) for v in votes) / (n * k)
    p_no = 1 - p_yes
    pe = p_yes ** 2 + p_no ** 2
    pi = []
    for v in votes:
        y = sum(v)
        nn = k - y
        pi.append((y * (y - 1) + nn * (nn - 1)) / (k * (k - 1)))
    pbar = sum(pi) / n
    if abs(1 - pe) < 1e-12:
        return 1.0
    return (pbar - pe) / (1 - pe)


def unanimity(votes: List[Sequence[int]]) -> float:
    votes = [v for v in votes if v]
    if not votes:
        return float("nan")
    return sum(1 for v in votes if len(set(v)) == 1) / len(votes)


def bootstrap_ci(values: Sequence[float], iters: int = 2000, alpha: float = 0.05, seed: int = 7):
    vals = list(values)
    if not vals:
        return (float("nan"), float("nan"))
    rnd = random.Random(seed)
    means = []
    for _ in range(iters):
        s = [vals[rnd.randrange(len(vals))] for _ in vals]
        means.append(sum(s) / len(s))
    means.sort()
    return means[int(iters * alpha / 2)], means[int(iters * (1 - alpha / 2)) - 1]


def mean(xs):
    xs = [x for x in xs if x == x]
    return sum(xs) / len(xs) if xs else float("nan")
