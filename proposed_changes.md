Votes table should map telegram user ids to option ids  
(Currently maps internal poll voter ids to option ids)

There should be a VoterNameWhitelist table  
- maps username to poll ids or none
- When a user casts a vote check if username matches and user ID matches
- only only cast if user ID is none or matching

There should be a VoterWhitelist table 
- maps voter telegram user id to poll id

There should be a GroupWhitelist table
- maps tele group chat IDs to poll ids
- When a user registers for a poll check if group id in whitelist table
- if in whitelist table then go and add user id to VoterWhitelist table

There should be a table to keep track of number of voters in poll
There should be a table to keep track of number of votes in poll
