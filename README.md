# RCV-tele-bot
Ranked choice voting telegram bot - try it at [@ranked_choice_voting_bot](https://t.me/ranked_choice_voting_bot)   
Created using Python3.10, the python-telegram-bot bot library, and the peewee SQL ORM

### Commands
1) `/start` - start bot
2) `/user_details` - Shows your username and user id
3) `/create_poll ...` - Creates a new poll
   ```
   /create_poll @user_1 @user_2 ... @user_n:  
   poll title  
   poll option 1  
   poll option 2
   ...
   poll option m
   ```
4) `/view_poll {poll_id}` - Shows poll details given `poll_id`
5) `/vote ...` - Vote for the poll with the specified `poll_id`
   ```
   /vote {poll_id}: {option_1} > {option_2} > ... > {option_n} 
   /vote {poll_id} {option_1} > {option_2} > ... > {option_n} 
   /vote {poll_id} {option_1} {option_2} ... {option_n}
   ```
   requires that the user is one of the registered 
   voters of the poll  
   The last option of the ranked vote can also accept 2 special values, `0` and `nil`:
   - Vote `0` to abstain from voting for any option in the poll   
     (In this scenario, no vote will be given to any of the candidates,
     but the voter will still be counted towards the total number of 
     voters needed to achieve a majority in the polling result calculation)
   - Vote `nil` to effectively exclude yourself from the poll  
     (In this scenario, no vote will be given to any of the candidates, 
     and the voter will no longer be counted towards the total number of 
     voters needed to achieve a majority in the polling result calculation)
6) `/poll_results {poll_id}` - Returns poll results if the poll has been closed
7) `/has_voted {poll_id}` - Tells you if you've voted for the poll with the 
specified poll_id
8) `/close_poll {poll_id}` - Close the poll with the specified poll_id.   
note that only the poll's creator is allowed
to issue this command to close the poll
9) `/view_votes {poll_id}` - View all the votes entered for the poll 
with the specified poll_id. This can only be done after the poll 
has been closed first
10) `/view_voters {poll_id}` - Show which voters have voted and which have not
11) `/help` - view commands available to the bot

Commands for testing and debugging purposes: 
1) `/vote_admin ...` - Casts a vote on behalf of the specified user  
   ```
   /vote_admin @{username} {poll_id}: {option_1} > {option_2} > ... > {option_n}
   ```
2) `/close_poll_admin {poll_id}` - Close a poll
(typically only the poll's author is allowed to do this)
3) `/unclose_poll_admin {poll_id}` - Reopen a poll (typically a poll cannot be reopened)

### Setup
Project was built using Python3.10

1. Create a database and database user for the bot program to use as follows:
   ```SQL
   create database ranked_choice_voting;
   create user 'rcv_user'@'localhost' identified by <YOUR_MYSQL_PASSWORD>;
   GRANT create, alter, delete, index, insert, select, update, references ON ranked_choice_voting.* TO 'rcv_user'@'localhost';
   GRANT reload ON *.* TO 'rcv_user'@'localhost';
   ```
3. Create a config.yml file at the project root using config.example.yml as a template,
   and fill it up with MySQL credentials and telegram bot API token
4. Create a new virtual env at the project root and activate it
   ```shell
   $ python3.10 -m venv venv
   $ source venv/bin/activate
   ```
5. Install dependencies and do database initialisation
   ```shell
   (venv) $ python -m pip install -r requirements.txt
   (venv) $ python -m database.py
   ```
6. Run the bot
   ```shell
   (venv) $ python bot.py
   ```

### Database Schema
Database ORM definition can be found in `database.py`
![Schema Image](https://github.com/milselarch/RCV-tele-bot/blob/master/schema.png)
