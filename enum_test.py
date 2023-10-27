from ranked_choice import SpecialVoteValues

a = SpecialVoteValues.ZERO_VOTE
print(a)

print(SpecialVoteValues.from_string('nil'))
print(SpecialVoteValues.NULL_VOTE.value)
print(SpecialVoteValues(-1))

print(-1 == SpecialVoteValues(-1))
