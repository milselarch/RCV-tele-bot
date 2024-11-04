from typing import List, Sequence
from collections import defaultdict


def get_duplicate_nums(nums: Sequence[int]) -> List[int]:
    num_count = defaultdict(int)
    for num in nums:
        num_count[num] += 1

    return [num for num, count in num_count.items() if count > 1]
