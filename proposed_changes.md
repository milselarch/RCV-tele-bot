/ Add a users table to map user ids to their usernames   
- < Check if telegram username can fit char field length  
- < update users table whenever any command is received by bot
/ PollVoters table should remove username 
- < update code accordingly
/ PollVoters table should map telegram user ids to option ids
- < update code according;y

/ There should be a UsernameWhitelist table  
- / maps username to poll id, as well as user_id (or none)
- < when a user casts a vote check if username matches and user ID matches
- < only cast if user ID is none or matching for row with same username

/ There should be a ChatWhitelist table
- / maps tele group chat IDs to poll ids
- < When a user registers for a poll check if group id in whitelist table
- < if in whitelist table then go and add user id to VoterWhitelist table

/ There should be a column to keep track of number of voters in poll  
- < find a way to auto update voter count after registration    
/ There should be a column to keep track of number of votes in poll  
- < find a way to auto update votes count after registration

< update schema diagram  
< update schema SQL file  
