-- Mounted into nexusapi-database-test's entrypoint init directory.
--
-- The mysql image grants the app user rights on MYSQL_DATABASE only, but the
-- Django test runner creates and drops a database of its own (`test_LMS`), so
-- the user needs rights across the server. This container is for the test and
-- benchmark suites and is never exposed outside the compose network.
GRANT ALL PRIVILEGES ON *.* TO 'adminUser'@'%' WITH GRANT OPTION;
FLUSH PRIVILEGES;
