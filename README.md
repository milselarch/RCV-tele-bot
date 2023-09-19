# RCV-tele-bot
Ranked choice voting telegram bot - try it at [@ranked_choice_voting_bot](https://t.me/ranked_choice_voting_bot)   
Created using Python3.10, the python-telegram-bot bot library, and the peewee SQL ORM

### Commands
```
/start - start bot
/user_details - Shows your username and user id
——————————————————
/create_poll @user_1 @user_2 ... @user_n:
poll title
poll option 1
poll option 2
...
poll option m
- Creates a new poll
——————————————————
/view_poll {poll_id} - Shows poll details given poll_id
——————————————————
/vote {poll_id}: {option_1} > {option_2} > ... > {option_n} 
- Vote for the poll with the specified poll_id
requires that the user is one of the registered 
voters of the poll
——————————————————
/poll_results {poll_id}
- Returns poll results if the poll has been closed
——————————————————
/has_voted {poll_id} 
- Tells you if you've voted for the poll with the 
specified poll_id
——————————————————
/close_poll {poll_id}
- Close the poll with the specified poll_id
note that only the poll's creator is allowed 
to issue this command to close the poll
——————————————————
/view_votes {poll_id}
- View all the votes entered for the poll 
with the specified poll_id. This can only be done
after the poll has been closed first
——————————————————
/view_voters {poll_id}
- Show which voters have voted and which have not
——————————————————
/help - view commands available to the bot
```

Commands for testing and debugging purposes:   
```
/vote_admin @{username} {poll_id}: {option_1} > {option_2} > ... > {option_n} 
- Casts a vote on behalf of the specified user
——————————————————
/close_poll_admin {poll_id}
- Close a poll (typically only the poll's author is allowed to do this)
——————————————————
/unclose_poll_admin {poll_id} - reopen a poll
- Reopen a poll (typically a poll cannot be reopened)
```

### Setup
Project was built using Python3.10

1. Create a database and database user for the bot program to use as follows:
   ```
   create database ranked_choice_voting;
   create user 'rcv_user'@'localhost' identified by <YOUR_MYSQL_PASSWORD>;
   GRANT create, alter, delete, index, insert, select, update, references ON ranked_choice_voting.* TO 'rcv_user'@'localhost';
   GRANT reload ON *.* TO 'rcv_user'@'localhost';
   ```
3. Create a config.yml file at the project root using config.example.yml as a template,
   and fill it up with MySQL credentials and telegram bot API token
4. Create a new virtual env at the project root and activate it
   ```
   $ python3.10 -m venv venv
   $ source venv/bin/activate
   ```
5. Install dependencies and do database initialisation
   ```
   (venv) $ python -m pip install -r requirements.txt
   (venv) $ python -m database.py
   ```
6. Run the bot
   ```
   (venv) $ python bot.py
   ```

### Database Schema
Database ORM definition can be found in `database.py`
![Schema Image](https://github.com/milselarch/RCV-tele-bot/blob/master/schema.png)
