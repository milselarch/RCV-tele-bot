from enum import IntEnum


class SubscriptionTiers(IntEnum):
    FREE = 0
    TIER_1 = 1
    TIER_2 = 2

    def get_max_voters(self):
        # returns the maximum number of voters that can join a poll created
        # by a user with the given subscription tier
        match self:
            case SubscriptionTiers.FREE:
                return 20
            case SubscriptionTiers.TIER_1:
                return 50
            case SubscriptionTiers.TIER_2:
                return 200
            case _:
                raise ValueError(f"Invalid SubscriptionTiers value: {self}")

    def get_max_polls(self):
        match self:
            case SubscriptionTiers.FREE:
                return 10
            case SubscriptionTiers.TIER_1:
                return 20
            case SubscriptionTiers.TIER_2:
                return 40
            case _:
                raise ValueError(f"Invalid SubscriptionTiers value: {self}")