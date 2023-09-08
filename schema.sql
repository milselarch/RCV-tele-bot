CREATE TABLE `polls` (
  `id` integer PRIMARY KEY,
  `desc` text,
  `close_time` timestamp,
  `closed` bool
);

CREATE TABLE `groups` (
  `id` integer PRIMARY KEY,
  `poll_id` integer,
  `group_id` integer,
  `broadcasted` bool
);

CREATE TABLE `poll_users` (
  `id` integer PRIMARY KEY,
  `poll_id` integer
);

CREATE TABLE `options` (
  `id` integer PRIMARY KEY,
  `poll_id` integer,
  `option_name` varchar(255)
);

CREATE TABLE `votes` (
  `id` integer PRIMARY KEY,
  `poll_id` integer,
  `poll_user_id` integer,
  `option_id` integer,
  `ranking` integer
);

ALTER TABLE `polls` ADD FOREIGN KEY (`id`) REFERENCES `groups` (`poll_id`);

ALTER TABLE `polls` ADD FOREIGN KEY (`id`) REFERENCES `poll_users` (`poll_id`);

ALTER TABLE `polls` ADD FOREIGN KEY (`id`) REFERENCES `options` (`poll_id`);

ALTER TABLE `polls` ADD FOREIGN KEY (`id`) REFERENCES `votes` (`poll_id`);

ALTER TABLE `poll_users` ADD FOREIGN KEY (`id`) REFERENCES `votes` (`poll_user_id`);

ALTER TABLE `options` ADD FOREIGN KEY (`id`) REFERENCES `votes` (`option_id`);
