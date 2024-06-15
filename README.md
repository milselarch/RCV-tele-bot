# Ranked Choice Voting Telegram Bot
Ranked choice voting telegram bot - try it at [@ranked_choice_voting_bot](https://t.me/ranked_choice_voting_bot)   
Bot and webapp backends were written in Python3.10 using the python-telegram-bot bot library, and the peewee SQL ORM. Voting webapp interface integration was written in Typescript using React and [react-telegram-web-app](https://github.com/vkruglikov/react-telegram-web-app) components

![screenshots](https://github.com/milselarch/RCV-tele-bot/assets/11241733/99691922-483d-4fce-a99d-91662b7106b8)

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
4) `/create_group_poll ...`  
   Creates a new poll that chat members can self-register for
   ```
   /create_group_poll @user_1 @user_2 ... @user_n:  
   poll title  
   poll option 1  
   poll option 2
   ...
   poll option m
   ```
5) `/whitelist_chat_registration {poll_id}`  
whitelists the current chat so that chat members can self-register
for the poll specified by poll_id within the chat group
6) `/blacklist_chat_registration {poll_id}`  
whitelists the current chat so that chat members can self-register
for the poll specified by poll_id within the chat group
7) `/view_poll {poll_id}` - Shows poll details given `poll_id`
8) `/vote ...` - Vote for the poll with the specified `poll_id`
   ```
   /vote {poll_id}: {option_1} > {option_2} > ... > {option_n} 
   /vote {poll_id} {option_1} > {option_2} > ... > {option_n} 
   /vote {poll_id} {option_1} {option_2} ... {option_n}
   ```
   requires that the user is one of the registered 
   voters of the poll  
   The last option of the ranked vote can also accept 2 special values, 
   `abstain` and `withhold`:
   - Vote `withhold` to abstain from voting for any option in the poll   
     (In this scenario, no vote will be given to any of the candidates,
     but the voter will still be counted towards the total number of 
     voters needed to achieve a majority in the polling result calculation)
   - Vote `abstain` to effectively exclude yourself from the poll  
     (In this scenario, no vote will be given to any of the candidates, 
     and the voter will no longer be counted towards the total number of 
     voters needed to achieve a majority in the polling result calculation)
9) `/poll_results {poll_id}` - Returns poll results if the poll has been closed
10) `/has_voted {poll_id}` - Tells you if you've voted for the poll with the 
specified poll_id
11) `/close_poll {poll_id}` - Close the poll with the specified poll_id.   
note that only the poll's creator is allowed
to issue this command to close the poll
12) `/view_votes {poll_id}` - View all the votes entered for the poll 
with the specified poll_id. This can only be done after the poll 
has been closed first
13) `/view_voters {poll_id}` - Show which voters have voted and which have not
14) `/about` - View miscellaneous information about the bot
15) `/view_polls` - View all polls created by you
16) `/delete_poll {poll_id}` - Delete poll by poll_id
17) `/help` - View commands available to the bot

Commands for testing and debugging purposes: 
1) `/vote_admin ...` - Casts a vote on behalf of the specified user  
   ```
   /vote_admin @{username} {poll_id}: {option_1} > {option_2} > ... > {option_n}
   ```
2) `/close_poll_admin {poll_id}` - Close a poll
(typically only the poll's author is allowed to do this)
3) `/unclose_poll_admin {poll_id}` - Reopen a poll (typically a poll cannot be reopened)
4) `/lookup_from_username_admin` - Resolve user ID(s) given username
5) `/insert_user_admin` - Insert a user by user_id and optional username 

### Backend Setup
Project was built using `Python3.10`

1. Create a database and database user for the bot program to use as follows:
   ```SQL
   create database ranked_choice_voting;
   create user 'rcv_user'@'localhost' identified by <YOUR_MYSQL_PASSWORD>;
   GRANT create, alter, delete, index, insert, select, update, references ON ranked_choice_voting.* TO 'rcv_user'@'localhost';
   GRANT reload ON *.* TO 'rcv_user'@'localhost';
   ```
2. Create a config.yml file at the project root using config.example.yml as a template,
   and fill it up with MySQL credentials and telegram bot API token
3. Create a new virtual env at the project root and activate it
   ```shell
   $ python3.10 -m venv venv
   $ source venv/bin/activate
   ```
4. Install dependencies and do database initialisation
   ```shell
   (venv) $ python -m pip install -r requirements.txt
   (venv) $ python -m database.py
   ```
5. Install and run Redis cache
    - `sudo apt update`
    - `sudo apt install redis-server -y`
    - `sudo vim /etc/redis/redis.conf`
      - Changed `supervised no` to `supervised systemd`
    - `sudo systemctl restart redis`
    - `sudo systemctl status redis`
    - `sudo systemctl enable --now redis-server`
6. Run the bot
   ```shell
   (venv) $ python bot.py
   ```
7. Run the webapp backend server
    ```shell
   (venv) $ python webapp.py
   ```

### Database Schema
Database ORM definition can be found in `database.py`

![Schema Image](https://github.com/milselarch/RCV-tele-bot/blob/master/schema.png)

### Frontend Setup
Uses node `v20.10.0`   

Installation:  
1. `nvm use v20.10.0`
2. `cd telegram-webapp`
3. `npm install`

Development run instructions:
1. `cd telegram-webapp`
2. `npm run start`

Production build instructions:
1. `firebase init`
2. `npm run build`
3. `firebase deploy`
