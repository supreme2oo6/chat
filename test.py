import unittest
import sqlite3
import os
from bot_new import init_db, add_user, get_user, update_user, add_channel, get_channels

class TestBot(unittest.TestCase):
    def setUp(self):
        # Создаём тестовую БД
        self.test_db = 'test_bot.db'
        os.environ['DATABASE_PATH'] = self.test_db
        init_db()

    def tearDown(self):
        # Удаляем тестовую БД
        if os.path.exists(self.test_db):
            os.remove(self.test_db)

    def test_add_user(self):
        add_user(123, 'Test', 'testuser')
        user = get_user(123)
        self.assertIsNotNone(user)
        self.assertEqual(user['first_name'], 'Test')

    def test_update_user(self):
        add_user(123, 'Test', 'testuser')
        update_user(123, gender='male', registered=True)
        user = get_user(123)
        self.assertEqual(user['gender'], 'male')
        self.assertTrue(user['registered'])

    def test_add_channel(self):
        add_channel('@testchannel', 'Test Channel')
        channels = get_channels()
        self.assertEqual(len(channels), 1)
        self.assertEqual(channels[0]['id'], '@testchannel')

if __name__ == '__main__':
    unittest.main()
